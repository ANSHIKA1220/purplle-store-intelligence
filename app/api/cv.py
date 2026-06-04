from fastapi import APIRouter
from sqlalchemy import func

from app.database import SessionLocal
from app.models.cv_event import CVEvent

router = APIRouter()


@router.get("/cv/summary")
def cv_summary():

    db = SessionLocal()

    entries = db.query(CVEvent).filter(
        CVEvent.event_type == "ENTRY"
    ).count()

    exits = db.query(CVEvent).filter(
        CVEvent.event_type == "EXIT"
    ).count()

    zone_changes = db.query(CVEvent).filter(
        CVEvent.event_type == "ZONE_CHANGE"
    ).count()

    dwell_events = db.query(CVEvent).filter(
        CVEvent.event_type == "DWELL_TIME"
    ).count()

    db.close()

    return {
        "entries": entries,
        "exits": exits,
        "zone_changes": zone_changes,
        "dwell_events": dwell_events
    }


@router.get("/cv/zones")
def zone_summary():

    db = SessionLocal()

    results = (
        db.query(
            CVEvent.zone,
            func.count(CVEvent.id)
        )
        .filter(CVEvent.event_type == "ZONE_CHANGE")
        .group_by(CVEvent.zone)
        .all()
    )

    db.close()

    return {
        zone: count
        for zone, count in results
    }


@router.get("/cv/dwell")
def dwell_summary():

    db = SessionLocal()

    results = (
        db.query(
            CVEvent.zone,
            func.sum(CVEvent.value)
        )
        .filter(CVEvent.event_type == "DWELL_TIME")
        .group_by(CVEvent.zone)
        .all()
    )

    db.close()

    return {
        zone: round(value, 2)
        for zone, value in results
    }