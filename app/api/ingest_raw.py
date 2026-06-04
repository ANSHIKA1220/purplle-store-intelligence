"""
/events/ingest/raw — accepts the real sample_events.jsonl format and
translates it into the internal canonical schema before storing.

The real event format (from sample_events.jsonl) has two distinct shapes:

Entry/Exit events:
  {
    "event_type": "entry" | "exit",
    "id_token": "ID_60001",
    "store_code": "store_1076",
    "camera_id": "cam1",
    "event_timestamp": "2026-03-08T18:10:05.120000",
    "is_staff": false,
    "gender_pred": "F",
    "age_pred": 28,
    "age_bucket": "25-34",
    "is_face_hidden": false,
    "group_id": null,
    "group_size": null
  }

Zone events:
  {
    "event_type": "zone_entered" | "zone_exited",
    "track_id": 101,
    "store_id": "ST1076",
    "camera_id": "CAM2",
    "zone_id": "PURPLLE_MUM_1076_Z01",
    "zone_name": "Left Shelf",
    "zone_type": "SHELF",
    "event_time": "2026-03-08T18:10:45.280000",
    "gender": "F",
    "age": 28,
    "age_bucket": "25-34"
  }

Queue events:
  {
    "queue_event_id": "cfd8e3c5-...",
    "event_type": "queue_completed" | "queue_abandoned",
    "track_id": 102,
    "store_id": "ST1076",
    "camera_id": "PURPLLE_MUM_1076_CAM6",
    "zone_id": "PURPLLE_MUM_1076_Z_BILLING_01",
    "zone_name": "Billing Counter Queue",
    "queue_join_ts": "...",
    "queue_served_ts": "...",
    "queue_exit_ts": "...",
    "wait_seconds": 8,
    "queue_position_at_join": 2,
    "abandoned": false,
    "gender": "M",
    "age": 31
  }
"""

import uuid
import logging
from typing import List, Any, Dict, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db_event import EventDB
from app.services.session_service import process_event
from app.models.event import Event
from app.api.ingest import _compute_dwell_ms

logger = logging.getLogger("store_intelligence.ingest_raw")

router = APIRouter(tags=["Events"])


# ─────────────────────────────────────────────────────────────────────────────
# Store ID normalisation
# Maps both "store_1076" and "ST1076" → "ST1076"
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_store_id(raw: str) -> str:
    """
    Normalise any store ID format to canonical 'ST{number}' form.
      store_1076  → ST1076
      ST1076      → ST1076
      STORE_BLR_002 → STORE_BLR_002  (already canonical, pass through)
    """
    if raw is None:
        return "UNKNOWN"
    raw = str(raw).strip()
    # "store_1076" → "ST1076"
    if raw.lower().startswith("store_") and raw[6:].isdigit():
        return f"ST{raw[6:]}"
    return raw


# ─────────────────────────────────────────────────────────────────────────────
# Event type mapping
# ─────────────────────────────────────────────────────────────────────────────

_EVENT_TYPE_MAP = {
    "entry":            "ENTRY",
    "exit":             "EXIT",
    "zone_entered":     "ZONE_ENTER",
    "zone_exited":      "ZONE_EXIT",
    "queue_completed":  "BILLING_QUEUE_JOIN",
    "queue_abandoned":  "BILLING_QUEUE_ABANDON",
    "reentry":          "REENTRY",
    "zone_dwell":       "ZONE_DWELL",
}


def _map_event_type(raw: str) -> Optional[str]:
    return _EVENT_TYPE_MAP.get(raw.lower().strip())


# ─────────────────────────────────────────────────────────────────────────────
# Timestamp parsing (handles both ISO formats in the sample data)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_ts(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        # Remove trailing Z or microseconds inconsistencies
        val = str(val).replace("Z", "+00:00")
        return datetime.fromisoformat(val).replace(tzinfo=timezone.utc)
    except Exception:
        try:
            return datetime.strptime(val[:26], "%Y-%m-%dT%H:%M:%S.%f").replace(
                tzinfo=timezone.utc
            )
        except Exception:
            return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Visitor ID resolution
# Entry/exit events use id_token; zone/queue events use track_id.
# We need a consistent visitor_id across both.
# ─────────────────────────────────────────────────────────────────────────────

def _visitor_id(raw: Dict) -> str:
    # Entry/exit: id_token is already a good unique key
    id_token = raw.get("id_token")
    if id_token:
        return str(id_token)
    # Zone/queue: use track_id prefixed
    track_id = raw.get("track_id")
    if track_id is not None:
        return f"VIS_{int(track_id):06d}"
    return f"VIS_{uuid.uuid4().hex[:8]}"


