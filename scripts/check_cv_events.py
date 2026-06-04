from app.database import SessionLocal
from app.models.cv_event import CVEvent

db = SessionLocal()

events = db.query(CVEvent).all()

for event in events:
    print(
        event.id,
        event.event_type,
        event.track_id,
        event.zone,
        event.value
    )

db.close()