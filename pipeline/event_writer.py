from datetime import datetime
import uuid

from app.database import SessionLocal
from app.models.db_event import EventDB


def save_event(
    event_type,
    track_id,
    zone=None,
    value=None
):

    db = SessionLocal()

    event = EventDB(
        event_id=str(uuid.uuid4()),
        visitor_id=f"track_{track_id}",
        store_id="ST1076",
        camera_id="CV_PIPELINE",
        event_type=event_type,
        timestamp=datetime.utcnow(),
        zone_id=zone,
        zone_name=zone,
        track_id=str(track_id),
        dwell_ms=int(value * 1000)
        if event_type == "DWELL_TIME" and value is not None
        else 0,
        confidence=1.0
    )

    db.add(event)

    db.commit()

    db.close()