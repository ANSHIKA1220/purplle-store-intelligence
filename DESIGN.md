# Store Intelligence — System Design

## Overview

This system transforms raw CCTV footage from Apex Retail stores into real-time business intelligence. Starting from anonymised video clips, it detects people, tracks their movement, emits structured behavioural events, and exposes a queryable analytics API backed by a Streamlit dashboard.

The architecture has four layers:

```
CCTV Footage
     │
     ▼
Detection Layer (YOLOv8 + ByteTrack)
     │  run_pipeline.py
     ▼
Event Stream (structured JSON events)
     │  POST /events/ingest
     ▼
Intelligence API (FastAPI + SQLite)
     │  /metrics, /funnel, /heatmap, /anomalies, /health
     ▼
Live Dashboard (Streamlit, 10s auto-refresh)
```

---

## Stage 1 — Detection Layer

**Model**: YOLOv8n (nano). Chosen for its inference speed on CPU-accessible hardware without sacrificing meaningful accuracy for person detection. The nano variant runs at 30+ FPS on a modern laptop CPU — acceptable for the 15fps source footage.

**Tracker**: ByteTrack (via Supervision). Maintains track IDs across frames using high-confidence and low-confidence detection pools. `lost_track_buffer=90` (3 seconds at 30fps) handles brief occlusions behind shelving — a deliberate choice for the retail context where a person may briefly disappear behind a display.

**Entry detection**: A virtual horizontal line at 60% of frame height. Upward crossings (y decreasing) = EXIT, downward crossings (y increasing) = ENTRY. This simple line-crossing approach is robust against the lighting variation in the clips because it operates on tracker centre coordinates, not raw pixel values.

**Zone detection**: `cv2.pointPolygonTest` on bounding box centre coordinates against zone polygons defined in `store_layout.json`. Each zone is a convex polygon defined in pixel space calibrated to the camera's field of view.

**Staff heuristic**: Bounding boxes whose height exceeds 85% of the frame height are flagged `is_staff=True`. Staff tend to stand close to cameras (e.g. behind counters) producing anomalously tall bounding boxes relative to customers further from the camera. This is a geometric heuristic — a production deployment would use a classifier on uniform colour/pattern, but this works reliably for the provided footage.

**Re-entry**: The `SessionManager` retains `visitor_id → exit_time` for 5 minutes after a session closes. When the same tracker ID reappears, it checks this window. A reappearance within 5 minutes emits a `REENTRY` event rather than a new `ENTRY`, avoiding inflation of the unique visitor count.

**Group entry**: ByteTrack assigns distinct track IDs to each person even when they enter simultaneously, so three people entering together produce three `ENTRY` events. The detection confidence may be lower for occluded individuals — these are still emitted with their actual confidence value rather than being suppressed.

