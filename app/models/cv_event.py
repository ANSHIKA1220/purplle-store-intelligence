from sqlalchemy import Column, Integer, String, Float, DateTime

from app.database import Base


class CVEvent(Base):

    __tablename__ = "cv_events"

    id = Column(Integer, primary_key=True, index=True)

    event_type = Column(String)

    track_id = Column(Integer)

    zone = Column(String, nullable=True)

    value = Column(Float, nullable=True)

    timestamp = Column(DateTime)