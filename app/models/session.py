from sqlalchemy import Column, String, DateTime, Boolean, Integer
from app.database import Base


class SessionDB(Base):

    __tablename__ = "sessions"

    visitor_id = Column(String, primary_key=True)

    store_id = Column(String, index=True)

    entry_time = Column(DateTime)

    exit_time = Column(DateTime, nullable=True)

    dwell_seconds = Column(Integer, nullable=True)

    converted = Column(Boolean, default=False)

    is_staff = Column(Boolean, default=False)

    reentry = Column(Boolean, default=False)

    # reached billing queue (BILLING_QUEUE_JOIN received)
    reached_billing = Column(Boolean, default=False)

    # left queue without purchasing (BILLING_QUEUE_ABANDON received)
    abandoned_queue = Column(Boolean, default=False)

    # number of zone visits/changes during this session
    zone_visits = Column(Integer, default=0)
