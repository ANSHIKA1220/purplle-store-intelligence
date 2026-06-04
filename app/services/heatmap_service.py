"""
Heatmap service — zone visit frequency + avg dwell, normalised 0-100.

Includes a data_confidence flag when fewer than 20 sessions have been
observed in the query window.
"""

import logging
from typing import Dict, Any, List

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.db_event import EventDB
from app.models.session import SessionDB

logger = logging.getLogger("store_intelligence.heatmap")

LOW_CONFIDENCE_THRESHOLD = 20  # sessions below this → data_confidence: LOW


def get_heatmap(store_id: str, db: Session) -> Dict[str, Any]:
    """
    Returns per-zone heatmap data for a store.

    visit_count:    number of distinct visitor sessions per zone (non-staff)
    avg_dwell_ms:   average dwell time per zone in milliseconds
    heat_score:     visit_count normalised to 0-100 relative to the busiest zone
    data_confidence: HIGH / LOW (LOW if total sessions < 20)
    """

    # ----------------------------------------------------------------
    # Total unique customer sessions (for confidence flag)
    # ----------------------------------------------------------------
    total_sessions = (
        db.query(func.count(SessionDB.visitor_id))
        .filter(
            SessionDB.store_id == store_id,
            SessionDB.is_staff == False,  # noqa: E712
        )
        .scalar()
        or 0
    )

    data_confidence = "HIGH" if total_sessions >= LOW_CONFIDENCE_THRESHOLD else "LOW"

    # ----------------------------------------------------------------
    # Zone visit counts — support both ZONE_ENTER and ZONE_CHANGE
    # ----------------------------------------------------------------
    visit_rows = (
        db.query(
            EventDB.zone_id,
            EventDB.zone_name,
            func.count(func.distinct(EventDB.visitor_id)).label("visit_count"),
        )
        .filter(
            EventDB.store_id == store_id,
            EventDB.event_type.in_(["ZONE_ENTER", "ZONE_CHANGE"]),
            EventDB.zone_id.isnot(None),
            EventDB.is_staff == False,  # noqa: E712
        )
        .group_by(EventDB.zone_id, EventDB.zone_name)
        .all()
    )

    # ----------------------------------------------------------------
    # Avg dwell per zone — from ZONE_DWELL and DWELL_TIME events
    # ----------------------------------------------------------------
    dwell_rows = (
        db.query(
            EventDB.zone_id,
            func.avg(EventDB.dwell_ms).label("avg_dwell_ms"),
        )
        .filter(
            EventDB.store_id == store_id,
            EventDB.event_type.in_(["ZONE_DWELL", "DWELL_TIME", "ZONE_EXIT"]),
            EventDB.zone_id.isnot(None),
            EventDB.dwell_ms > 0,
            EventDB.is_staff == False,  # noqa: E712
        )
        .group_by(EventDB.zone_id)
        .all()
    )

    dwell_by_zone = {
        row.zone_id: round(float(row.avg_dwell_ms or 0), 2)
        for row in dwell_rows
    }

    # ----------------------------------------------------------------
    # Normalise visit counts to 0–100 heat score
    # ----------------------------------------------------------------
    if not visit_rows:
        return {
            "store_id": store_id,
            "data_confidence": data_confidence,
            "total_sessions": total_sessions,
            "zones": [],
        }

    max_visits = max((row.visit_count for row in visit_rows), default=1) or 1

    zones: List[Dict[str, Any]] = []
    for row in sorted(visit_rows, key=lambda r: r.visit_count, reverse=True):
        zone_id = row.zone_id or "UNKNOWN"
        zone_name = row.zone_name or zone_id
        visit_count = row.visit_count or 0
        heat_score = round(visit_count / max_visits * 100, 1)
        avg_dwell_ms = dwell_by_zone.get(zone_id, 0.0)

        zones.append(
            {
                "zone_id": zone_id,
                "zone_name": zone_name,
                "visit_count": visit_count,
                "avg_dwell_ms": avg_dwell_ms,
                "avg_dwell_seconds": round(avg_dwell_ms / 1000, 2),
                "heat_score": heat_score,
            }
        )

    return {
        "store_id": store_id,
        "data_confidence": data_confidence,
        "total_sessions": total_sessions,
        "zones": zones,
    }
