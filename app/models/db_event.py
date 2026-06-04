from sqlalchemy import Column
from sqlalchemy import String
from sqlalchemy import Integer
from sqlalchemy import Float
from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import JSON

from app.database import Base


class EventDB(Base):

    __tablename__ = "events"

    event_id = Column(String, primary_key=True)

    visitor_id = Column(String)

    store_id = Column(String)

    camera_id = Column(String)

    event_type = Column(String)

    timestamp = Column(DateTime)

    zone_id = Column(String)

    zone_name = Column(String)

    track_id = Column(String)

    dwell_ms = Column(Integer)

    is_staff = Column(Boolean)

    confidence = Column(Float)

    metadata_json = Column(JSON)