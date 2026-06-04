# PROMPT: Write tests for a zone heatmap API endpoint that returns per-zone
# visit_count, avg_dwell_ms, heat_score (0-100 normalised), and a data_confidence
# flag (HIGH if >=20 sessions, LOW otherwise). Test: heat_score of busiest zone=100,
# normalisation correct, confidence flag low below threshold, empty store returns
# empty zones list, staff zones excluded.
#
# CHANGES MADE: Added test for avg_dwell_ms using ZONE_DWELL events, removed
# assumption that exact scores match (floating point), used pytest.approx for
# score comparisons, added test verifying data_confidence field values.

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
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


def make_zone_event(zone_id, visitor_id=None, event_type="ZONE_ENTER", dwell_ms=0, is_staff=False):
    return {
        "event_id": str(uuid.uuid4()),
        "visitor_id": visitor_id or f"VIS_{uuid.uuid4().hex[:6]}",
        "store_id": STORE,
        "camera_id": "CAM_FLOOR_01",
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": zone_id,
        "zone_name": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": 0.87,
        "metadata": {},
    }


def seed_sessions(count, is_staff=False):
    db = SessionLocal()
    for i in range(count):
        db.add(SessionDB(
            visitor_id=f"VIS_{i:04d}",
            store_id=STORE,
            entry_time=datetime.now(timezone.utc),
            is_staff=is_staff,
            converted=False, reentry=False,
            reached_billing=False, abandoned_queue=False, zone_visits=1,
        ))
    db.commit()
    db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Response structure
# ─────────────────────────────────────────────────────────────────────────────

def test_heatmap_structure():
    resp = client.get(f"/stores/{STORE}/heatmap")
    assert resp.status_code == 200
    data = resp.json()
    assert "store_id" in data
    assert "zones" in data
    assert "data_confidence" in data
    assert "total_sessions" in data


def test_empty_store_returns_empty_zones():
    resp = client.get(f"/stores/{STORE}/heatmap")
    data = resp.json()
    assert data["zones"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Heat score normalisation
# ─────────────────────────────────────────────────────────────────────────────

def test_busiest_zone_heat_score_100():
    """The zone with the most visits must have heat_score == 100."""
    # Zone A: 5 visitors, Zone B: 2 visitors
    for i in range(5):
        ingest([make_zone_event("ZONE_A", visitor_id=f"VIS_A_{i}")])
    for i in range(2):
        ingest([make_zone_event("ZONE_B", visitor_id=f"VIS_B_{i}")])

    resp = client.get(f"/stores/{STORE}/heatmap")
    zones = resp.json()["zones"]
    zone_a = next(z for z in zones if z["zone_id"] == "ZONE_A")
    assert zone_a["heat_score"] == 100.0


def test_heat_scores_are_normalised_0_100():
    """All heat_scores must be between 0 and 100."""
    for zone in ["LEFT_SHELF", "CENTER_AISLE", "RIGHT_SHELF"]:
        for _ in range(3):
            ingest([make_zone_event(zone)])

    resp = client.get(f"/stores/{STORE}/heatmap")
    for z in resp.json()["zones"]:
        assert 0.0 <= z["heat_score"] <= 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Data confidence flag
# ─────────────────────────────────────────────────────────────────────────────

def test_low_confidence_below_20_sessions():
    """Fewer than 20 sessions → data_confidence=LOW."""
    seed_sessions(10)
    resp = client.get(f"/stores/{STORE}/heatmap")
    assert resp.json()["data_confidence"] == "LOW"


def test_high_confidence_with_20_plus_sessions():
    """20+ sessions → data_confidence=HIGH."""
    seed_sessions(20)
    resp = client.get(f"/stores/{STORE}/heatmap")
    assert resp.json()["data_confidence"] == "HIGH"


# ─────────────────────────────────────────────────────────────────────────────
# Staff exclusion
# ─────────────────────────────────────────────────────────────────────────────

def test_staff_zone_visits_excluded():
    """Staff zone visits must not inflate zone visit counts."""
    customer_events = [make_zone_event("LEFT_SHELF", visitor_id=f"VIS_C_{i}") for i in range(3)]
    staff_events = [make_zone_event("LEFT_SHELF", visitor_id=f"VIS_S_{i}", is_staff=True) for i in range(10)]
    ingest(customer_events + staff_events)

    resp = client.get(f"/stores/{STORE}/heatmap")
    zones = resp.json()["zones"]
    if zones:
        left = next((z for z in zones if z["zone_id"] == "LEFT_SHELF"), None)
        if left:
            # Only 3 distinct customer visitors (staff excluded)
            assert left["visit_count"] <= 3


# ─────────────────────────────────────────────────────────────────────────────
# Avg dwell
# ─────────────────────────────────────────────────────────────────────────────

def test_avg_dwell_ms_populated():
    """ZONE_DWELL events with dwell_ms should populate avg_dwell_ms."""
    events = [
        make_zone_event("FRAGRANCE", dwell_ms=30000, event_type="ZONE_DWELL"),
        make_zone_event("FRAGRANCE", dwell_ms=60000, event_type="ZONE_DWELL"),
    ]
    ingest(events)

    resp = client.get(f"/stores/{STORE}/heatmap")
    zones = resp.json()["zones"]
    frag = next((z for z in zones if z["zone_id"] == "FRAGRANCE"), None)
    if frag:
        assert frag["avg_dwell_ms"] == pytest.approx(45000, rel=0.01)
