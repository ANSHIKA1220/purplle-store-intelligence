"""
Event generator — translates raw CV detections into structured store events.

This is the bridge between the detection layer and the Intelligence API.
It takes per-frame detections + tracking state and emits structured events
conforming to the challenge schema.

Event types emitted:
  ENTRY              — visitor crosses entry threshold inbound
  EXIT               — visitor crosses entry threshold outbound
  REENTRY            — visitor seen again after a prior EXIT
  ZONE_ENTER         — visitor enters a named zone
  ZONE_EXIT          — visitor leaves a named zone
  ZONE_DWELL         — visitor has been in zone for 30+ seconds (repeated every 30s)
  BILLING_QUEUE_JOIN — visitor enters billing zone while queue_depth > 0
  BILLING_QUEUE_ABANDON — visitor leaves billing zone without a purchase following
"""

import uuid
import logging
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable

from pipeline.session_manager import VisitorSession, SessionManager

logger = logging.getLogger("pipeline.event_generator")

ZONE_DWELL_INTERVAL_SECONDS = 30
BILLING_ZONE_IDS = {"BILLING", "BILLING_COUNTER", "BILLING_AREA"}

# Staff detection: bounding boxes taller than this ratio of frame height
# are likely staff (standing near camera) — heuristic, not ML-based
STAFF_HEIGHT_RATIO = 0.85


