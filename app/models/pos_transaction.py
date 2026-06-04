"""
POS Transaction model.

Matches the official challenge schema:
  store_id, transaction_id, timestamp, basket_value_inr
  STORE_BLR_002, TXN_00441, 2026-03-03T14:38:12Z, 1240.00

Also supports the legacy CSV format (order_id, order_date, order_time,
store_id, product_id, brand_name, total_amount) for backward compatibility.
"""

from sqlalchemy import Column, String, Float, DateTime
from app.database import Base


class POSTransaction(Base):

    __tablename__ = "pos_transactions"

    # Primary key — transaction_id from the canonical schema (e.g. TXN_00441)
    # Falls back to order_id string for legacy CSV data
    transaction_id = Column(String, primary_key=True)

    store_id = Column(String, index=True, nullable=False)

    # ISO-8601 UTC timestamp of the transaction
    timestamp = Column(DateTime, nullable=False, index=True)

    # Amount in INR
    basket_value_inr = Column(Float, nullable=False, default=0.0)
