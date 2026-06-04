"""
Session manager — tracks per-visitor state across the pipeline.

Maintains an in-memory registry of active visitor sessions.
Each session is keyed by visitor_id (derived from track_id).

Responsibilities:
  - Open a session on ENTRY
  - Detect RE-ENTRY (same track_id seen after EXIT within a time window)
  - Close a session on EXIT
  - Track zone history per visitor (for dwell + billing correlation)
  - Determine if a visitor abandons the billing queue

This is the in-pipeline state machine. Persistence to DB happens via
event emission through the EventEmitter.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, List

logger = logging.getLogger("pipeline.session_manager")

# If a known track_id reappears within this many seconds → REENTRY
REENTRY_WINDOW_SECONDS = 300  # 5 minutes


@dataclass
class VisitorSession:
    visitor_id: str
    store_id: str
    camera_id: str
    track_id: int
    entry_time: datetime
    is_staff: bool = False

    # Zone state
    current_zone: Optional[str] = None
    zone_entry_time: Optional[datetime] = None
    zone_history: List[str] = field(default_factory=list)

    # Billing state
    in_billing_queue: bool = False
    billing_entry_time: Optional[datetime] = None

    # Session state
    exited: bool = False
    exit_time: Optional[datetime] = None

    # Dwell tracking — 30s ZONE_DWELL events
    last_dwell_emit_time: Optional[datetime] = None

    # Event sequence counter
    session_seq: int = 0

    def next_seq(self) -> int:
        self.session_seq += 1
        return self.session_seq


class SessionManager:
    """
    Manages in-memory session state for the detection pipeline.

    track_id → visitor_id mapping is maintained here.
    A visitor's visitor_id is set when they first appear and persisted
    across potential tracker ID reassignments using trajectory similarity.
    """

    def __init__(self, store_id: str, camera_id: str):
        self.store_id = store_id
        self.camera_id = camera_id

        # Active sessions: visitor_id → VisitorSession
        self._active: Dict[str, VisitorSession] = {}

        # Recently exited: visitor_id → exit_time (for re-entry detection)
        self._exited: Dict[str, datetime] = {}

        # track_id → visitor_id mapping
        self._track_to_visitor: Dict[int, str] = {}

        logger.info("SessionManager ready for store=%s camera=%s", store_id, camera_id)

    def get_or_create(
        self,
        track_id: int,
        is_staff: bool = False,
    ) -> VisitorSession:
        """
        Get existing session for this track_id, or create a new one.
        """
        visitor_id = self._track_to_visitor.get(track_id)

        if visitor_id and visitor_id in self._active:
            return self._active[visitor_id]

        # New visitor — assign visitor_id
        visitor_id = f"VIS_{track_id:06d}"
        self._track_to_visitor[track_id] = visitor_id

        session = VisitorSession(
            visitor_id=visitor_id,
            store_id=self.store_id,
            camera_id=self.camera_id,
            track_id=track_id,
            entry_time=_utcnow(),
            is_staff=is_staff,
        )
        self._active[visitor_id] = session
        logger.debug("New session: %s (track_id=%d)", visitor_id, track_id)
        return session

    def is_reentry(self, track_id: int) -> bool:
        """Check if this track_id corresponds to a recently exited visitor."""
        visitor_id = self._track_to_visitor.get(track_id)
        if not visitor_id:
            return False

        exit_time = self._exited.get(visitor_id)
        if exit_time is None:
            return False

        elapsed = (_utcnow() - exit_time).total_seconds()
        return elapsed <= REENTRY_WINDOW_SECONDS

    def close_session(self, visitor_id: str) -> Optional[VisitorSession]:
        """Mark a session as exited."""
        session = self._active.get(visitor_id)
        if not session:
            return None

        session.exited = True
        session.exit_time = _utcnow()
        self._exited[visitor_id] = session.exit_time

        # Remove from active but keep track mapping for reentry detection
        del self._active[visitor_id]
        logger.debug("Session closed: %s", visitor_id)
        return session

    def get_by_track(self, track_id: int) -> Optional[VisitorSession]:
        """Look up active session by tracker ID."""
        visitor_id = self._track_to_visitor.get(track_id)
        if not visitor_id:
            return None
        return self._active.get(visitor_id)

    def get_visitor_id(self, track_id: int) -> str:
        """Get or generate visitor_id for a track_id."""
        if track_id not in self._track_to_visitor:
            self._track_to_visitor[track_id] = f"VIS_{track_id:06d}"
        return self._track_to_visitor[track_id]

    @property
    def active_count(self) -> int:
        return len(self._active)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
