"""
CV Analytics endpoints — reads from the main events table.

These endpoints provide a live view of computer vision pipeline output,
unified across both the legacy cv_events table and the main events table.
The main events table is the authoritative source.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models.db_event import EventDB

router = APIRouter(tags=["CV Analytics"])


@router.get("/cv/summary")
def cv_summary(
    store_id: str = Query(None, description="Filter by store ID"),
    db: Session = Depends(get_db),
):
    """
    Returns counts of CV-generated event types from the main events table.
    Optionally filtered by store_id.
    """
    q = db.query(EventDB)
    if store_id:
        q = q.filter(EventDB.store_id == store_id)

    entries = q.filter(EventDB.event_type == "ENTRY").count()
    exits = q.filter(EventDB.event_type == "EXIT").count()

    # Zone changes covers both ZONE_ENTER (new schema) and ZONE_CHANGE (legacy)
    zone_changes = q.filter(
        EventDB.event_type.in_(["ZONE_ENTER", "ZONE_CHANGE"])
    ).count()

    # Dwell events covers both ZONE_DWELL (new schema) and DWELL_TIME (legacy)
    dwell_events = q.filter(
        EventDB.event_type.in_(["ZONE_DWELL", "DWELL_TIME"])
    ).count()

    reentries = q.filter(EventDB.event_type == "REENTRY").count()
    billing_joins = q.filter(EventDB.event_type == "BILLING_QUEUE_JOIN").count()
    billing_abandons = q.filter(EventDB.event_type == "BILLING_QUEUE_ABANDON").count()

    return {
        "entries": entries,
        "exits": exits,
        "zone_changes": zone_changes,
        "dwell_events": dwell_events,
        "reentries": reentries,
        "billing_queue_joins": billing_joins,
        "billing_queue_abandons": billing_abandons,
    }


@router.get("/cv/zones")
def zone_summary(
    store_id: str = Query(None, description="Filter by store ID"),
    db: Session = Depends(get_db),
):
    """
    Returns zone visit counts from the main events table.
    Covers both ZONE_ENTER and ZONE_CHANGE event types.
    """
    q = (
        db.query(
            func.coalesce(EventDB.zone_name, EventDB.zone_id, "UNKNOWN").label("zone"),
            func.count().label("count"),
        )
        .filter(EventDB.event_type.in_(["ZONE_ENTER", "ZONE_CHANGE"]))
        .filter(EventDB.zone_id.isnot(None))
    )
    if store_id:
        q = q.filter(EventDB.store_id == store_id)

    results = q.group_by(
        func.coalesce(EventDB.zone_name, EventDB.zone_id, "UNKNOWN")
    ).all()

    return {zone: count for zone, count in results}


@router.get("/cv/dwell")
def dwell_summary(
    store_id: str = Query(None, description="Filter by store ID"),
    db: Session = Depends(get_db),
):
    """
    Returns average dwell time in seconds per zone.
    Uses dwell_ms from ZONE_DWELL/ZONE_EXIT events.
    Also computes dwell from ZONE_ENTER/ZONE_EXIT pairs where dwell_ms is available.
    """
    q = (
        db.query(
            func.coalesce(EventDB.zone_name, EventDB.zone_id, "UNKNOWN").label("zone"),
            func.avg(EventDB.dwell_ms).label("avg_dwell_ms"),
        )
        .filter(
            EventDB.event_type.in_(["ZONE_DWELL", "ZONE_EXIT", "DWELL_TIME"]),
            EventDB.zone_id.isnot(None),
            EventDB.dwell_ms > 0,
        )
    )
    if store_id:
        q = q.filter(EventDB.store_id == store_id)

    results = q.group_by(
        func.coalesce(EventDB.zone_name, EventDB.zone_id, "UNKNOWN")
    ).all()

    return {
        zone: round(float(avg_ms or 0) / 1000, 2)  # Convert ms → seconds
        for zone, avg_ms in results
    }
