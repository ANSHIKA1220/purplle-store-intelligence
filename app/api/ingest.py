from fastapi import APIRouter
from fastapi import Depends
from app.services.session_service import process_event
from sqlalchemy.orm import Session

from app.database import get_db

from app.models.event import Event
from app.models.db_event import EventDB


router = APIRouter(
    tags=["Events"]
)


@router.post("/events/ingest")
def ingest_events(
        events: list[Event],
        db: Session = Depends(get_db)
):
    ingested = 0
    duplicates = 0

    for event in events:

        existing = (
            db.query(EventDB)
            .filter(EventDB.event_id == event.event_id)
            .first()
        )

        if existing:
            duplicates += 1
            continue

        db_event = EventDB(
            event_id=event.event_id,
            visitor_id=event.visitor_id,
            store_id=event.store_id,
            camera_id=event.camera_id,
            event_type=event.event_type,
            timestamp=event.timestamp,
            zone_id=event.zone_id,
            zone_name=event.zone_name,
            track_id=event.track_id,
            dwell_ms=event.dwell_ms,
            is_staff=event.is_staff,
            confidence=event.confidence,
            metadata_json=event.metadata
        )

        db.add(db_event)
        process_event(event, db)
        ingested += 1

    db.commit()

    return {
        "ingested": ingested,
        "duplicates": duplicates
    }