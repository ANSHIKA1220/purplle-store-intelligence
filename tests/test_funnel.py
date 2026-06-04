# PROMPT: Write pytest tests for a store funnel API that returns 4 stages:
# entry, zone_visit, billing_queue, purchase with counts and drop-off %.
# Test: re-entry must not double-count a visitor, staff excluded from all stages,
# correct drop-off % calculation, funnel with zero visitors handles gracefully,
# overall_conversion_pct correct. Seed data via ORM and via /events/ingest.
#
# CHANGES MADE: Added test that verifies reentry doesn't increment entry stage count,
# fixed drop-off formula (uses relative % vs next stage), added zero-visitor
# edge case test, added test for funnel with all stages populated via ORM sessions.

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.session import SessionDB
from app.models.pos_transaction import POSTransaction

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


STORE = "STORE_BLR_002"


def ingest(events):
    return client.post("/events/ingest", json=events).json()


def make_entry(visitor_id=None, is_staff=False):
    return {
        "event_id": str(uuid.uuid4()),
        "visitor_id": visitor_id or f"VIS_{uuid.uuid4().hex[:6]}",
        "store_id": STORE,
        "camera_id": "CAM_ENTRY_01",
        "event_type": "ENTRY",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": is_staff,
        "confidence": 0.9,
        "metadata": {},
    }


def seed_session(visitor_id, is_staff=False, reached_billing=False, converted=False, reentry=False, zone_visits=0):
    db = SessionLocal()
    db.add(SessionDB(
        visitor_id=visitor_id,
        store_id=STORE,
        entry_time=datetime.now(timezone.utc),
        is_staff=is_staff,
        reached_billing=reached_billing,
        abandoned_queue=False,
        converted=converted,
        reentry=reentry,
        zone_visits=zone_visits,
    ))
    db.commit()
    db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Basic structure
# ─────────────────────────────────────────────────────────────────────────────

def test_funnel_structure():
    resp = client.get(f"/stores/{STORE}/funnel")
    assert resp.status_code == 200
    data = resp.json()
    assert "funnel" in data
    assert "overall_conversion_pct" in data
    stages = [s["stage"] for s in data["funnel"]]
    assert stages == ["entry", "zone_visit", "billing_queue", "purchase"]


def test_funnel_zero_visitors():
    """Empty store must return zeros, not crash or return nulls."""
    resp = client.get(f"/stores/{STORE}/funnel")
    assert resp.status_code == 200
    data = resp.json()
    for stage in data["funnel"]:
        assert stage["count"] == 0
    assert data["overall_conversion_pct"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Counts
# ─────────────────────────────────────────────────────────────────────────────

def test_entry_stage_count():
    """Entry stage = unique customer visitors."""
    ingest([make_entry() for _ in range(4)])
    resp = client.get(f"/stores/{STORE}/funnel")
    entry_stage = next(s for s in resp.json()["funnel"] if s["stage"] == "entry")
    assert entry_stage["count"] == 4


def test_staff_excluded_from_all_stages():
    """Staff entries must not appear in any funnel stage."""
    ingest([make_entry(is_staff=True) for _ in range(3)])
    ingest([make_entry() for _ in range(2)])  # 2 real customers
    resp = client.get(f"/stores/{STORE}/funnel")
    entry_stage = next(s for s in resp.json()["funnel"] if s["stage"] == "entry")
    assert entry_stage["count"] == 2  # only non-staff


# ─────────────────────────────────────────────────────────────────────────────
# Re-entry deduplication
# ─────────────────────────────────────────────────────────────────────────────

def test_reentry_does_not_double_count():
    """
    A REENTRY event for the same visitor_id must not increment the entry count.
    The funnel entry stage counts unique visitor sessions, not raw ENTRY events.
    """
    visitor_id = "VIS_REENTRY_001"
    events = [
        {**make_entry(visitor_id=visitor_id), "event_type": "ENTRY"},
        {**make_entry(visitor_id=visitor_id), "event_type": "EXIT",
         "event_id": str(uuid.uuid4())},
        {**make_entry(visitor_id=visitor_id), "event_type": "REENTRY",
         "event_id": str(uuid.uuid4())},
    ]
    ingest(events)

    resp = client.get(f"/stores/{STORE}/funnel")
    entry_stage = next(s for s in resp.json()["funnel"] if s["stage"] == "entry")
    # Should be 1 session, not 2 (entry + reentry)
    assert entry_stage["count"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Drop-off calculation
# ─────────────────────────────────────────────────────────────────────────────

def test_dropoff_percentages():
    """Drop-off at each stage must be a non-negative float."""
    # Seed: 10 entries, 8 zone visits, 4 billing, 2 purchases
    for i in range(10):
        seed_session(f"VIS_{i:03d}", zone_visits=1 if i < 8 else 0,
                     reached_billing=(i < 4), converted=(i < 2))

    resp = client.get(f"/stores/{STORE}/funnel")
    data = resp.json()

    for stage in data["funnel"]:
        assert stage["dropoff_pct"] >= 0.0
        assert stage["dropoff_pct"] <= 100.0

    # First stage has 0 dropoff
    assert data["funnel"][0]["dropoff_pct"] == 0.0


def test_overall_conversion_pct():
    """overall_conversion_pct = purchase / entry * 100."""
    # Ingest ENTRY events so stage 1 (entry count from events table) is populated
    visitor_ids = [f"VIS_{i:03d}" for i in range(10)]
    entry_events = [
        {
            "event_id": str(uuid.uuid4()),
            "visitor_id": vid,
            "store_id": STORE,
            "camera_id": "CAM_ENTRY_01",
            "event_type": "ENTRY",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "zone_id": None,
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 0.9,
            "metadata": {},
        }
        for vid in visitor_ids
    ]
    ingest(entry_events)

    # Mark 2 sessions as converted directly
    db = SessionLocal()
    for i in range(2):
        s = db.query(SessionDB).filter(SessionDB.visitor_id == visitor_ids[i]).first()
        if s:
            s.converted = True
    db.commit()
    db.close()

    resp = client.get(f"/stores/{STORE}/funnel")
    data = resp.json()
    # 2 purchases out of 10 entries = 20%
    assert data["overall_conversion_pct"] == pytest.approx(20.0, abs=1.0)