class EventGenerator:
    """
    Emits structured events to a configurable sink (API endpoint or JSONL file).

    Parameters
    ----------
    store_id    : str        — e.g. "STORE_BLR_002"
    camera_id   : str        — e.g. "CAM_ENTRY_01"
    camera_type : str        — "entry" | "floor" | "billing"
    zones       : dict       — zone_id → polygon (list of (x,y) tuples)
    emit_fn     : callable   — receives a dict event; default is print to stdout
    clip_start_time : datetime — real-world start of the clip (for ISO timestamps)
    fps         : float      — frames per second of the source clip
    """

    def __init__(
        self,
        store_id: str,
        camera_id: str,
        camera_type: str,
        zones: Dict[str, Any],
        emit_fn: Optional[Callable[[Dict], None]] = None,
        clip_start_time: Optional[datetime] = None,
        fps: float = 15.0,
    ):
        self.store_id = store_id
        self.camera_id = camera_id
        self.camera_type = camera_type
        self.zones = zones  # zone_id → {"polygon": [...], "sku_zone": "..."}
        self.emit_fn = emit_fn or (lambda e: print(json.dumps(e)))
        self.clip_start_time = clip_start_time or datetime.now(timezone.utc)
        self.fps = fps

        self.session_mgr = SessionManager(store_id, camera_id)

        # track_id → previous_y (for entry/exit line crossing)
        self._prev_y: Dict[int, float] = {}

        # track_id → previous_zone (for zone transition)
        self._prev_zone: Dict[int, Optional[str]] = {}

        # track_id → zone_entry_frame (for dwell calculation)
        self._zone_entry_frame: Dict[int, int] = {}

        # track_id → last_dwell_emit_frame
        self._last_dwell_frame: Dict[int, int] = {}

        # Current billing queue depth (maintained by this generator)
        self._queue_depth: int = 0
        self._billing_visitors: set = set()

    # ─────────────────────────────────────────────────────────────────────────
    # Entry / Exit processing (for entry camera)
    # ─────────────────────────────────────────────────────────────────────────

    def process_entry_frame(
        self,
        frame_number: int,
        detections,
        line_y: int,
        frame_height: int,
    ) -> None:
        """
        Process detections from the entry camera.

        Detects line crossings at line_y.
        Moving downward (y increasing) = ENTRY.
        Moving upward (y decreasing) = EXIT.
        """
        if detections.tracker_id is None or len(detections) == 0:
            return

        timestamp = self._frame_to_ts(frame_number)

        for i, (bbox, track_id, conf) in enumerate(
            zip(detections.xyxy, detections.tracker_id, detections.confidence)
        ):
            x1, y1, x2, y2 = map(float, bbox)
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2

            is_staff = self._is_staff_heuristic(y1, y2, frame_height)
            visitor_id = self.session_mgr.get_visitor_id(int(track_id))

            prev_y = self._prev_y.get(track_id)

            if prev_y is not None:
                crossed_down = prev_y < line_y <= cy
                crossed_up = prev_y > line_y >= cy

                if crossed_down:
                    # Check for re-entry
                    if self.session_mgr.is_reentry(int(track_id)):
                        self._emit_event(
                            "REENTRY", visitor_id, timestamp, conf,
                            is_staff=is_staff,
                            metadata={"session_seq": 1},
                        )
                    else:
                        self.session_mgr.get_or_create(int(track_id), is_staff)
                        self._emit_event(
                            "ENTRY", visitor_id, timestamp, conf,
                            is_staff=is_staff,
                            metadata={"session_seq": 1},
                        )

                elif crossed_up:
                    self.session_mgr.close_session(visitor_id)
                    self._emit_event(
                        "EXIT", visitor_id, timestamp, conf,
                        is_staff=is_staff,
                        metadata={"session_seq": 99},
                    )

            self._prev_y[int(track_id)] = cy

    # ─────────────────────────────────────────────────────────────────────────
    # Zone processing (for floor and billing cameras)
    # ─────────────────────────────────────────────────────────────────────────

    def process_zone_frame(
        self,
        frame_number: int,
        detections,
        frame_height: int,
    ) -> None:
        """
        Process detections from a zone/floor camera.

        Detects zone transitions and emits ZONE_ENTER, ZONE_EXIT, ZONE_DWELL,
        BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON events.
        """
        if detections.tracker_id is None or len(detections) == 0:
            return

        timestamp = self._frame_to_ts(frame_number)
        active_track_ids = set(int(tid) for tid in detections.tracker_id)

        # Handle visitors who just left the frame (possible ZONE_EXIT / BILLING_ABANDON)
        self._handle_disappeared_tracks(active_track_ids, frame_number, timestamp)

        for i, (bbox, track_id, conf) in enumerate(
            zip(detections.xyxy, detections.tracker_id, detections.confidence)
        ):
            x1, y1, x2, y2 = map(float, bbox)
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2
            track_id = int(track_id)

            is_staff = self._is_staff_heuristic(y1, y2, frame_height)
            visitor_id = self.session_mgr.get_visitor_id(track_id)

            current_zone = self._get_zone(cx, cy)
            prev_zone = self._prev_zone.get(track_id)

            session = self.session_mgr.get_or_create(track_id, is_staff)
            seq = session.next_seq()

            # Zone transition
            if prev_zone != current_zone:
                # ZONE_EXIT from previous zone
                if prev_zone is not None:
                    dwell_ms = self._calc_dwell_ms(track_id, frame_number)
                    self._emit_event(
                        "ZONE_EXIT", visitor_id, timestamp, conf,
                        zone_id=prev_zone,
                        zone_name=self._zone_name(prev_zone),
                        dwell_ms=dwell_ms,
                        is_staff=is_staff,
                        metadata={"session_seq": seq, "sku_zone": self._sku_zone(prev_zone)},
                    )

                    # Billing queue abandon check
                    if prev_zone in BILLING_ZONE_IDS and visitor_id in self._billing_visitors:
                        self._billing_visitors.discard(visitor_id)
                        self._queue_depth = max(0, self._queue_depth - 1)
                        self._emit_event(
                            "BILLING_QUEUE_ABANDON", visitor_id, timestamp, conf,
                            zone_id=prev_zone,
                            zone_name=self._zone_name(prev_zone),
                            is_staff=is_staff,
                            metadata={"session_seq": seq, "queue_depth": self._queue_depth},
                        )

                # ZONE_ENTER to new zone
                if current_zone is not None:
                    self._zone_entry_frame[track_id] = frame_number
                    self._last_dwell_frame[track_id] = frame_number

                    # Billing queue join
                    if current_zone in BILLING_ZONE_IDS:
                        self._queue_depth += 1
                        self._billing_visitors.add(visitor_id)
                        self._emit_event(
                            "BILLING_QUEUE_JOIN", visitor_id, timestamp, conf,
                            zone_id=current_zone,
                            zone_name=self._zone_name(current_zone),
                            is_staff=is_staff,
                            metadata={
                                "session_seq": seq,
                                "queue_depth": self._queue_depth,
                                "sku_zone": "CHECKOUT",
                            },
                        )
                    else:
                        self._emit_event(
                            "ZONE_ENTER", visitor_id, timestamp, conf,
                            zone_id=current_zone,
                            zone_name=self._zone_name(current_zone),
                            is_staff=is_staff,
                            metadata={
                                "session_seq": seq,
                                "sku_zone": self._sku_zone(current_zone),
                            },
                        )

                self._prev_zone[track_id] = current_zone

            else:
                # Same zone — check for ZONE_DWELL (emit every 30s)
                if current_zone is not None:
                    last_dwell = self._last_dwell_frame.get(track_id, frame_number)
                    frames_since_dwell = frame_number - last_dwell
                    dwell_interval_frames = int(ZONE_DWELL_INTERVAL_SECONDS * self.fps)

                    if frames_since_dwell >= dwell_interval_frames:
                        total_dwell_ms = self._calc_dwell_ms(track_id, frame_number)
                        self._emit_event(
                            "ZONE_DWELL", visitor_id, timestamp, conf,
                            zone_id=current_zone,
                            zone_name=self._zone_name(current_zone),
                            dwell_ms=total_dwell_ms,
                            is_staff=is_staff,
                            metadata={
                                "session_seq": seq,
                                "sku_zone": self._sku_zone(current_zone),
                            },
                        )
                        self._last_dwell_frame[track_id] = frame_number

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _emit_event(
        self,
        event_type: str,
        visitor_id: str,
        timestamp: datetime,
        confidence: float,
        zone_id: Optional[str] = None,
        zone_name: Optional[str] = None,
        dwell_ms: int = 0,
        is_staff: bool = False,
        metadata: Optional[Dict] = None,
    ) -> None:
        event = {
            "event_id": str(uuid.uuid4()),
            "store_id": self.store_id,
            "camera_id": self.camera_id,
            "visitor_id": visitor_id,
            "event_type": event_type,
            "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "zone_id": zone_id,
            "zone_name": zone_name,
            "dwell_ms": dwell_ms,
            "is_staff": is_staff,
            "confidence": round(float(confidence), 4),
            "metadata": metadata or {},
        }
        self.emit_fn(event)

    def _frame_to_ts(self, frame_number: int) -> datetime:
        """Convert frame number to ISO UTC timestamp."""
        offset_seconds = frame_number / self.fps
        return self.clip_start_time + timedelta(seconds=offset_seconds)

    def _get_zone(self, cx: float, cy: float) -> Optional[str]:
        """Point-in-polygon test against all defined zones."""
        import cv2
        import numpy as np
        for zone_id, zone_info in self.zones.items():
            polygon = np.array(zone_info["polygon"], dtype=np.int32)
            if cv2.pointPolygonTest(polygon, (cx, cy), False) >= 0:
                return zone_id
        return None

    def _zone_name(self, zone_id: Optional[str]) -> Optional[str]:
        if zone_id is None:
            return None
        info = self.zones.get(zone_id, {})
        return info.get("name", zone_id)

    def _sku_zone(self, zone_id: Optional[str]) -> Optional[str]:
        if zone_id is None:
            return None
        info = self.zones.get(zone_id, {})
        return info.get("sku_zone")

    def _calc_dwell_ms(self, track_id: int, current_frame: int) -> int:
        entry_frame = self._zone_entry_frame.get(track_id, current_frame)
        return int((current_frame - entry_frame) / self.fps * 1000)

    def _is_staff_heuristic(self, y1: float, y2: float, frame_height: int) -> bool:
        """
        Heuristic: staff tend to occupy a larger vertical portion of the frame
        (they stand upright close to the camera vs. customers further away).
        This is a fallback — a proper classifier would use uniform detection.
        """
        if frame_height == 0:
            return False
        bbox_height_ratio = (y2 - y1) / frame_height
        return bbox_height_ratio > STAFF_HEIGHT_RATIO

    def _handle_disappeared_tracks(
        self,
        active_track_ids: set,
        frame_number: int,
        timestamp: datetime,
    ) -> None:
        """
        Tracks that were seen previously but are no longer active in this frame
        may have exited — clean up their zone state.
        """
        gone_tracks = set(self._prev_zone.keys()) - active_track_ids
        for track_id in gone_tracks:
            zone = self._prev_zone.pop(track_id, None)
            if zone is not None:
                visitor_id = self.session_mgr.get_visitor_id(track_id)
                dwell_ms = self._calc_dwell_ms(track_id, frame_number)

                # If they were in billing and left without purchase → abandon
                if zone in BILLING_ZONE_IDS and visitor_id in self._billing_visitors:
                    self._billing_visitors.discard(visitor_id)
                    self._queue_depth = max(0, self._queue_depth - 1)
                    self._emit_event(
                        "BILLING_QUEUE_ABANDON", visitor_id, timestamp, 0.5,
                        zone_id=zone,
                        zone_name=self._zone_name(zone),
                        dwell_ms=dwell_ms,
                        metadata={"queue_depth": self._queue_depth},
                    )

            # Clean up frame tracking
            self._zone_entry_frame.pop(track_id, None)
            self._last_dwell_frame.pop(track_id, None)