# ─────────────────────────────────────────────────────────────────────────────
# Per-event-type translators
# ─────────────────────────────────────────────────────────────────────────────

def _translate_entry_exit(raw: Dict) -> Optional[Dict]:
    """Translate entry/exit raw event → internal schema dict."""
    et = _map_event_type(raw.get("event_type", ""))
    if not et:
        return None

    store_raw = raw.get("store_code") or raw.get("store_id") or "UNKNOWN"
    store_id = _normalise_store_id(store_raw)

    ts = _parse_ts(raw.get("event_timestamp") or raw.get("event_time"))

    return {
        "event_id": str(uuid.uuid4()),
        "visitor_id": _visitor_id(raw),
        "store_id": store_id,
        "camera_id": str(raw.get("camera_id", "CAM_UNKNOWN")),
        "event_type": et,
        "timestamp": ts,
        "zone_id": None,
        "zone_name": None,
        "track_id": str(raw.get("track_id")) if raw.get("track_id") else None,
        "dwell_ms": 0,
        "is_staff": bool(raw.get("is_staff", False)),
        "confidence": None,
        "metadata": {
            "gender_pred": raw.get("gender_pred") or raw.get("gender"),
            "age_pred": raw.get("age_pred") or raw.get("age"),
            "age_bucket": raw.get("age_bucket"),
            "group_id": raw.get("group_id"),
            "group_size": raw.get("group_size"),
            "is_face_hidden": raw.get("is_face_hidden"),
        },
    }


def _translate_zone(raw: Dict) -> Optional[Dict]:
    """Translate zone_entered/zone_exited → internal schema dict."""
    et = _map_event_type(raw.get("event_type", ""))
    if not et:
        return None

    store_raw = raw.get("store_id") or raw.get("store_code") or "UNKNOWN"
    store_id = _normalise_store_id(store_raw)

    ts = _parse_ts(raw.get("event_time") or raw.get("event_timestamp"))

    return {
        "event_id": str(uuid.uuid4()),
        "visitor_id": _visitor_id(raw),
        "store_id": store_id,
        "camera_id": str(raw.get("camera_id", "CAM_UNKNOWN")),
        "event_type": et,
        "timestamp": ts,
        "zone_id": raw.get("zone_id"),
        "zone_name": raw.get("zone_name"),
        "track_id": str(raw.get("track_id")) if raw.get("track_id") else None,
        "dwell_ms": 0,
        "is_staff": bool(raw.get("is_staff", False)),
        "confidence": None,
        "metadata": {
            "gender": raw.get("gender") or raw.get("gender_pred"),
            "age": raw.get("age") or raw.get("age_pred"),
            "age_bucket": raw.get("age_bucket"),
            "zone_type": raw.get("zone_type"),
            "is_revenue_zone": raw.get("is_revenue_zone"),
            "zone_hotspot_x": raw.get("zone_hotspot_x"),
            "zone_hotspot_y": raw.get("zone_hotspot_y"),
        },
    }


