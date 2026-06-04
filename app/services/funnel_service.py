"""
Funnel service — Entry → Zone Visit → Billing Queue → Purchase

Session is the unit of analysis:
  - Re-entries must NOT double-count a visitor
  - Staff sessions excluded
  - Drop-off % is computed between consecutive funnel stages
"""

import logging
from typing import Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.db_event import EventDB
from app.models.session import SessionDB
from app.models.pos_transaction import POSTransaction

logger = logging.getLogger("store_intelligence.funnel")


def get_funnel(store_id: str, db: Session) -> Dict[str, Any]:
    """
    Returns the customer conversion funnel for a store.

    Stage 1 — Entry:         distinct customer sessions with at least one ENTRY event
    Stage 2 — Zone Visit:    sessions that visited at least one product zone
    Stage 3 — Billing Queue: sessions that reached billing queue
    Stage 4 — Purchase:      sessions marked as converted (POS-correlated)
    """

    # Stage 1 — Unique customer sessions (ENTRY events, non-staff)
    entry_visitor_ids = (
        db.query(EventDB.visitor_id)
        .filter(
            EventDB.store_id == store_id,
            EventDB.event_type == "ENTRY",
            EventDB.is_staff == False,  # noqa: E712
        )
        .distinct()
        .subquery()
    )

    stage1_entries = (
        db.query(func.count())
        .select_from(entry_visitor_ids)
        .scalar()
        or 0
    )

    # Stage 2 — Reached at least one zone (ZONE_ENTER or ZONE_CHANGE, non-staff)
    zone_visitor_ids = (
        db.query(EventDB.visitor_id)
        .filter(
            EventDB.store_id == store_id,
            EventDB.event_type.in_(["ZONE_ENTER", "ZONE_CHANGE"]),
            EventDB.is_staff == False,  # noqa: E712
        )
        .distinct()
        .subquery()
    )

    stage2_zone_visit = (
        db.query(func.count())
        .select_from(zone_visitor_ids)
        .scalar()
        or 0
    )

    # Stage 3 — Reached billing queue (BILLING_QUEUE_JOIN or sessions with reached_billing)
    stage3_billing_queue = (
        db.query(func.count(SessionDB.visitor_id))
        .filter(
            SessionDB.store_id == store_id,
            SessionDB.is_staff == False,  # noqa: E712
            SessionDB.reached_billing == True,  # noqa: E712
        )
        .scalar()
        or 0
    )

    # If no session-level data, fall back to event counts
    if stage3_billing_queue == 0:
        stage3_billing_queue = (
            db.query(func.count(func.distinct(EventDB.visitor_id)))
            .filter(
                EventDB.store_id == store_id,
                EventDB.event_type == "BILLING_QUEUE_JOIN",
                EventDB.is_staff == False,  # noqa: E712
            )
            .scalar()
            or 0
        )

    # Stage 4 — Purchased (converted sessions)
    stage4_purchase = (
        db.query(func.count(SessionDB.visitor_id))
        .filter(
            SessionDB.store_id == store_id,
            SessionDB.is_staff == False,  # noqa: E712
            SessionDB.converted == True,  # noqa: E712
        )
        .scalar()
        or 0
    )

    # If session conversion not tracked, use POS transaction count as proxy
    if stage4_purchase == 0:
        stage4_purchase = (
            db.query(func.count(POSTransaction.transaction_id))
            .filter(POSTransaction.store_id == store_id)
            .scalar()
            or 0
        )

    # ----------------------------------------------------------------
    # Compute drop-off percentages between stages
    # ----------------------------------------------------------------
    def dropoff(current: int, previous: int) -> float:
        if previous == 0:
            return 0.0
        return round((1 - current / previous) * 100, 2)

    return {
        "store_id": store_id,
        "funnel": [
            {
                "stage": "entry",
                "label": "Entered Store",
                "count": stage1_entries,
                "dropoff_pct": 0.0,
            },
            {
                "stage": "zone_visit",
                "label": "Visited Product Zone",
                "count": stage2_zone_visit,
                "dropoff_pct": dropoff(stage2_zone_visit, stage1_entries),
            },
            {
                "stage": "billing_queue",
                "label": "Reached Billing Queue",
                "count": stage3_billing_queue,
                "dropoff_pct": dropoff(stage3_billing_queue, stage2_zone_visit),
            },
            {
                "stage": "purchase",
                "label": "Completed Purchase",
                "count": stage4_purchase,
                "dropoff_pct": dropoff(stage4_purchase, stage3_billing_queue),
            },
        ],
        "overall_conversion_pct": round(
            stage4_purchase / stage1_entries * 100, 2
        )
        if stage1_entries > 0
        else 0.0,
    }
