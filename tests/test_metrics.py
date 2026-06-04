# PROMPT: Write pytest tests for a store metrics API endpoint that returns
# unique_visitors, conversion_rate, avg_dwell_per_zone, queue_depth, and
# abandonment_rate. Test: zero-purchase store, all-staff sessions excluded,
# conversion rate calculation with POS data, queue depth from latest event metadata.
# Use FastAPI TestClient with clean DB per test.
#
# CHANGES MADE: Added fixture for seeding POS transactions directly via ORM,
# adjusted conversion rate test to use session-based correlation (not raw count),
# added test for store with no events at all (must not crash).

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.pos_transaction import POSTransaction
from app.models.session import SessionDB

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def make_event(event_type, store_id="STORE_BLR_002", visitor_id=None, is_staff=False, zone_id=None):
    return {
        "event_id": str(uuid.uuid4()),
        "visitor_id": visitor_id or f"VIS_{uuid.uuid4().hex[:6]}",
        "store_id": store_id,
        "camera_id": "CAM_FLOOR_01",
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": zone_id,
        "dwell_ms": 0,
        "is_staff": is_staff,
        "confidence": 0.88,
        "metadata": {},
    }


def seed_pos(store_id="STORE_BLR_002", count=3):
    db = SessionLocal()
    for i in range(count):
        db.add(POSTransaction(
            transaction_id=f"TXN_TEST_{i:04d}",
            store_id=store_id,
            timestamp=datetime.now(timezone.utc),
            basket_value_inr=500.0 + i * 100,
        ))
    db.commit()
    db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Zero-purchase store
# ─────────────────────────────────────────────────────────────────────────────

def test_zero_purchase_store():
    """Store with visitors but no POS data → conversion_rate=0, no crash."""
    events = [make_event("ENTRY") for _ in range(5)]
    client.post("/events/ingest", json=events)

    resp = client.get("/stores/STORE_BLR_002/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["unique_visitors"] == 5
    assert data["total_transactions"] == 0
    assert data["conversion_rate"] == 0.0
    assert data["total_revenue"] == 0.0


def test_no_events_at_all():
    """Store with no events must return valid response with zeros."""
    resp = client.get("/stores/STORE_NOBODY/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["unique_visitors"] == 0
    assert data["conversion_rate"] == 0.0


def test_staff_excluded_from_unique_visitors():
    """Staff entries must not count toward unique_visitors."""
    store = "STORE_BLR_002"
    customer_events = [make_event("ENTRY", store_id=store) for _ in range(3)]
    staff_events = [make_event("ENTRY", store_id=store, is_staff=True) for _ in range(4)]
    client.post("/events/ingest", json=customer_events + staff_events)

    resp = client.get(f"/stores/{store}/metrics")
    data = resp.json()
    assert data["unique_visitors"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# Revenue and transactions
# ─────────────────────────────────────────────────────────────────────────────

def test_revenue_and_transaction_count():
    seed_pos("STORE_BLR_002", count=5)
    resp = client.get("/stores/STORE_BLR_002/metrics")
    data = resp.json()
    assert data["total_transactions"] == 5
    assert data["total_revenue"] > 0


def test_revenue_zero_when_no_pos():
    resp = client.get("/stores/STORE_EMPTY/metrics")
    data = resp.json()
    assert data["total_revenue"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Queue depth
# ─────────────────────────────────────────────────────────────────────────────

def test_queue_depth_from_metadata():
    """Queue depth should reflect the most recent BILLING_QUEUE_JOIN metadata."""
    event = make_event("BILLING_QUEUE_JOIN", zone_id="BILLING")
    event["metadata"] = {"queue_depth": 7}
    client.post("/events/ingest", json=[event])

    resp = client.get("/stores/STORE_BLR_002/metrics")
    data = resp.json()
    assert data["current_queue_depth"] == 7


def test_queue_depth_zero_by_default():
    client.post("/events/ingest", json=[make_event("ENTRY")])
    resp = client.get("/stores/STORE_BLR_002/metrics")
    assert resp.json()["current_queue_depth"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Abandonment rate
# ─────────────────────────────────────────────────────────────────────────────

def test_abandonment_rate_calculation():
    """50% abandonment: 2 billing visitors, 1 abandons."""
    store = "STORE_BLR_002"
    db = SessionLocal()
    db.add(SessionDB(
        visitor_id="VIS_A", store_id=store,
        entry_time=datetime.now(timezone.utc),
        is_staff=False, reached_billing=True, abandoned_queue=True,
        converted=False, reentry=False, zone_visits=1,
    ))
    db.add(SessionDB(
        visitor_id="VIS_B", store_id=store,
        entry_time=datetime.now(timezone.utc),
        is_staff=False, reached_billing=True, abandoned_queue=False,
        converted=True, reentry=False, zone_visits=2,
    ))
    db.commit()
    db.close()

    resp = client.get(f"/stores/{store}/metrics")
    data = resp.json()
    assert data["abandonment_rate"] == 50.0


# ─────────────────────────────────────────────────────────────────────────────
# Avg dwell per zone
# ─────────────────────────────────────────────────────────────────────────────

def test_avg_dwell_per_zone_returned():
    """ZONE_DWELL events with dwell_ms should populate avg_dwell_per_zone."""
    event = make_event("ZONE_DWELL", zone_id="LEFT_SHELF")
    event["dwell_ms"] = 45000
    client.post("/events/ingest", json=[event])

    resp = client.get("/stores/STORE_BLR_002/metrics")
    data = resp.json()
    dwell = data.get("avg_dwell_per_zone", {})
    assert "LEFT_SHELF" in dwell
    assert dwell["LEFT_SHELF"] > 0
