from datetime import datetime

from app.database import SessionLocal
from app.models.cv_event import CVEvent


def save_event(
    event_type,
    track_id,
    zone=None,
    value=None
):

    db = SessionLocal()

    event = CVEvent(
        event_type=event_type,
        track_id=track_id,
        zone=zone,
        value=value,
        timestamp=datetime.utcnow()
    )

    db.add(event)

    db.commit()

    db.close()