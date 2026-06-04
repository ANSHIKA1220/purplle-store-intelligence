import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.event import Event
from app.models.db_event import EventDB
from app.services.session_service import process_event

logger = logging.getLogger("store_intelligence.ingest")

router = APIRouter(tags=["Events"])

VALID_EVENT_TYPES = {
    "ENTRY",
    "EXIT",
    "ZONE_ENTER",
    "ZONE_EXIT",
    "ZONE_DWELL",
    "BILLING_QUEUE_JOIN",
    "BILLING_QUEUE_ABANDON",
    "REENTRY",
    # legacy CV pipeline events (kept for backward compat)
    "ZONE_CHANGE",
    "DWELL_TIME",
}


def _compute_dwell_ms(event, db) -> int:
    """
    Compute dwell_ms for a ZONE_EXIT event by finding the most recent
    ZONE_ENTER for the same visitor + zone and calculating the time delta.
    Returns 0 if no matching ZONE_ENTER is found.
    """
    try:
        enter_event = (
            db.query(EventDB)
            .filter(
                EventDB.visitor_id == event.visitor_id,
                EventDB.store_id == event.store_id,
                EventDB.zone_id == event.zone_id,
                EventDB.event_type.in_(["ZONE_ENTER", "ZONE_CHANGE"]),
                EventDB.timestamp <= event.timestamp,
            )
            .order_by(EventDB.timestamp.desc())
            .first()
        )
        if enter_event and enter_event.timestamp:
            enter_ts = enter_event.timestamp
            exit_ts = event.timestamp
            # Strip timezone if present
            if hasattr(exit_ts, "tzinfo") and exit_ts.tzinfo:
                exit_ts = exit_ts.replace(tzinfo=None)
            if hasattr(enter_ts, "tzinfo") and enter_ts.tzinfo:
                enter_ts = enter_ts.replace(tzinfo=None)
            delta_ms = int((exit_ts - enter_ts).total_seconds() * 1000)
            return max(0, delta_ms)
    except Exception:
        pass
    return 0


@router.post("/events/ingest", status_code=200)
def ingest_events(
    request: Request,
    events: List[Event],
    db: Session = Depends(get_db),
):
    """
    Ingest a batch of up to 500 events.

    - Idempotent by event_id — duplicate events are silently skipped.
    - Partial success: malformed/invalid events are rejected with details;
      valid events in the same batch are still ingested.
    - Returns counts for ingested, duplicates, and rejected events.
    """
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))

    if len(events) > 500:
        return {
            "error": "BATCH_TOO_LARGE",
            "message": f"Batch size {len(events)} exceeds maximum of 500.",
            "trace_id": trace_id,
        }

    ingested = 0
    duplicates = 0
    rejected = []

    for event in events:
        # Validate event_type
        if event.event_type.upper() not in VALID_EVENT_TYPES:
            rejected.append(
                {
                    "event_id": event.event_id,
                    "reason": f"Unknown event_type '{event.event_type}'",
                }
            )
            continue

        # Idempotency check
        existing = (
            db.query(EventDB)
            .filter(EventDB.event_id == event.event_id)
            .first()
        )
        if existing:
            duplicates += 1
            continue

        # Normalise event_type to canonical form
        canonical_type = event.event_type.upper()

        # Compute dwell_ms for ZONE_EXIT if not already provided
        dwell_ms = event.dwell_ms or 0
        if canonical_type == "ZONE_EXIT" and dwell_ms == 0 and event.zone_id:
            dwell_ms = _compute_dwell_ms(event, db)

        db_event = EventDB(
            event_id=event.event_id,
            visitor_id=event.visitor_id,
            store_id=event.store_id,
            camera_id=event.camera_id,
            event_type=canonical_type,
            timestamp=event.timestamp,
            zone_id=event.zone_id,
            zone_name=event.zone_name,
            track_id=event.track_id,
            dwell_ms=dwell_ms,
            is_staff=event.is_staff,
            confidence=event.confidence,
            metadata_json=event.metadata,
        )

        db.add(db_event)
        process_event(event, db)
        ingested += 1

    db.commit()

    logger.info(
        "Batch ingest complete",
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
