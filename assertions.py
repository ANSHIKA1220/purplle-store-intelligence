"""
assertions.py — 10 example test assertions the API must pass.

These mirror the kind of automated correctness checks the scoring harness runs.
Run with: python assertions.py [API_BASE_URL]

# PROMPT: Based on the challenge spec, write 10 assertion tests that an automated
# scoring harness would run against a Store Intelligence API. Cover: ingest
# idempotency, metrics structure, funnel stage ordering, health endpoint, anomaly
# response schema, heatmap normalisation, zero-purchase store, batch size limit,
# partial success on bad events, and the acceptance gate endpoint STORE_BLR_002.
#
# CHANGES MADE: Added real event seeding before metrics/funnel/heatmap assertions
# so they test actual computed values. Added STORE_BLR_002 specific assertion
# to mirror the acceptance gate check. Removed assertions that assumed specific
# numeric values (non-deterministic across environments).
"""

import sys
import uuid
import json
import requests
from datetime import datetime, timezone

BASE_URL = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8000"

STORE_ID = "STORE_BLR_002"  # The acceptance gate store ID

passed = 0
failed = 0


def assert_true(condition, name, detail=""):
    global passed, failed
    if condition:
        print(f"  ✅ PASS  {name}")
        passed += 1
    else:
        print(f"  ❌ FAIL  {name}" + (f"\n         {detail}" if detail else ""))
        failed += 1


def make_event(event_type="ENTRY", store_id=STORE_ID, visitor_id=None, zone_id=None):
    return {
        "event_id": str(uuid.uuid4()),
        "visitor_id": visitor_id or f"VIS_{uuid.uuid4().hex[:6]}",
        "store_id": store_id,
        "camera_id": "CAM_ENTRY_01",
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id": zone_id,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.91,
        "metadata": {"session_seq": 1},
    }


print(f"\nRunning assertions against {BASE_URL}\n{'─'*50}")

# ── 1. Acceptance gate: GET /stores/STORE_BLR_002/metrics returns valid JSON ──
r = requests.get(f"{BASE_URL}/stores/{STORE_ID}/metrics")
assert_true(
    r.status_code == 200 and "unique_visitors" in r.json() and "conversion_rate" in r.json(),
    "GET /stores/STORE_BLR_002/metrics returns valid JSON with required fields",
    f"status={r.status_code} body={r.text[:100]}",
)

# ── 2. POST /events/ingest accepts events without 5xx ──
event = make_event("ENTRY")
r = requests.post(f"{BASE_URL}/events/ingest", json=[event])
assert_true(
    r.status_code < 500,
    "POST /events/ingest accepts events without 5xx response",
    f"status={r.status_code}",
)

# ── 3. Idempotency: same event_id ingested twice → duplicate count ──
event2 = make_event("ENTRY")
requests.post(f"{BASE_URL}/events/ingest", json=[event2])
r2 = requests.post(f"{BASE_URL}/events/ingest", json=[event2])
data2 = r2.json()
assert_true(
    data2.get("duplicates", 0) == 1 and data2.get("ingested", 1) == 0,
    "POST /events/ingest is idempotent — same event_id ingested twice counts as duplicate",
    f"response={data2}",
)

# ── 4. Partial success: invalid event_type rejected, valid events ingested ──
good = make_event("ENTRY")
bad = {**make_event(), "event_type": "COMPLETELY_INVALID_TYPE"}
r = requests.post(f"{BASE_URL}/events/ingest", json=[good, bad])
data = r.json()
assert_true(
    data.get("ingested", 0) == 1 and data.get("rejected_count", 0) == 1,
    "POST /events/ingest partial success — valid ingested, invalid rejected in same batch",
    f"response={data}",
)

# ── 5. Batch size limit — 501 events rejected ──
events_501 = [make_event() for _ in range(501)]
r = requests.post(f"{BASE_URL}/events/ingest", json=events_501)
data = r.json()
assert_true(
    "BATCH_TOO_LARGE" in str(data),
    "POST /events/ingest rejects batch > 500 with BATCH_TOO_LARGE error",
    f"response={data}",
)

