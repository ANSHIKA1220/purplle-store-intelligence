from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.db_event import EventDB
from app.models.pos_transaction import POSTransaction


def get_store_metrics(
        store_id: str,
        db: Session
):

    visitor_count = (
        db.query(EventDB.visitor_id)
        .filter(EventDB.store_id == store_id)
        .distinct()
        .count()
    )

    total_transactions = (
        db.query(POSTransaction)
        .filter(
            POSTransaction.store_id == store_id
        )
        .count()
    )

    revenue = (
        db.query(
            func.sum(
                POSTransaction.total_amount
            )
        )
        .filter(
            POSTransaction.store_id == store_id
        )
        .scalar()
    )

    revenue = revenue or 0

    conversion_rate = 0

    if visitor_count > 0:
        conversion_rate = (
            total_transactions /
            visitor_count
        ) * 100

    return {
        "store_id": store_id,
        "unique_visitors": visitor_count,
        "total_revenue": round(revenue, 2),
        "total_transactions": total_transactions,
        "conversion_rate": round(
            conversion_rate,
            2
        )
    }