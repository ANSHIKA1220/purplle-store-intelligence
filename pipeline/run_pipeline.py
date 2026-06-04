"""
run_pipeline.py — Master pipeline runner.

Processes all CCTV clips for a store, emits structured events to the
Intelligence API via POST /events/ingest.

Usage:
    python -m pipeline.run_pipeline --store STORE_BLR_002 --api http://localhost:8000
    python -m pipeline.run_pipeline --store STORE_BLR_002 --output pipeline/outputs/events.jsonl
    python -m pipeline.run_pipeline --help

The pipeline:
  1. Loads store layout (zone definitions) from data/store_layout.json
  2. For each camera clip (entry → floor → billing order):
     a. Runs YOLOv8 + ByteTrack person detection
     b. Emits structured events via EventGenerator
     c. Batches events and POSTs to /events/ingest (or writes to JSONL)
  3. Prints a summary of events emitted
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

import cv2
import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.detector import PersonDetector
from pipeline.event_generator import EventGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("pipeline.run_pipeline")

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

LAYOUT_PATH = ROOT / "data" / "store_layout.json"
INPUTS_BASE = ROOT / "pipeline" / "inputs"
OUTPUTS_BASE = ROOT / "pipeline" / "outputs"

INGEST_BATCH_SIZE = 100     # POST every N events
API_TIMEOUT_SECONDS = 10

# Default store clip structure (camera_type → filename pattern)
CAMERA_FILENAME_PATTERNS = {
    "entry": ["entry", "entry 1", "entry 2", "CAM 3 - entry"],
    "floor": ["zone", "CAM 1 - zone", "CAM 2 - zone"],
    "billing": ["billing", "billing_area", "CAM 5 - billing"],
}

# Entry line Y position as fraction of frame height
ENTRY_LINE_FRACTION = 0.60


# ─────────────────────────────────────────────────────────────────────────────
# Layout loading
# ─────────────────────────────────────────────────────────────────────────────

def load_store_layout(store_id: str) -> Dict[str, Any]:
    """Load zone definitions for a store from store_layout.json."""
    with open(LAYOUT_PATH) as f:
        layout = json.load(f)

    for store in layout["stores"]:
        if store["store_id"] == store_id:
            # Build zone dict: zone_id → {polygon, sku_zone, name}
            zones = {}
            for z in store["zones"]:
                zones[z["zone_id"]] = {
                    "polygon": z["polygon"],
                    "name": z["zone_name"],
                    "sku_zone": z.get("sku_zone"),
                    "type": z.get("type"),
                }
            return {
                "store_id": store["store_id"],
                "zones": zones,
                "cameras": {c["camera_id"]: c for c in store["cameras"]},
            }

    raise ValueError(f"Store '{store_id}' not found in {LAYOUT_PATH}")


# ─────────────────────────────────────────────────────────────────────────────
# Clip discovery
# ─────────────────────────────────────────────────────────────────────────────

def find_clips(store_id: str) -> Dict[str, Path]:
    """
    Find video clips for a store by scanning the inputs directory.
    Returns {camera_type: path} for each discovered clip.
    """
    # Try exact store directory first
    store_dirs = [
        INPUTS_BASE / store_id,
        INPUTS_BASE / "Store 1",
        INPUTS_BASE / "Store 2",
    ]

    found: Dict[str, Path] = {}

    for store_dir in store_dirs:
        if not store_dir.exists():
            continue

        for cam_type, patterns in CAMERA_FILENAME_PATTERNS.items():
            if cam_type in found:
                continue
            for mp4 in store_dir.glob("*.mp4"):
                stem_lower = mp4.stem.lower()
                for pat in patterns:
                    if pat.lower() in stem_lower:
                        found[cam_type] = mp4
                        logger.info("Found %s clip: %s", cam_type, mp4)
                        break

    return found


# ─────────────────────────────────────────────────────────────────────────────
# Event sink factories
# ─────────────────────────────────────────────────────────────────────────────

def make_api_sink(api_base: str) -> tuple:
    """Returns (emit_fn, flush_fn) that batch-POST to /events/ingest."""
    buffer: List[Dict] = []

    def emit(event: Dict) -> None:
        buffer.append(event)
        if len(buffer) >= INGEST_BATCH_SIZE:
            flush()

    def flush() -> None:
        if not buffer:
            return
        batch = list(buffer)
        buffer.clear()
        url = f"{api_base.rstrip('/')}/events/ingest"
        try:
            resp = requests.post(url, json=batch, timeout=API_TIMEOUT_SECONDS)
            if resp.ok:
                data = resp.json()
                logger.info(
                    "Ingested batch: ingested=%d duplicates=%d",
                    data.get("ingested", 0),
                    data.get("duplicates", 0),
                )
            else:
                logger.warning("Ingest returned %d: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.error("Ingest request failed: %s", exc)

    return emit, flush


def make_jsonl_sink(output_path: Path) -> tuple:
    """Returns (emit_fn, flush_fn) that write events to a JSONL file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(output_path, "w", encoding="utf-8")
    count = [0]

    def emit(event: Dict) -> None:
        fh.write(json.dumps(event) + "\n")
        count[0] += 1
        if count[0] % 100 == 0:
            fh.flush()
            logger.info("Events written: %d", count[0])

    def flush() -> None:
        fh.flush()

    return emit, flush


