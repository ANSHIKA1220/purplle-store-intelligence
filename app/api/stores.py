"""
GET /stores — list all store IDs that have data in the system.
Used by the dashboard to auto-populate the store selector.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models.db_event import EventDB

router = APIRouter(tags=["Stores"])


@router.get("/stores")
def list_stores(db: Session = Depends(get_db)):
    """
    Returns all store IDs that have at least one event ingested,
    along with their event count and latest event timestamp.
    """
    rows = (
        db.query(
            EventDB.store_id,
            func.count(EventDB.event_id).label("event_count"),
            func.max(EventDB.timestamp).label("latest_event"),
        )
        .group_by(EventDB.store_id)
        .order_by(func.count(EventDB.event_id).desc())
        .all()
    )

    stores = [
        {
            "store_id": row.store_id,
            "event_count": row.event_count,
            "latest_event": row.latest_event.isoformat() if row.latest_event else None,
        }
        for row in rows
    ]

    return {
        "stores": stores,
        "total": len(stores),
    }
