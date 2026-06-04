"""
Anomaly detection service.

Detects the following active anomalies:

  BILLING_QUEUE_SPIKE   — current queue depth exceeds spike threshold
  CONVERSION_DROP       — today's conversion rate vs 7-day rolling average drops > 20%
  DEAD_ZONE             — a zone with historical activity has had no visits in 30 min
  HIGH_ABANDONMENT      — queue abandonment rate exceeds 20%
  STALE_FEED            — no events received in the last 10 minutes (wall-clock)

Time reference: all "recent" comparisons use the store's own most-recent event
timestamp as "now" for relative checks (DEAD_ZONE, conversion window), so that
historical datasets don't produce false-positive stale/dead alerts.

STALE_FEED is the only check that uses real wall-clock time, because its purpose
is to detect whether the live camera pipeline has stopped sending.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.db_event import EventDB
from app.models.session import SessionDB
from app.models.pos_transaction import POSTransaction

logger = logging.getLogger("store_intelligence.anomalies")

# Thresholds
QUEUE_SPIKE_CRITICAL = 8
QUEUE_SPIKE_WARN = 5
ABANDONMENT_WARN = 20.0
ABANDONMENT_CRITICAL = 40.0
CONVERSION_DROP_WARN = 20.0
CONVERSION_DROP_CRITICAL = 40.0
DEAD_ZONE_MINUTES = 30
STALE_FEED_MINUTES = 10
MIN_SESSIONS_FOR_CONVERSION = 10


def _latest_event_ts(store_id: str, db: Session) -> Optional[datetime]:
    """Return the most recent event timestamp for a store (naive UTC)."""
    ts = (
        db.query(func.max(EventDB.timestamp))
        .filter(EventDB.store_id == store_id)
        .scalar()
    )
    return ts  # naive UTC or None


def get_anomalies(store_id: str, db: Session) -> Dict[str, Any]:
    wall_now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC

    # Use the store's latest event as the "data now" for relative checks.
    # This avoids false positives when replaying historical datasets.
    data_now = _latest_event_ts(store_id, db) or wall_now

    anomalies: List[Dict[str, Any]] = []

    anomalies.extend(_check_queue_spike(store_id, db))
    anomalies.extend(_check_abandonment(store_id, db))
    anomalies.extend(_check_conversion_drop(store_id, db, data_now))
    anomalies.extend(_check_dead_zones(store_id, db, data_now))
    anomalies.extend(_check_stale_feed(store_id, db, wall_now))  # always wall-clock

    return {
        "store_id": store_id,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "store_id": store_id,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Individual anomaly detectors
# ─────────────────────────────────────────────────────────────────────────────

def _check_queue_spike(store_id: str, db: Session) -> List[Dict]:
    """Detect if the current billing queue depth is abnormally high."""
    latest = (
        db.query(EventDB)
        .filter(
            EventDB.store_id == store_id,
            EventDB.event_type == "BILLING_QUEUE_JOIN",
        )
        .order_by(EventDB.timestamp.desc())
        .first()
    )

    if not latest or not latest.metadata_json:
        return []

    queue_depth = latest.metadata_json.get("queue_depth", 0) or 0

    if queue_depth >= QUEUE_SPIKE_CRITICAL:
        severity = "CRITICAL"
    elif queue_depth >= QUEUE_SPIKE_WARN:
        severity = "WARN"
    else:
        return []

    return [
        {
            "type": "BILLING_QUEUE_SPIKE",
            "severity": severity,
            "value": queue_depth,
            "message": f"Billing queue depth is {queue_depth} — above normal threshold.",
            "suggested_action": (
                "Open an additional billing counter immediately."
                if severity == "CRITICAL"
                else "Monitor queue closely; consider opening a second counter."
            ),
        }
    ]


def _check_abandonment(store_id: str, db: Session) -> List[Dict]:
    """Detect high queue abandonment rate."""
    billing_sessions = (
        db.query(func.count(SessionDB.visitor_id))
        .filter(
            SessionDB.store_id == store_id,
            SessionDB.reached_billing == True,  # noqa: E712
        )
        .scalar()
        or 0
    )

    if billing_sessions == 0:
        return []

    abandoned = (
        db.query(func.count(SessionDB.visitor_id))
        .filter(
            SessionDB.store_id == store_id,
            SessionDB.abandoned_queue == True,  # noqa: E712
        )
        .scalar()
        or 0
    )

    rate = abandoned / billing_sessions * 100

    if rate >= ABANDONMENT_CRITICAL:
        severity = "CRITICAL"
    elif rate >= ABANDONMENT_WARN:
        severity = "WARN"
    else:
        return []

    return [
        {
            "type": "HIGH_ABANDONMENT",
            "severity": severity,
            "value": round(rate, 2),
            "message": f"Queue abandonment rate is {rate:.1f}% — customers are leaving before purchasing.",
            "suggested_action": (
                "Investigate checkout bottleneck immediately; consider fast-track lane."
                if severity == "CRITICAL"
                else "Review queue wait times and staff allocation at billing."
            ),
        }
    ]


def _check_conversion_drop(
    store_id: str, db: Session, now: datetime
) -> List[Dict]:
    """
    Compare today's conversion rate to the 7-day rolling average.
    Only fires if there are enough sessions to be statistically meaningful.
    """
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seven_days_ago = today_start - timedelta(days=7)

    # Today's stats
    today_sessions = (
        db.query(func.count(SessionDB.visitor_id))
        .filter(
            SessionDB.store_id == store_id,
            SessionDB.is_staff == False,  # noqa: E712
            SessionDB.entry_time >= today_start,
        )
        .scalar()
        or 0
    )

    if today_sessions < MIN_SESSIONS_FOR_CONVERSION:
        return []  # Not enough data

    today_converted = (
        db.query(func.count(SessionDB.visitor_id))
        .filter(
            SessionDB.store_id == store_id,
            SessionDB.is_staff == False,  # noqa: E712
            SessionDB.converted == True,  # noqa: E712
            SessionDB.entry_time >= today_start,
        )
        .scalar()
        or 0
    )

    today_rate = today_converted / today_sessions * 100

    # 7-day baseline
    hist_sessions = (
        db.query(func.count(SessionDB.visitor_id))
        .filter(
            SessionDB.store_id == store_id,
            SessionDB.is_staff == False,  # noqa: E712
            SessionDB.entry_time >= seven_days_ago,
            SessionDB.entry_time < today_start,
        )
        .scalar()
        or 0
    )

    if hist_sessions < MIN_SESSIONS_FOR_CONVERSION:
        return []  # No historical baseline

    hist_converted = (
        db.query(func.count(SessionDB.visitor_id))
        .filter(
            SessionDB.store_id == store_id,
            SessionDB.is_staff == False,  # noqa: E712
            SessionDB.converted == True,  # noqa: E712
            SessionDB.entry_time >= seven_days_ago,
            SessionDB.entry_time < today_start,
        )
        .scalar()
        or 0
    )

    hist_rate = hist_converted / hist_sessions * 100
    if hist_rate == 0:
        return []

    relative_drop = (hist_rate - today_rate) / hist_rate * 100

    if relative_drop >= CONVERSION_DROP_CRITICAL:
        severity = "CRITICAL"
    elif relative_drop >= CONVERSION_DROP_WARN:
        severity = "WARN"
    else:
        return []

    return [
        {
            "type": "CONVERSION_DROP",
            "severity": severity,
            "value": round(relative_drop, 2),
            "message": (
                f"Today's conversion rate ({today_rate:.1f}%) is {relative_drop:.1f}% "
                f"below the 7-day average ({hist_rate:.1f}%)."
            ),
            "suggested_action": (
                "Escalate to store manager — investigate product availability and staff engagement."
                if severity == "CRITICAL"
                else "Review today's promotions and floor-staff interactions to identify friction."
            ),
        }
    ]


def _check_dead_zones(
    store_id: str, db: Session, now: datetime
) -> List[Dict]:
    """
    Detect zones that had historical activity but have received no visits
    in the last DEAD_ZONE_MINUTES.
    """
    cutoff = now - timedelta(minutes=DEAD_ZONE_MINUTES)

    # All zones with any historical activity
    active_zones_ever = (
        db.query(EventDB.zone_id)
        .filter(
            EventDB.store_id == store_id,
            EventDB.event_type.in_(["ZONE_ENTER", "ZONE_CHANGE"]),
            EventDB.zone_id.isnot(None),
        )
        .distinct()
        .all()
    )

    all_zone_ids = {row.zone_id for row in active_zones_ever}
    if not all_zone_ids:
        return []

    # Zones with activity in the last window
    recently_active = (
        db.query(EventDB.zone_id)
        .filter(
            EventDB.store_id == store_id,
            EventDB.event_type.in_(["ZONE_ENTER", "ZONE_CHANGE"]),
            EventDB.zone_id.isnot(None),
            EventDB.timestamp >= cutoff,
        )
        .distinct()
        .all()
    )

    recently_active_ids = {row.zone_id for row in recently_active}
    dead_zones = all_zone_ids - recently_active_ids

    anomalies = []
    for zone_id in sorted(dead_zones):
        anomalies.append(
            {
                "type": "DEAD_ZONE",
                "severity": "INFO",
                "value": zone_id,
                "message": (
                    f"Zone '{zone_id}' has had no customer visits in the last "
                    f"{DEAD_ZONE_MINUTES} minutes."
                ),
                "suggested_action": (
                    f"Check for obstacles or lighting issues in zone '{zone_id}'. "
                    "Consider repositioning staff or signage to drive traffic."
                ),
            }
        )

    return anomalies


def _check_stale_feed(
    store_id: str, db: Session, now: datetime
) -> List[Dict]:
    """Detect if no events have been received from this store in 10 minutes."""
    cutoff = now - timedelta(minutes=STALE_FEED_MINUTES)

    latest_event = (
        db.query(func.max(EventDB.timestamp))
        .filter(EventDB.store_id == store_id)
        .scalar()
    )

    if latest_event is None:
        return []  # No events at all — don't flag as stale on first run

    if latest_event < cutoff:
        lag_minutes = int((now - latest_event).total_seconds() / 60)
        return [
            {
                "type": "STALE_FEED",
                "severity": "WARN",
                "value": lag_minutes,
                "message": (
                    f"No events received from store {store_id} for {lag_minutes} minutes. "
                    "Camera feed may be offline."
                ),
                "suggested_action": (
                    "Check CCTV network connectivity and pipeline health. "
                    "Restart the event pipeline if the feed has been interrupted."
                ),
            }
        ]

    return []
