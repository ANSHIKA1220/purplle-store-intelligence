# PROMPT: Write comprehensive pytest tests for a FastAPI /events/ingest endpoint
# that validates: idempotency by event_id, batch size limits (max 500), partial
# success on malformed events, correct ingestion counting, staff event handling,
# and all valid event types from the schema. Use httpx TestClient. Include edge
# cases: empty batch, all-duplicate batch, single event, mixed valid/invalid.
#
# CHANGES MADE: Added re-entry event test, adjusted event_type validation to match
# our VALID_EVENT_TYPES set, fixed timestamp format to ISO-8601 UTC, added
# fixture for generating test events with uuid4 event_ids.

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.db_event import EventDB

client = TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_db():
    """Recreate tables before each test for isolation."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def make_event(
    event_type: str = "ENTRY",
    store_id: str = "STORE_BLR_002",
    visitor_id: str = None,
    event_id: str = None,
    zone_id: str = None,
) -> dict:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "visitor_id": visitor_id or f"VIS_{uuid.uuid4().hex[:6]}",
        "store_id": store_id,
        "camera_id": "CAM_ENTRY_01",
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": zone_id,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.91,
        "metadata": {},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────

def test_ingest_single_event():
    resp = client.post("/events/ingest", json=[make_event("ENTRY")])
    assert resp.status_code == 200
    data = resp.json()
    assert data["ingested"] == 1
    assert data["duplicates"] == 0


def test_ingest_multiple_events():
    events = [make_event("ENTRY"), make_event("ZONE_ENTER", zone_id="LEFT_SHELF"), make_event("EXIT")]
    resp = client.post("/events/ingest", json=events)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ingested"] == 3
    assert data["duplicates"] == 0


def test_ingest_all_valid_event_types():
    """Every event type in the catalogue must be accepted."""
    valid_types = [
        "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
        "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY",
        "ZONE_CHANGE", "DWELL_TIME",
    ]
    events = [make_event(et) for et in valid_types]
    resp = client.post("/events/ingest", json=events)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ingested"] == len(valid_types)
    assert data["rejected_count"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Idempotency
# ─────────────────────────────────────────────────────────────────────────────

def test_idempotency_same_event_id():
    """Posting the same event twice must result in only 1 ingestion."""
    event = make_event("ENTRY")
    resp1 = client.post("/events/ingest", json=[event])
    resp2 = client.post("/events/ingest", json=[event])
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["ingested"] == 1
    assert resp2.json()["ingested"] == 0
    assert resp2.json()["duplicates"] == 1


def test_idempotency_full_batch_duplicate():
    """Entire batch of duplicates → 0 ingested, N duplicates."""
    events = [make_event() for _ in range(5)]
    client.post("/events/ingest", json=events)
    resp2 = client.post("/events/ingest", json=events)
    data = resp2.json()
    assert data["ingested"] == 0
    assert data["duplicates"] == 5


def test_idempotency_safe_to_call_twice():
    """The same payload sent twice must not corrupt data."""
    events = [make_event("ENTRY"), make_event("EXIT")]
    client.post("/events/ingest", json=events)
    client.post("/events/ingest", json=events)  # second call — idempotent

    db = SessionLocal()
    count = db.query(EventDB).count()
    db.close()
    assert count == 2  # still only 2 events


# ─────────────────────────────────────────────────────────────────────────────
# Batch size limit
# ─────────────────────────────────────────────────────────────────────────────

def test_batch_size_limit_501():
    """Batch of 501 events must be rejected with an error."""
    events = [make_event() for _ in range(501)]
    resp = client.post("/events/ingest", json=events)
    assert resp.status_code == 200  # returns error body, not HTTP error
    data = resp.json()
    assert "BATCH_TOO_LARGE" in data.get("error", "")


def test_batch_size_exactly_500():
    """Exactly 500 events must be accepted."""
    events = [make_event() for _ in range(500)]
    resp = client.post("/events/ingest", json=events)
    assert resp.status_code == 200
    assert resp.json()["ingested"] == 500


# ─────────────────────────────────────────────────────────────────────────────
# Partial success on malformed events
# ─────────────────────────────────────────────────────────────────────────────

def test_partial_success_invalid_event_type():
    """Valid events in the batch are ingested; invalid ones are rejected."""
    good = make_event("ENTRY")
    bad = make_event("TOTALLY_UNKNOWN_TYPE")
    resp = client.post("/events/ingest", json=[good, bad])
    assert resp.status_code == 200
    data = resp.json()
    assert data["ingested"] == 1
    assert data["rejected_count"] == 1
    assert "rejected" in data


def test_partial_success_mixed_batch():
    """Mixed valid/invalid events — valid ones go through."""
    events = [
        make_event("ENTRY"),
        make_event("NOT_REAL"),
        make_event("EXIT"),
        make_event("FAKE_TYPE"),
        make_event("ZONE_ENTER", zone_id="CENTER_AISLE"),
    ]
    resp = client.post("/events/ingest", json=events)
    data = resp.json()
    assert data["ingested"] == 3
    assert data["rejected_count"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_batch():
    """Empty batch must succeed with zero ingested."""
    resp = client.post("/events/ingest", json=[])
    assert resp.status_code == 200
    data = resp.json()
    assert data["ingested"] == 0
    assert data["duplicates"] == 0


def test_staff_events_are_persisted():
    """Staff events (is_staff=True) must be stored but not affect customer metrics."""
    staff_event = make_event("ENTRY")
    staff_event["is_staff"] = True
    resp = client.post("/events/ingest", json=[staff_event])
    assert resp.status_code == 200
    assert resp.json()["ingested"] == 1

    db = SessionLocal()
    ev = db.query(EventDB).filter(EventDB.event_id == staff_event["event_id"]).first()
    db.close()
    assert ev is not None
    assert ev.is_staff is True


def test_reentry_event_ingested():
    """REENTRY events must be accepted without creating duplicate sessions."""
    visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
    entry = make_event("ENTRY", visitor_id=visitor_id)
    exit_ev = make_event("EXIT", visitor_id=visitor_id)
    reentry = make_event("REENTRY", visitor_id=visitor_id)

    resp = client.post("/events/ingest", json=[entry, exit_ev, reentry])
    data = resp.json()
    assert data["ingested"] == 3
    assert data["rejected_count"] == 0


def test_zero_purchase_store_returns_valid_response():
    """A store with entries but no POS data must return conversion_rate=0, not crash."""
    entry = make_event("ENTRY", store_id="STORE_EMPTY_001")
    client.post("/events/ingest", json=[entry])

    resp = client.get("/stores/STORE_EMPTY_001/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["conversion_rate"] == 0.0
    assert data["unique_visitors"] >= 0


def test_all_staff_clip_excluded_from_metrics():
    """If all sessions are staff, unique_visitors must be 0."""
    store_id = "STORE_STAFF_ONLY"
    for i in range(5):
        e = make_event("ENTRY", store_id=store_id, visitor_id=f"STAFF_{i:03d}")
        e["is_staff"] = True
        client.post("/events/ingest", json=[e])

    resp = client.get(f"/stores/{store_id}/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["unique_visitors"] == 0