def _translate_queue(raw: Dict) -> Optional[Dict]:
    """Translate queue_completed/queue_abandoned → internal schema dict."""
    et = _map_event_type(raw.get("event_type", ""))
    if not et:
        return None

    store_raw = raw.get("store_id") or raw.get("store_code") or "UNKNOWN"
    store_id = _normalise_store_id(store_raw)

    # Use queue_join_ts as the canonical event timestamp
    ts = _parse_ts(
        raw.get("queue_join_ts")
        or raw.get("event_time")
        or raw.get("event_timestamp")
    )

    # Compute dwell_ms from wait_seconds if available
    wait_seconds = raw.get("wait_seconds") or 0
    dwell_ms = int(wait_seconds * 1000)

    # Determine queue depth from position at join
    queue_depth = raw.get("queue_position_at_join") or 0

    return {
        "event_id": raw.get("queue_event_id") or str(uuid.uuid4()),
        "visitor_id": _visitor_id(raw),
        "store_id": store_id,
        "camera_id": str(raw.get("camera_id", "CAM_BILLING")),
        "event_type": et,
        "timestamp": ts,
        "zone_id": raw.get("zone_id"),
        "zone_name": raw.get("zone_name"),
        "track_id": str(raw.get("track_id")) if raw.get("track_id") else None,
        "dwell_ms": dwell_ms,
        "is_staff": False,
        "confidence": None,
        "metadata": {
            "queue_depth": queue_depth,
            "wait_seconds": wait_seconds,
            "abandoned": raw.get("abandoned", False),
            "queue_join_ts": raw.get("queue_join_ts"),
            "queue_served_ts": raw.get("queue_served_ts"),
            "queue_exit_ts": raw.get("queue_exit_ts"),
            "gender": raw.get("gender"),
            "age": raw.get("age"),
            "age_bucket": raw.get("age_bucket"),
            "zone_type": raw.get("zone_type"),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main translator dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def translate_raw_event(raw: Dict) -> Optional[Dict]:
    """
    Translate any raw event format → internal schema dict.
    Returns None if the event type is unknown.
    """
    et = str(raw.get("event_type", "")).lower().strip()

    if et in ("entry", "exit", "reentry"):
        return _translate_entry_exit(raw)
    elif et in ("zone_entered", "zone_exited", "zone_dwell"):
        return _translate_zone(raw)
    elif et in ("queue_completed", "queue_abandoned"):
        return _translate_queue(raw)
    else:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/events/ingest/raw", status_code=200)
def ingest_raw_events(
    request: Request,
    events: List[Dict[str, Any]],
    db: Session = Depends(get_db),
):
    """
    Ingest a batch of events in the raw sample_events.jsonl format.

    Accepts the real pipeline output schema (id_token, store_code,
    zone_entered, queue_completed, etc.) and translates to internal schema
    before persisting.

    Idempotent: queue events use their queue_event_id; entry/exit/zone
    events get a generated UUID (not idempotent across calls for those types —
    use /events/ingest with pre-translated events for full idempotency).
    """
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))

    if len(events) > 500:
        return {"error": "BATCH_TOO_LARGE", "trace_id": trace_id}

    ingested = 0
    duplicates = 0
    rejected = []

    for raw in events:
        translated = translate_raw_event(raw)
        if translated is None:
            rejected.append({
                "raw_event_type": raw.get("event_type"),
                "reason": f"Unknown or unsupported event_type: '{raw.get('event_type')}'",
            })
            continue

        # Idempotency: queue events have stable IDs; others will re-ingest
        existing = (
            db.query(EventDB)
            .filter(EventDB.event_id == translated["event_id"])
            .first()
        )
        if existing:
            duplicates += 1
            continue

        ts = translated["timestamp"]
        if ts is None:
            ts = datetime.now(timezone.utc)

        # Compute dwell for ZONE_EXIT if not already set
        dwell_ms = translated.get("dwell_ms", 0) or 0
        if translated["event_type"] == "ZONE_EXIT" and dwell_ms == 0:
            # Build a minimal event-like object to reuse _compute_dwell_ms
            class _FakeEvent:
                visitor_id = translated["visitor_id"]
                store_id = translated["store_id"]
                zone_id = translated.get("zone_id")
                timestamp = ts
            dwell_ms = _compute_dwell_ms(_FakeEvent(), db)

        db_event = EventDB(
            event_id=translated["event_id"],
            visitor_id=translated["visitor_id"],
            store_id=translated["store_id"],
            camera_id=translated["camera_id"],
            event_type=translated["event_type"],
            timestamp=ts.replace(tzinfo=None),  # store as naive UTC
            zone_id=translated.get("zone_id"),
            zone_name=translated.get("zone_name"),
            track_id=translated.get("track_id"),
            dwell_ms=dwell_ms,
            is_staff=translated.get("is_staff", False),
            confidence=translated.get("confidence"),
            metadata_json=translated.get("metadata", {}),
        )
        db.add(db_event)

        # Build a minimal Event object for session_service
        event_obj = Event(
            event_id=translated["event_id"],
            visitor_id=translated["visitor_id"],
            store_id=translated["store_id"],
            camera_id=translated["camera_id"],
            event_type=translated["event_type"],
            timestamp=ts,
            zone_id=translated.get("zone_id"),
            zone_name=translated.get("zone_name"),
            track_id=translated.get("track_id"),
            dwell_ms=translated.get("dwell_ms", 0),
            is_staff=translated.get("is_staff", False),
            confidence=translated.get("confidence"),
            metadata=translated.get("metadata", {}),
        )
        process_event(event_obj, db)
        ingested += 1

    db.commit()

    logger.info(
        "Raw ingest complete",
        extra={
            "trace_id": trace_id,
            "event_count": ingested,
            "duplicates": duplicates,
            "rejected": len(rejected),
        },
    )

    response: dict = {
        "ingested": ingested,
        "duplicates": duplicates,
        "rejected_count": len(rejected),
        "trace_id": trace_id,
    }
    if rejected:
        response["rejected"] = rejected
    return response