**Confidence handling**: Low-confidence detections (below `DETECTION_CONFIDENCE=0.25`) are not forwarded to ByteTrack at all (YOLO's internal threshold). Between 0.25 and 0.9, events are emitted with their real confidence value. The downstream API stores this value and the `/heatmap` endpoint uses the `data_confidence` flag (LOW if <20 sessions) to signal statistical uncertainty — separate from per-event confidence.

---

## Stage 2 — Event Schema

Events are emitted as JSON conforming to the challenge schema. The `event_id` is a UUID4 generated at emission time. The `timestamp` is derived from `clip_start_time + frame_number / fps`, converting the clip's relative frame offset to an absolute ISO-8601 UTC timestamp.

The `metadata.session_seq` field is an incrementing counter per visitor session — useful for reconstructing session timelines from an unordered event stream.

---

## Stage 3 — Intelligence API

Built with **FastAPI** and **SQLAlchemy** on **SQLite**.

### Ingest (`POST /events/ingest`)
Idempotent by `event_id` — duplicate events are detected with a primary-key lookup before insert. Batch validation separates malformed events (unknown `event_type`) from valid ones; the valid portion is committed even if some events in the batch are rejected. The response includes `rejected` details for each invalid event.

### Session building
`session_service.process_event()` is called on every ingested event and maintains the `sessions` table. ENTRY opens a session, EXIT closes it and computes `dwell_seconds`, ZONE_ENTER/ZONE_CHANGE increments `zone_visits`, BILLING_QUEUE_JOIN/ABANDON sets the billing flags. The session table is the authoritative source for funnel and metrics computation.

### Metrics (`GET /stores/{id}/metrics`)
`unique_visitors` counts distinct non-staff sessions. Conversion is computed via POS time-window correlation: a visitor is converted if they were in the `BILLING` zone within 5 minutes before any POS transaction timestamp for the store. This avoids the false assumption that transactions / visitors = conversion rate (a store could have repeat purchasers).

### Funnel (`GET /stores/{id}/funnel`)
Session-based, not event-count-based. Re-entries do not create new sessions (same `visitor_id`). Staff sessions are excluded at every stage. Drop-off % at each stage is `(1 - stage_n / stage_n-1) * 100`.

### Heatmap (`GET /stores/{id}/heatmap`)
Zone visit counts are normalised to 0–100 relative to the busiest zone. Average dwell is in milliseconds from `ZONE_DWELL` / `DWELL_TIME` events. The `data_confidence: LOW` flag is set when total sessions < 20 — below this the averages are statistically unreliable.

### Anomalies (`GET /stores/{id}/anomalies`)
Five anomaly types: `BILLING_QUEUE_SPIKE`, `HIGH_ABANDONMENT`, `CONVERSION_DROP`, `DEAD_ZONE`, `STALE_FEED`. Each includes a `suggested_action` string — operationally useful for an on-call engineer. The severity tiers (INFO / WARN / CRITICAL) map to escalation levels.

### Health (`GET /health`)
Checks DB connectivity and computes per-store event staleness. Returns `STALE_FEED` warnings for stores with no events in the last 10 minutes. This is the first endpoint an on-call engineer checks — it answers "is the system running?" not "are metrics interesting?".

---

## Stage 4 — Live Dashboard

Streamlit with a 10-second auto-refresh (`time.sleep(10)` + `st.rerun()`). Shows:
- KPI row: visitors, revenue, transactions, conversion rate, queue depth, abandonment rate
- Funnel bar chart + table with drop-off %
- Heatmap bar chart with heat scores per zone
- Active anomalies expandable by severity
- CV pipeline event counts (entries, exits, zone changes, dwell events)

The store ID is configurable via sidebar input — not hard-coded.

---

## Production Readiness

**Structured logging**: Every request logs `trace_id`, `store_id`, `endpoint`, `method`, `latency_ms`, `status_code`. Ingest additionally logs `event_count`. The `X-Trace-Id` header is returned on every response for client-side correlation.

**Graceful degradation**: `OperationalError` (DB unavailable) is caught by a FastAPI exception handler that returns HTTP 503 with a structured JSON body — no raw stack traces. A generic 500 handler provides the same protection for unexpected exceptions.

**Idempotency**: Verified by primary-key lookup on `event_id` before every insert. Tests confirm that posting the same payload twice produces the same DB state.

**Containerisation**: `docker-compose.yml` defines `api` and `dashboard` services. `Dockerfile` installs system dependencies for OpenCV headless mode. `HEALTHCHECK` uses `curl /health`.

---

## AI-Assisted Decisions

### 1. Event schema — `metadata.session_seq` field

I asked Claude: *"In a distributed event stream from multiple cameras, how would you reconstruct session timelines if events arrive out of order?"*

Claude suggested using a monotonic per-session sequence number emitted at generation time rather than relying on timestamps (which can have clock skew between cameras). I agreed and implemented `session_seq` as an incrementing counter in the `EventGenerator`. This was not in the original schema I drafted — the AI caught a real ordering problem.

### 2. Conversion rate calculation

My initial implementation divided `total_transactions / unique_visitors`. Claude flagged that this is wrong: a store could have 5 transactions but 50 visitors, giving 10% — but it could also mean 5 repeat purchasers and 45 non-buyers, or 5 unique buyers. The correct approach is to correlate individual sessions to transactions using a time window. I implemented the 5-minute billing-zone correlation method from the problem statement based on this feedback, overriding my original simpler approach.

### 3. Staff detection heuristic

I asked Claude: *"Without a separate staff classifier, what geometric properties of YOLO bounding boxes distinguish staff from customers in a retail setting?"*

Claude proposed several options: bounding box height ratio, aspect ratio, position in frame (staff tend to be near counter edges), and trajectory (staff move in systematic patterns vs random customer browsing). I implemented the bounding box height ratio heuristic (`bbox_height > 85% of frame height`) because it's fast, requires no extra model, and handles the primary case (staff standing behind counters close to the camera). I did not implement trajectory-based classification because it would require multi-frame state and is over-engineering for the time window.

---

## Known Limitations and Trade-offs

- **SQLite**: Adequate for a single-store demo and correctness evaluation. At 40 stores × 15fps × 3 cameras, the write rate would exceed SQLite's safe concurrency limit. PostgreSQL would be the production choice.
- **Synchronous ingest**: FastAPI processes each ingest batch synchronously. Under high event volume, an async queue (Redis + background worker) would be needed.
- **No cross-camera re-ID**: The same person on the floor camera and the entry camera cannot be definitively linked without an OSNet re-ID model. Current implementation trusts that events from separate cameras produce separate sessions (acceptable for accuracy evaluation within a single camera's view).
- **Staff classification**: The bounding-box height heuristic produces false positives for tall customers near the camera. A production system would use a clothing colour/pattern classifier fine-tuned on store-specific uniform images.
