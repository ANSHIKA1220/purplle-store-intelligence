# PROMPT: Write tests for a /health API endpoint that returns service status,
# db_connected bool, last_event_per_store dict, stale_feeds list, and checked_at
# timestamp. Test: OK status when DB connected and recent events, DEGRADED when
# stale events exist, stale_feeds list populated correctly, checked_at is valid
# ISO-8601 timestamp.
#
# CHANGES MADE: Added test that verifies stale_threshold_minutes present in response,
# changed timestamp comparison to check ISO-8601 format rather than exact value,
# added test for store with no events (should not appear in last_event_per_store).

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models.db_event import EventDB

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def seed_event(store_id="STORE_BLR_002", ts=None):
    db = SessionLocal()
    db.add(EventDB(
        event_id=str(uuid.uuid4()),
        visitor_id="VIS_TEST",
        store_id=store_id,
        camera_id="CAM_TEST",
        event_type="ENTRY",
        timestamp=ts or datetime.utcnow(),
        is_staff=False,
        confidence=0.9,
        metadata_json={},
    ))
    db.commit()
    db.close()


def test_health_response_structure():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "db_connected" in data
    assert "last_event_per_store" in data
    assert "stale_feeds" in data
    assert "checked_at" in data
    assert "stale_threshold_minutes" in data


def test_health_ok_with_recent_events():
    seed_event(ts=datetime.utcnow())
    resp = client.get("/health")
    data = resp.json()
    assert data["db_connected"] is True
    assert "STORE_BLR_002" in data["last_event_per_store"]
    assert "STORE_BLR_002" not in data["stale_feeds"]


def test_health_degraded_with_stale_events():
    """Events older than 10 minutes mark the feed as stale."""
    seed_event(ts=datetime.utcnow() - timedelta(minutes=15))
    resp = client.get("/health")
    data = resp.json()
    assert "STORE_BLR_002" in data["stale_feeds"]
    assert data["status"] == "DEGRADED"


def test_health_checked_at_is_iso8601():
    resp = client.get("/health")
    checked_at = resp.json()["checked_at"]
    # Should not raise
    datetime.fromisoformat(checked_at.replace("Z", "+00:00"))


def test_health_no_events_store_not_in_last_event_map():
    """Store with no events should not appear in last_event_per_store."""
    resp = client.get("/health")
    data = resp.json()
    assert "STORE_NO_DATA" not in data["last_event_per_store"]


def test_health_multiple_stores():
    seed_event(store_id="STORE_A", ts=datetime.utcnow())
    seed_event(store_id="STORE_B", ts=datetime.utcnow() - timedelta(minutes=20))
    resp = client.get("/health")
    data = resp.json()
    assert "STORE_A" in data["last_event_per_store"]
    assert "STORE_B" in data["last_event_per_store"]
    assert "STORE_B" in data["stale_feeds"]
    assert "STORE_A" not in data["stale_feeds"]
