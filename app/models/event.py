from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict, Any


class Event(BaseModel):

    event_id: str

    visitor_id: str

    store_id: str

    camera_id: str

    event_type: str

    timestamp: datetime

    zone_id: Optional[str] = None

    zone_name: str | None = None

    track_id: str | None = None

    dwell_ms: Optional[int] = 0

    is_staff: bool = False

    confidence: Optional[float] = None

    metadata: Optional[Dict[str, Any]] = {}