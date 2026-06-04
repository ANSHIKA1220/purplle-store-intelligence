from sqlalchemy import Column
from sqlalchemy import String
from sqlalchemy import DateTime
from sqlalchemy import Boolean
from sqlalchemy import Integer
from app.database import Base


class SessionDB(Base):

    __tablename__ = "sessions"

    visitor_id = Column(String, primary_key=True)

    store_id = Column(String)

    entry_time = Column(DateTime)

    exit_time = Column(DateTime)

    dwell_seconds = Column(Integer)

    converted = Column(Boolean, default=False)

    is_staff = Column(Boolean, default=False)

