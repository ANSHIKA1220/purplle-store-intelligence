"""
Session lifecycle service.

Maintains the sessions table from inbound events:
  ENTRY       → open a new session (or create if first-seen)
  REENTRY     → mark existing session as reentry, do NOT create a duplicate
  EXIT        → close session, compute dwell_seconds
  ZONE_ENTER / ZONE_CHANGE → increment zone_visits on session
  BILLING_QUEUE_JOIN       → flag session as having reached billing queue
  BILLING_QUEUE_ABANDON    → flag session as queue abandoner
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session as DBSession

from app.models.event import Event
from app.models.session import SessionDB

logger = logging.getLogger("store_intelligence.session")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # store naive UTC


def process_event(event: Event, db: DBSession) -> None:
    event_type = event.event_type.upper()

    # ------------------------------------------------------------------ ENTRY
    if event_type == "ENTRY":
        existing = (
            db.query(SessionDB)
            .filter(SessionDB.visitor_id == event.visitor_id)
            .first()
        )
        if not existing:
            session = SessionDB(
                visitor_id=event.visitor_id,
                store_id=event.store_id,
                entry_time=_strip_tz(event.timestamp),
                is_staff=event.is_staff,
                converted=False,
                reentry=False,
                reached_billing=False,
                abandoned_queue=False,
                zone_visits=0,
            )
            db.add(session)
        # If session already exists, this is a reentry handled elsewhere — skip

    # --------------------------------------------------------------- REENTRY
    elif event_type == "REENTRY":
        session = _get_session(event.visitor_id, db)
        if session:
            session.reentry = True
        # Reentry must NOT open a duplicate session — same visitor_id

    # ------------------------------------------------------------------- EXIT
    elif event_type == "EXIT":
        session = _get_session(event.visitor_id, db)
        if session and session.exit_time is None:
            ts = _strip_tz(event.timestamp)
            session.exit_time = ts
            if session.entry_time:
                delta = (ts - session.entry_time).total_seconds()
                session.dwell_seconds = max(0, int(delta))

    # ------------------------------------------- ZONE_ENTER / ZONE_CHANGE
    elif event_type in ("ZONE_ENTER", "ZONE_CHANGE"):
        session = _get_session(event.visitor_id, db)
        if session:
            session.zone_visits = (session.zone_visits or 0) + 1

    # ------------------------------------------- BILLING_QUEUE_JOIN
    elif event_type == "BILLING_QUEUE_JOIN":
        session = _get_session(event.visitor_id, db)
        if session:
            session.reached_billing = True

    # ------------------------------------------- BILLING_QUEUE_ABANDON
    elif event_type == "BILLING_QUEUE_ABANDON":
        session = _get_session(event.visitor_id, db)
        if session:
            session.abandoned_queue = True


def mark_converted(visitor_id: str, db: DBSession) -> None:
    """Called externally when a POS transaction is correlated to a session."""
    session = _get_session(visitor_id, db)
    if session:
        session.converted = True


def _get_session(visitor_id: str, db: DBSession):
    return (
        db.query(SessionDB)
        .filter(SessionDB.visitor_id == visitor_id)
        .first()
    )


def _strip_tz(dt: datetime) -> datetime:
    """Return naive UTC datetime (SQLite doesn't store tz info)."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
