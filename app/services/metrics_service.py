"""
Metrics service — real-time store analytics.

Conversion rate is computed using session-correlated POS transactions:
a visitor who was in the billing zone within the 5 minutes before a
transaction timestamp counts as converted.
"""

import logging
from datetime import timedelta
from typing import Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.db_event import EventDB
from app.models.pos_transaction import POSTransaction
from app.models.session import SessionDB

logger = logging.getLogger("store_intelligence.metrics")

BILLING_CORRELATION_WINDOW_MINUTES = 5


def get_store_metrics(store_id: str, db: Session) -> Dict[str, Any]:
    """
    Returns real-time metrics for a store.

    Unique visitors: distinct visitor_ids in sessions where is_staff=False.
    Conversion rate: sessions correlated to a POS transaction (time-window method).
    Avg dwell per zone: average dwell_ms from ZONE_DWELL / DWELL_TIME events per zone.
    Queue depth: latest queue_depth value from BILLING_QUEUE_JOIN metadata.
    Abandonment rate: abandoned sessions / total billing-queue sessions.
    """

    # ----------------------------------------------------------------
    # 1. Unique customer sessions (exclude staff)
    # ----------------------------------------------------------------
    unique_visitors = (
        db.query(func.count(SessionDB.visitor_id))
        .filter(
            SessionDB.store_id == store_id,
            SessionDB.is_staff == False,  # noqa: E712
        )
        .scalar()
        or 0
    )

    # ----------------------------------------------------------------
    # 2. POS transactions
    # ----------------------------------------------------------------
    total_transactions = (
        db.query(func.count(POSTransaction.transaction_id))
        .filter(POSTransaction.store_id == store_id)
        .scalar()
        or 0
    )

    revenue = (
        db.query(func.sum(POSTransaction.basket_value_inr))
        .filter(POSTransaction.store_id == store_id)
        .scalar()
        or 0.0
    )

    # ----------------------------------------------------------------
    # 3. Conversion rate via session ↔ POS time-window correlation
    #    A visitor is "converted" if they were in the billing zone
    #    within 5 minutes before any POS transaction timestamp.
    # ----------------------------------------------------------------
    converted_visitors = (
        db.query(func.count(SessionDB.visitor_id))
        .filter(
            SessionDB.store_id == store_id,
            SessionDB.is_staff == False,  # noqa: E712
            SessionDB.converted == True,  # noqa: E712
        )
        .scalar()
        or 0
    )

    # If nobody has been marked converted yet, try to correlate on-the-fly
    if converted_visitors == 0 and total_transactions > 0:
        converted_visitors = _correlate_conversions(store_id, db)

    conversion_rate = (
        round(converted_visitors / unique_visitors * 100, 2)
        if unique_visitors > 0
        else 0.0
    )

    # ----------------------------------------------------------------
    # 4. Avg dwell time per zone
    # ----------------------------------------------------------------
    avg_dwell_per_zone = _avg_dwell_per_zone(store_id, db)

    # ----------------------------------------------------------------
    # 5. Current queue depth (latest value from metadata)
    # ----------------------------------------------------------------
    current_queue_depth = _current_queue_depth(store_id, db)

    # ----------------------------------------------------------------
    # 6. Abandonment rate
    # ----------------------------------------------------------------
    billing_sessions = (
        db.query(func.count(SessionDB.visitor_id))
        .filter(
            SessionDB.store_id == store_id,
            SessionDB.reached_billing == True,  # noqa: E712
        )
        .scalar()
        or 0
    )

    abandoned = (
        db.query(func.count(SessionDB.visitor_id))
        .filter(
            SessionDB.store_id == store_id,
            SessionDB.abandoned_queue == True,  # noqa: E712
        )
        .scalar()
        or 0
    )

    abandonment_rate = (
        round(abandoned / billing_sessions * 100, 2)
        if billing_sessions > 0
        else 0.0
    )

    return {
        "store_id": store_id,
        "unique_visitors": unique_visitors,
        "converted_visitors": converted_visitors,
        "conversion_rate": conversion_rate,
        "total_transactions": total_transactions,
        "total_revenue": round(float(revenue), 2),
        "avg_dwell_per_zone": avg_dwell_per_zone,
        "current_queue_depth": current_queue_depth,
        "billing_sessions": billing_sessions,
        "abandonment_rate": abandonment_rate,
    }


def _correlate_conversions(store_id: str, db: Session) -> int:
    """
    For each POS transaction, find sessions where the visitor was present
    in the billing zone in the 5 minutes before the transaction.
    Returns count of distinct converted visitor_ids and persists the flag.
    """
    transactions = (
        db.query(POSTransaction)
        .filter(POSTransaction.store_id == store_id)
        .all()
    )

    converted_ids = set()

    for txn in transactions:
        txn_time = txn.timestamp
        window_start = txn_time - timedelta(minutes=BILLING_CORRELATION_WINDOW_MINUTES)

        # Find billing-zone events in the window
        billing_events = (
            db.query(EventDB.visitor_id)
            .filter(
                EventDB.store_id == store_id,
                EventDB.event_type.in_(
                    ["BILLING_QUEUE_JOIN", "ZONE_ENTER", "ZONE_CHANGE"]
                ),
                EventDB.zone_id.in_(["BILLING", "BILLING_COUNTER"]),
                EventDB.timestamp >= window_start,
                EventDB.timestamp <= txn_time,
                EventDB.is_staff == False,  # noqa: E712
            )
            .distinct()
            .all()
        )

        for (visitor_id,) in billing_events:
            converted_ids.add(visitor_id)

    # Persist the conversion flag
    if converted_ids:
        sessions = (
            db.query(SessionDB)
            .filter(
                SessionDB.store_id == store_id,
                SessionDB.visitor_id.in_(converted_ids),
            )
            .all()
        )
        for s in sessions:
            s.converted = True
        db.commit()

    return len(converted_ids)


def _avg_dwell_per_zone(store_id: str, db: Session) -> Dict[str, float]:
    """Average dwell time in milliseconds per zone."""
    rows = (
        db.query(
            EventDB.zone_id,
            func.avg(EventDB.dwell_ms).label("avg_dwell"),
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
    return {
        row.zone_id: round(float(row.avg_dwell or 0), 2)
        for row in rows
    }


def _current_queue_depth(store_id: str, db: Session) -> int:
    """Return the most recently observed queue_depth from BILLING_QUEUE_JOIN events."""
    latest = (
        db.query(EventDB)
        .filter(
            EventDB.store_id == store_id,
            EventDB.event_type == "BILLING_QUEUE_JOIN",
        )
        .order_by(EventDB.timestamp.desc())
        .first()
    )
    if latest and latest.metadata_json:
        return latest.metadata_json.get("queue_depth", 0) or 0
    return 0