# ── 6. GET /stores/{id}/funnel returns 4 stages with required fields ──
r = requests.get(f"{BASE_URL}/stores/{STORE_ID}/funnel")
data = r.json()
stages = data.get("funnel", [])
stage_names = [s.get("stage") for s in stages]
assert_true(
    r.status_code == 200
    and len(stages) == 4
    and all(s in stage_names for s in ["entry", "zone_visit", "billing_queue", "purchase"])
    and "overall_conversion_pct" in data,
    "GET /stores/{id}/funnel returns 4 stages with counts, drop-off %, and overall_conversion_pct",
    f"stages={stage_names}",
)

# ── 7. GET /stores/{id}/heatmap returns zones with heat_score 0–100 ──
# Ingest some zone events first
zone_events = [make_event("ZONE_ENTER", zone_id="SKINCARE") for _ in range(3)]
zone_events += [make_event("ZONE_ENTER", zone_id="MAKEUP") for _ in range(2)]
requests.post(f"{BASE_URL}/events/ingest", json=zone_events)

r = requests.get(f"{BASE_URL}/stores/{STORE_ID}/heatmap")
data = r.json()
zones = data.get("zones", [])
heat_scores_valid = all(0.0 <= z.get("heat_score", -1) <= 100.0 for z in zones) if zones else True
assert_true(
    r.status_code == 200
    and "data_confidence" in data
    and heat_scores_valid,
    "GET /stores/{id}/heatmap returns zones with heat_score in [0,100] and data_confidence flag",
    f"data_confidence={data.get('data_confidence')} zones={len(zones)}",
)

# ── 8. GET /stores/{id}/anomalies returns structured anomaly objects ──
r = requests.get(f"{BASE_URL}/stores/{STORE_ID}/anomalies")
data = r.json()
anomalies = data.get("anomalies", [])
schema_ok = all(
    all(k in a for k in ["type", "severity", "message", "suggested_action"])
    for a in anomalies
) if anomalies else True
severity_ok = all(a.get("severity") in ("INFO", "WARN", "CRITICAL") for a in anomalies)
assert_true(
    r.status_code == 200
    and "anomaly_count" in data
    and schema_ok
    and severity_ok,
    "GET /stores/{id}/anomalies returns structured anomalies with type/severity/message/suggested_action",
    f"anomaly_count={data.get('anomaly_count')} schema_ok={schema_ok}",
)

# ── 9. GET /health returns db_connected, last_event_per_store, stale_feeds ──
r = requests.get(f"{BASE_URL}/health")
data = r.json()
assert_true(
    r.status_code == 200
    and data.get("db_connected") is True
    and "last_event_per_store" in data
    and "stale_feeds" in data
    and "checked_at" in data,
    "GET /health returns db_connected=true, last_event_per_store, stale_feeds, checked_at",
    f"response={data}",
)

# ── 10. Zero-purchase store returns conversion_rate=0, not crash or null ──
empty_store = f"EMPTY_STORE_{uuid.uuid4().hex[:6]}"
entry_event = make_event("ENTRY", store_id=empty_store)
requests.post(f"{BASE_URL}/events/ingest", json=[entry_event])

r = requests.get(f"{BASE_URL}/stores/{empty_store}/metrics")
data = r.json()
assert_true(
    r.status_code == 200
    and data.get("conversion_rate") == 0.0
    and data.get("unique_visitors") >= 0
    and data.get("total_revenue") == 0.0,
    "Zero-purchase store returns conversion_rate=0.0 and valid structure (no crash, no nulls)",
    f"response={data}",
)

# ── Summary ──
print(f"\n{'─'*50}")
print(f"  Result: {passed}/{passed+failed} passed")
if failed == 0:
    print("  🎉 All assertions passed!")
else:
    print(f"  ⚠️  {failed} assertion(s) failed")
    sys.exit(1)
