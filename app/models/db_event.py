from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, JSON, Index
from app.database import Base


class EventDB(Base):

    __tablename__ = "events"

    event_id = Column(String, primary_key=True)

    visitor_id = Column(String, index=True, nullable=False)

    store_id = Column(String, index=True, nullable=False)

    camera_id = Column(String)

    event_type = Column(String, index=True, nullable=False)

    timestamp = Column(DateTime, index=True, nullable=False)

    zone_id = Column(String, nullable=True)

    zone_name = Column(String, nullable=True)

    track_id = Column(String, nullable=True)

    dwell_ms = Column(Integer, nullable=True, default=0)

    is_staff = Column(Boolean, nullable=False, default=False)

    confidence = Column(Float, nullable=True)

    metadata_json = Column(JSON, nullable=True)

    # Composite index for common query patterns
    __table_args__ = (
        Index("ix_events_store_type_ts", "store_id", "event_type", "timestamp"),
        Index("ix_events_store_visitor", "store_id", "visitor_id"),
    )
