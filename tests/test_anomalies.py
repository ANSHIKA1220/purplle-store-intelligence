# PROMPT: Write pytest tests for an anomaly detection API that detects:
# BILLING_QUEUE_SPIKE (queue_depth > threshold), HIGH_ABANDONMENT (>20%),
# CONVERSION_DROP (today vs 7-day avg drops >20%), DEAD_ZONE (no visits in 30min),
# STALE_FEED (no events in 10 min). Tests must verify severity levels (INFO/WARN/CRITICAL)
# and that suggested_action is a non-empty string. Edge case: no anomalies returns
# empty list, not null.
#
# CHANGES MADE: Fixed stale feed test to seed events with timestamps in the past
# (>10 minutes ago), adjusted DEAD_ZONE test to seed historical events and then
# not seed recent events. Added assertion for anomaly_count field.

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.db_event import EventDB
from app.models.session import SessionDB

client = TestClient(app)
STORE = "STORE_BLR_002"


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def ingest(events):
    return client.post("/events/ingest", json=events).json()


def make_event(event_type, store_id=STORE, zone_id=None, is_staff=False, ts=None, metadata=None):
    return {
        "event_id": str(uuid.uuid4()),
        "visitor_id": f"VIS_{uuid.uuid4().hex[:6]}",
        "store_id": store_id,
        "camera_id": "CAM_BILLING_01",
        "event_type": event_type,
        "timestamp": (ts or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": zone_id,
        "dwell_ms": 0,
        "is_staff": is_staff,
        "confidence": 0.85,
        "metadata": metadata or {},
    }


def seed_event_direct(event_type, store_id=STORE, ts=None, zone_id=None, metadata=None):
    """Directly insert an event into DB (bypasses ingest batching) for time-sensitive tests."""
    db = SessionLocal()
    db.add(EventDB(
        event_id=str(uuid.uuid4()),
        visitor_id=f"VIS_{uuid.uuid4().hex[:6]}",
        store_id=store_id,
        camera_id="CAM_TEST",
        event_type=event_type,
        timestamp=ts or datetime.utcnow(),
        zone_id=zone_id,
        is_staff=False,
        confidence=0.9,
        metadata_json=metadata or {},
    ))
    db.commit()
    db.close()


def seed_session(visitor_id, reached_billing=False, abandoned_queue=False, is_staff=False):
    db = SessionLocal()
    db.add(SessionDB(
        visitor_id=visitor_id, store_id=STORE,
        entry_time=datetime.utcnow(), is_staff=is_staff,
        reached_billing=reached_billing, abandoned_queue=abandoned_queue,
        converted=False, reentry=False, zone_visits=1,
    ))
    db.commit()
    db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Basic response structure
# ─────────────────────────────────────────────────────────────────────────────

def test_anomalies_response_structure():
    resp = client.get(f"/stores/{STORE}/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert "anomalies" in data
    assert "anomaly_count" in data
    assert "checked_at" in data
    assert isinstance(data["anomalies"], list)


def test_no_anomalies_returns_empty_list():
    """No data → no anomalies (not null, not crash)."""
    resp = client.get(f"/stores/{STORE}/anomalies")
    data = resp.json()
    assert data["anomalies"] == []
    assert data["anomaly_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Queue spike
# ─────────────────────────────────────────────────────────────────────────────

def test_queue_spike_warn():
    """queue_depth=5 → WARN severity."""
    seed_event_direct("BILLING_QUEUE_JOIN", zone_id="BILLING", metadata={"queue_depth": 5})
    resp = client.get(f"/stores/{STORE}/anomalies")
    types = [a["type"] for a in resp.json()["anomalies"]]
    assert "BILLING_QUEUE_SPIKE" in types
    spike = next(a for a in resp.json()["anomalies"] if a["type"] == "BILLING_QUEUE_SPIKE")
    assert spike["severity"] == "WARN"


def test_queue_spike_critical():
    """queue_depth=9 → CRITICAL severity."""
    seed_event_direct("BILLING_QUEUE_JOIN", zone_id="BILLING", metadata={"queue_depth": 9})
    resp = client.get(f"/stores/{STORE}/anomalies")
    spike = next((a for a in resp.json()["anomalies"] if a["type"] == "BILLING_QUEUE_SPIKE"), None)
    assert spike is not None
    assert spike["severity"] == "CRITICAL"
    assert isinstance(spike["suggested_action"], str)
    assert len(spike["suggested_action"]) > 0


def test_no_queue_spike_below_threshold():
    """queue_depth=3 → no spike anomaly."""
    seed_event_direct("BILLING_QUEUE_JOIN", zone_id="BILLING", metadata={"queue_depth": 3})
    resp = client.get(f"/stores/{STORE}/anomalies")
    types = [a["type"] for a in resp.json()["anomalies"]]
    assert "BILLING_QUEUE_SPIKE" not in types


# ─────────────────────────────────────────────────────────────────────────────
# High abandonment
# ─────────────────────────────────────────────────────────────────────────────

def test_high_abandonment_warn():
    """25% abandonment → WARN."""
    for i in range(4):
        seed_session(f"VIS_{i:03d}", reached_billing=True, abandoned_queue=(i == 0))

    resp = client.get(f"/stores/{STORE}/anomalies")
    types = [a["type"] for a in resp.json()["anomalies"]]
    assert "HIGH_ABANDONMENT" in types
    anom = next(a for a in resp.json()["anomalies"] if a["type"] == "HIGH_ABANDONMENT")
    assert anom["severity"] == "WARN"
    assert anom["value"] == pytest.approx(25.0, abs=0.1)


def test_high_abandonment_critical():
    """50% abandonment (3/6) → CRITICAL."""
    for i in range(6):
        seed_session(f"VIS_{i:03d}", reached_billing=True, abandoned_queue=(i < 3))

    resp = client.get(f"/stores/{STORE}/anomalies")
    anom = next((a for a in resp.json()["anomalies"] if a["type"] == "HIGH_ABANDONMENT"), None)
    assert anom is not None
    assert anom["severity"] == "CRITICAL"


def test_no_abandonment_below_threshold():
    """10% abandonment → no anomaly."""
    for i in range(10):
        seed_session(f"VIS_{i:03d}", reached_billing=True, abandoned_queue=(i == 0))

    resp = client.get(f"/stores/{STORE}/anomalies")
    types = [a["type"] for a in resp.json()["anomalies"]]
    assert "HIGH_ABANDONMENT" not in types


# ─────────────────────────────────────────────────────────────────────────────
# Stale feed
# ─────────────────────────────────────────────────────────────────────────────

def test_stale_feed_detected():
    """Last event >10 minutes ago → STALE_FEED WARN."""
    old_ts = datetime.utcnow() - timedelta(minutes=15)
    seed_event_direct("ENTRY", ts=old_ts)

    resp = client.get(f"/stores/{STORE}/anomalies")
    types = [a["type"] for a in resp.json()["anomalies"]]
    assert "STALE_FEED" in types
    stale = next(a for a in resp.json()["anomalies"] if a["type"] == "STALE_FEED")
    assert stale["severity"] == "WARN"
    assert stale["value"] >= 10


def test_no_stale_feed_for_recent_events():
    """Recent event → no stale feed."""
    seed_event_direct("ENTRY", ts=datetime.utcnow())
    resp = client.get(f"/stores/{STORE}/anomalies")
    types = [a["type"] for a in resp.json()["anomalies"]]
    assert "STALE_FEED" not in types


def test_no_stale_feed_when_no_events():
    """No events at all → no stale feed (can't alert on unknown state)."""
    resp = client.get(f"/stores/{STORE}/anomalies")
    types = [a["type"] for a in resp.json()["anomalies"]]
    assert "STALE_FEED" not in types


# ─────────────────────────────────────────────────────────────────────────────
# Dead zone
# ─────────────────────────────────────────────────────────────────────────────

def test_dead_zone_detected():
    """Zone with historical activity but no recent visits → DEAD_ZONE INFO."""
    # Seed a zone event 40 minutes ago
    old_ts = datetime.utcnow() - timedelta(minutes=40)
    seed_event_direct("ZONE_ENTER", zone_id="LEFT_SHELF", ts=old_ts)
    # Seed a recent ENTRY event so data_now is current (making the zone appear dead)
    seed_event_direct("ENTRY", ts=datetime.utcnow())

    resp = client.get(f"/stores/{STORE}/anomalies")
    dead = [a for a in resp.json()["anomalies"] if a["type"] == "DEAD_ZONE"]
    assert len(dead) >= 1
    assert dead[0]["severity"] == "INFO"
    assert "LEFT_SHELF" in dead[0]["value"]
    assert isinstance(dead[0]["suggested_action"], str)


def test_active_zone_not_flagged_as_dead():
    """Zone with recent activity must NOT be flagged as dead."""
    seed_event_direct("ZONE_ENTER", zone_id="RIGHT_SHELF", ts=datetime.utcnow())
    resp = client.get(f"/stores/{STORE}/anomalies")
    dead = [a for a in resp.json()["anomalies"] if a["type"] == "DEAD_ZONE"]
    zone_ids = [a["value"] for a in dead]
    assert "RIGHT_SHELF" not in zone_ids


# ─────────────────────────────────────────────────────────────────────────────
# All anomaly types have required fields
# ─────────────────────────────────────────────────────────────────────────────

def test_all_anomalies_have_required_fields():
    """Every anomaly must have type, severity, value, message, suggested_action."""
    # Seed a stale feed: old event + no recent activity (wall-clock stale)
    old_ts = datetime.utcnow() - timedelta(minutes=20)
    seed_event_direct("ENTRY", ts=old_ts)

    resp = client.get(f"/stores/{STORE}/anomalies")
    # Must have at least STALE_FEED
    anomalies = resp.json()["anomalies"]
    assert len(anomalies) >= 1
    for a in anomalies:
        assert "type" in a
        assert "severity" in a
        assert a["severity"] in ("INFO", "WARN", "CRITICAL")
        assert "value" in a
        assert "message" in a
        assert "suggested_action" in a
        assert len(a["suggested_action"]) > 0