# ─────────────────────────────────────────────────────────────────────────────
# Per-clip processing
# ─────────────────────────────────────────────────────────────────────────────

def process_clip(
    clip_path: Path,
    camera_type: str,
    camera_id: str,
    store_id: str,
    zones: Dict,
    emit_fn,
    clip_start_time: Optional[datetime] = None,
    headless: bool = True,
) -> int:
    """
    Process one video clip and emit events.

    Returns the number of frames processed.
    """
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        logger.error("Cannot open clip: %s", clip_path)
        return 0

    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    line_y = int(frame_h * ENTRY_LINE_FRACTION)

    logger.info(
        "Processing %s | camera=%s type=%s fps=%.1f frames=%d",
        clip_path.name, camera_id, camera_type, fps, total_frames,
    )

    if clip_start_time is None:
        clip_start_time = datetime.now(timezone.utc)

    generator = EventGenerator(
        store_id=store_id,
        camera_id=camera_id,
        camera_type=camera_type,
        zones=zones,
        emit_fn=emit_fn,
        clip_start_time=clip_start_time,
        fps=fps,
    )

    detector = PersonDetector()
    frame_number = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_number += 1

        detections = detector.detect(frame)

        if camera_type == "entry":
            generator.process_entry_frame(
                frame_number, detections, line_y, frame_h
            )
        else:
            generator.process_zone_frame(
                frame_number, detections, frame_h
            )

        if not headless:
            _draw_debug(frame, detections, camera_type, line_y, generator)
            cv2.imshow(f"{camera_type} — {clip_path.name}", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        if frame_number % 500 == 0:
            logger.info("  Frame %d / %d (%.1f%%)", frame_number, total_frames,
                        frame_number / total_frames * 100 if total_frames else 0)

    cap.release()
    if not headless:
        cv2.destroyAllWindows()

    logger.info("Clip complete: %d frames processed", frame_number)
    return frame_number


# ─────────────────────────────────────────────────────────────────────────────
# Debug overlay
# ─────────────────────────────────────────────────────────────────────────────

def _draw_debug(frame, detections, camera_type, line_y, generator):
    import numpy as np
    h, w = frame.shape[:2]

    if camera_type == "entry":
        cv2.line(frame, (0, line_y), (w, line_y), (0, 0, 255), 2)

    if detections.tracker_id is not None:
        for bbox, track_id in zip(detections.xyxy, detections.tracker_id):
            x1, y1, x2, y2 = map(int, bbox)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(
                frame, f"ID {track_id}",
                (x1, y1 - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def run(
    store_id: str,
    api_base: Optional[str],
    output_path: Optional[Path],
    headless: bool = True,
) -> None:
    layout = load_store_layout(store_id)
    zones = layout["zones"]
    clips = find_clips(store_id)

    if not clips:
        logger.error("No clips found for store %s in %s", store_id, INPUTS_BASE)
        sys.exit(1)

    if api_base:
        emit_fn, flush_fn = make_api_sink(api_base)
        logger.info("Events will be posted to %s/events/ingest", api_base)
    else:
        out = output_path or (OUTPUTS_BASE / f"{store_id}_events.jsonl")
        emit_fn, flush_fn = make_jsonl_sink(out)
        logger.info("Events will be written to %s", out)

    clip_start = datetime.now(timezone.utc)
    total_frames = 0

    # Process entry clip first, then floor, then billing
    for cam_type in ["entry", "floor", "billing"]:
        clip = clips.get(cam_type)
        if not clip:
            logger.warning("No %s clip found for store %s — skipping", cam_type, store_id)
            continue

        # Pick camera_id from layout if available
        cam_id_map = {"entry": "CAM_ENTRY_01", "floor": "CAM_FLOOR_01", "billing": "CAM_BILLING_01"}
        camera_id = cam_id_map.get(cam_type, f"CAM_{cam_type.upper()}_01")

        frames = process_clip(
            clip_path=clip,
            camera_type=cam_type,
            camera_id=camera_id,
            store_id=store_id,
            zones=zones,
            emit_fn=emit_fn,
            clip_start_time=clip_start,
            headless=headless,
        )
        total_frames += frames

    flush_fn()
    logger.info("Pipeline complete. Total frames processed: %d", total_frames)


def main():
    parser = argparse.ArgumentParser(description="Store Intelligence Pipeline")
    parser.add_argument("--store", default="STORE_BLR_002", help="Store ID from store_layout.json")
    parser.add_argument("--api", default=None, help="API base URL, e.g. http://localhost:8000")
    parser.add_argument("--output", default=None, help="Output JSONL file path")
    parser.add_argument("--display", action="store_true", help="Show OpenCV debug windows")
    args = parser.parse_args()

    if not args.api and not args.output:
        # Default: write to JSONL
        args.output = str(OUTPUTS_BASE / f"{args.store}_events.jsonl")

    run(
        store_id=args.store,
        api_base=args.api,
        output_path=Path(args.output) if args.output else None,
        headless=not args.display,
    )


if __name__ == "__main__":
    main()
