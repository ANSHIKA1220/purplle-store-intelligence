# Technical Choices — Store Intelligence

## Decision 1: Detection Model — YOLOv8n

### Options Considered

| Option | Pros | Cons |
|--------|------|------|
| YOLOv8n (chosen) | Fast on CPU, good person detection, Supervision integration | Lower accuracy than larger variants |
| YOLOv8m / YOLOv8l | Higher accuracy | 3–10× slower, too slow for real-time on typical hardware |
| RT-DETR | Strong accuracy, transformer-based | No ByteTrack integration in Supervision at time of writing |
| MediaPipe Pose | Fast, works on mobile | Not designed for multi-person tracking; struggles with occlusion |
| GPT-4V / Gemini Vision | Could describe scenes semantically | Per-frame API call cost is prohibitive; latency 2–10s per frame |

### What AI Suggested

I asked Claude to compare YOLOv8 variants for retail CV. It highlighted that YOLOv8n runs at 80+ FPS on a mid-range CPU while YOLOv8m runs at ~25 FPS — below the 30fps frame rate of the source clips. It also noted that ByteTrack is natively integrated with Supervision for YOLOv8, reducing integration friction.

For the staff detection sub-problem, Claude suggested using a VLM (GPT-4V) to classify each detected person's clothing as "uniform" or "customer" via a sliding-window prompt. I evaluated this but rejected it: the API latency (2–8 seconds per person crop) makes it unusable for a 15fps pipeline processing 1,200 frames/minute. The geometric bounding-box heuristic I implemented is a deliberate trade-off: less accurate, but runs in microseconds.

### What I Chose and Why

**YOLOv8n** with ByteTrack.

The 80ms inference time on CPU is compatible with near-real-time processing of 15fps footage (66ms/frame budget). For an engineering hiring challenge, runtime correctness matters more than maximally high mAP. If the system were productionised with GPU inference, I would upgrade to YOLOv8m or YOLOv8l for the accuracy gain.

I set `DETECTION_CONFIDENCE=0.25` (lower than the default 0.5) deliberately. The problem statement says "confidence calibration — low-confidence detections should be flagged, not dropped." Suppressing 0.25–0.5 confidence detections would undercount visitors in crowded or partially occluded scenes. The confidence value is stored on each event.

---

## Decision 2: Event Schema Design

### Options Considered

**Option A**: Minimal schema — just `event_type`, `visitor_id`, `timestamp`. Simple but loses spatial and session context that the API needs.

**Option B**: Full challenge schema — `event_id`, `visitor_id`, `store_id`, `camera_id`, `event_type`, `timestamp`, `zone_id`, `dwell_ms`, `is_staff`, `confidence`, `metadata`. Matches the problem statement exactly.

**Option C**: Kafka-style envelope with a `payload` blob. Flexible but the API would need custom parsing for every event type. Premature abstraction.

### What AI Suggested

Claude reviewed my initial schema draft and flagged two gaps:

1. **`metadata.session_seq`**: My draft did not include a sequence number. Claude noted that in a distributed multi-camera setup, events from different cameras for the same visitor could arrive out of order. A per-session monotonic counter allows reconstructing the correct session timeline without relying on timestamp ordering (which has clock-skew risk between cameras). I added `session_seq` to every event's metadata.

2. **`dwell_ms` vs `dwell_seconds`**: I initially used floating-point seconds. Claude pointed out that the problem statement explicitly uses `dwell_ms` (milliseconds as integer), which avoids floating-point precision issues in database storage and JSON serialisation. I agreed and standardised on milliseconds throughout.

### What I Chose and Why

I implemented **Option B** — the full challenge schema — exactly as specified, plus `session_seq` in metadata based on Claude's suggestion.

One deliberate extension: `zone_name` alongside `zone_id`. `zone_id` is the key (e.g. `LEFT_SHELF`) and `zone_name` is the display label (same value in this implementation, but allows localisation or renaming without changing the key). The funnel and heatmap queries join on `zone_id` for correctness but display `zone_name` to the user.

I did NOT add a `track_id` → `visitor_id` mapping table in the API layer. The pipeline assigns `visitor_id = f"VIS_{track_id:06d}"` at generation time. This is deterministic and avoids an extra DB lookup on every event. The trade-off is that tracker ID reuse across sessions (ByteTrack recycles IDs) could cause visitor_id collisions — mitigated by the REENTRY detection window (5 minutes) in `SessionManager`.

---

## Decision 3: API Architecture — Synchronous FastAPI with SQLite

### Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Synchronous FastAPI + SQLite (chosen) | Zero infrastructure, easy to `docker compose up`, fast to evaluate | Not horizontally scalable; SQLite write-lock under concurrent load |
| Async FastAPI + PostgreSQL | Production-grade; handles 40 stores concurrently | Requires a Postgres container, more complex setup |
| FastAPI + Redis Queue + Background Worker | True streaming; ingest is non-blocking | 3 services to coordinate; overkill for correctness evaluation |
| Django + DRF | Batteries-included ORM | Heavier; less performant for API-first design |

### What AI Suggested

I asked Claude: *"For a hackathon submission that needs to run via `docker compose up` on a judge's laptop, what are the trade-offs between SQLite and PostgreSQL?"*

Claude's response: use SQLite unless the scoring test sends concurrent requests — SQLite serialises writes via a single write lock, which would cause timeouts under parallel load. For sequential test execution (typical for automated scoring harnesses), SQLite performs identically to Postgres.

Claude also suggested using SQLAlchemy's `check_same_thread=False` for the SQLite engine — standard for FastAPI use — which I already had. It additionally flagged that `Base.metadata.create_all()` at startup is acceptable for SQLite but should be replaced with Alembic migrations in production.

### What I Chose and Why

**Synchronous FastAPI + SQLite**.

For this challenge the acceptance gate is `docker compose up` — a judge's first action. Adding a Postgres container increases setup friction (init scripts, volume mounts, wait-for-healthy logic). SQLite satisfies correctness requirements for the scoring test suite and requires zero external processes.

The production path is straightforward: swap `sqlite:///./store_intelligence.db` for a `DATABASE_URL` environment variable pointing at Postgres, and the SQLAlchemy layer handles the rest. This is documented in `DESIGN.md`.

I did override Claude's implicit suggestion to add async endpoint handlers. FastAPI supports async `def` endpoints for non-blocking I/O, but SQLAlchemy's synchronous ORM would block the event loop anyway without `run_in_executor`. The complexity cost exceeds the benefit for a single-server deployment.

**One place I disagreed with AI**: Claude suggested adding Redis caching for the `/metrics` endpoint to avoid repeated SQL aggregations. I rejected this — the problem statement says metrics must be "real-time, not cached from yesterday." Adding a Redis TTL cache would violate that requirement. The SQLite queries are fast enough (sub-10ms) for the expected event volume.

---

## Summary Table

| Decision | What AI Suggested | What I Built | Reason for Deviation |
|----------|------------------|--------------|----------------------|
| Detection model | YOLOv8, consider VLM for staff | YOLOv8n; geometric heuristic for staff | VLM latency incompatible with 15fps pipeline |
| Session sequence | Add session_seq to metadata | Added session_seq | Agreed — useful for out-of-order reconstruction |
| dwell units | Use dwell_ms (integer) | dwell_ms throughout | Agreed — matches challenge schema |
| DB choice | SQLite OK for sequential tests | SQLite | Agreed — minimises setup friction |
| Metrics caching | Cache with Redis TTL | No caching | Real-time requirement explicitly in problem statement |
| Async endpoints | Use async def | sync def | SQLAlchemy sync ORM would block event loop anyway |
