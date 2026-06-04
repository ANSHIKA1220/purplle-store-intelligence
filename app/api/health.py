from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from app.database import get_db
from app.models.db_event import EventDB

router = APIRouter(tags=["Health"])

STALE_THRESHOLD_MINUTES = 10


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """
    Service health check.

    Returns:
    - status: OK or DEGRADED
    - db_connected: bool
    - last_event_per_store: latest event timestamp per store
    - stale_feeds: list of store_ids with no event in the last 10 minutes
    - checked_at: current UTC time
    """
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(minutes=STALE_THRESHOLD_MINUTES)

    db_connected = True
    last_event_per_store: Dict[str, Optional[str]] = {}
    stale_feeds = []

    try:
        # Quick connectivity check
        db.execute(text("SELECT 1"))

        # Latest event timestamp per store
        rows = (
            db.query(
                EventDB.store_id,
                func.max(EventDB.timestamp).label("last_event"),
            )
            .group_by(EventDB.store_id)
            .all()
        )

        for row in rows:
            ts = row.last_event
            if ts is not None:
                # Make aware if naive
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                last_event_per_store[row.store_id] = ts.isoformat()
                if ts < stale_cutoff:
                    stale_feeds.append(row.store_id)
            else:
                last_event_per_store[row.store_id] = None
                stale_feeds.append(row.store_id)

    except Exception as exc:
        db_connected = False
        return {
            "status": "DEGRADED",
            "db_connected": False,
            "error": str(exc),
            "checked_at": now.isoformat(),
        }

    status = "OK" if db_connected and not stale_feeds else "DEGRADED"

    return {
        "status": status,
        "db_connected": db_connected,
        "last_event_per_store": last_event_per_store,
        "stale_feeds": stale_feeds,
        "stale_threshold_minutes": STALE_THRESHOLD_MINUTES,
        "checked_at": now.isoformat(),
    }
