"""
Detector — wraps YOLOv8 + ByteTrack for person detection and tracking.

Responsibilities:
  - Load YOLO model once
  - Run inference on a frame
  - Filter to persons only (class_id == 0)
  - Update ByteTrack with detections
  - Return (detections, tracker) for downstream use
"""

import logging
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import supervision as sv
from ultralytics import YOLO
from ultralytics.engine.results import Results

logger = logging.getLogger("pipeline.detector")

# Model path — try local first, then let ultralytics download
MODEL_PATH = Path(__file__).parent.parent / "yolov8n.pt"
FALLBACK_MODEL = "yolov8n.pt"

# Confidence threshold for detections
DETECTION_CONFIDENCE = 0.25

# ByteTrack parameters tuned for retail (15–30fps footage)
BYTETRACK_CONFIG = {
    "track_activation_threshold": 0.20,
    "lost_track_buffer": 90,       # 3s at 30fps — handles brief occlusions
    "minimum_matching_threshold": 0.65,
    "frame_rate": 30,
}


class PersonDetector:
    """
    Wraps YOLO + ByteTrack.

    Usage:
        detector = PersonDetector()
        for frame in video_frames:
            detections = detector.detect(frame)
            # detections.tracker_id, detections.xyxy, detections.confidence
    """

    def __init__(self, model_path: Optional[str] = None):
        path = model_path or (str(MODEL_PATH) if MODEL_PATH.exists() else FALLBACK_MODEL)
        logger.info("Loading YOLO model from %s", path)
        self.model = YOLO(path)

        self.tracker = sv.ByteTrack(**BYTETRACK_CONFIG)
        logger.info("ByteTrack tracker initialised")

    def detect(self, frame: np.ndarray) -> sv.Detections:
        """
        Run YOLO inference + ByteTrack on a single frame.

        Returns sv.Detections with tracker_id populated.
        Low-confidence detections are included (flagged in confidence field)
        rather than silently dropped — the event generator decides what to emit.
        """
        results: Results = self.model(
            frame,
            verbose=False,
            conf=DETECTION_CONFIDENCE,
        )[0]

        detections = sv.Detections.from_ultralytics(results)

        # Keep only persons (COCO class 0)
        if len(detections) > 0:
            person_mask = detections.class_id == 0
            detections = detections[person_mask]

        # Update tracker — ByteTrack handles partial occlusions and re-ID
        if len(detections) > 0:
            detections = self.tracker.update_with_detections(detections)

        return detections

    def reset_tracker(self):
        """Reset tracker state — call between videos."""
        self.tracker = sv.ByteTrack(**BYTETRACK_CONFIG)
        logger.debug("Tracker state reset")
