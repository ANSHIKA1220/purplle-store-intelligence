# Store Intelligence API

Real-time retail analytics — from raw CCTV footage to live store metrics.

Built for the Apex Retail engineering challenge: processes CCTV clips with YOLOv8 + ByteTrack, emits structured behavioural events, and exposes a production-ready analytics API with a live Streamlit dashboard.

---

## Quick Start (5 commands)

```bash
git clone <repo-url> store-intelligence && cd store-intelligence
pip install -r requirements.txt
python scripts/load_pos.py
uvicorn app.main:app --reload
streamlit run dashboard/streamlit_app.py
```

Then open:
- API docs: http://localhost:8000/docs
- Dashboard: http://localhost:8501 (auto-discovers available stores)
- Health check: http://localhost:8000/health

## Verify the acceptance gate

```bash
# Run the 10 assertion checks
python assertions.py http://localhost:8000
```

All 10 must pass before scoring begins.

---

## Docker (zero manual steps)

```bash
docker compose up
```

Services:
- `api` → http://localhost:8000
- `dashboard` → http://localhost:8501

---

## Running the Detection Pipeline

### Prerequisites

Place video clips in `pipeline/inputs/` following this structure:

```
pipeline/inputs/
  Store 1/
    CAM 3 - entry.mp4        ← entry/exit camera
    CAM 1 - zone.mp4         ← floor zone camera
    CAM 5 - billing.mp4      ← billing area camera
  Store 2/
    entry 2.mp4
    zone.mp4
    billing_area.mp4
```

### Process clips and feed into the API

```bash
# Start the API first
uvicorn app.main:app --reload

# In a second terminal — process Store 2 clips and ingest into API
python -m pipeline.run_pipeline --store STORE_BLR_002 --api http://localhost:8000

# Or write events to a JSONL file first, then replay
python -m pipeline.run_pipeline --store STORE_BLR_002 --output pipeline/outputs/events.jsonl
```

### Windows batch runner

```bat
run.bat STORE_BLR_002 http://localhost:8000
```

### Unix/Mac

```bash
./run.sh STORE_BLR_002 http://localhost:8000
```

### Pipeline options

```
python -m pipeline.run_pipeline --help

  --store     Store ID from store_layout.json (default: STORE_BLR_002)
  --api       API base URL to POST events to (e.g. http://localhost:8000)
  --output    JSONL output file path (alternative to --api)
  --display   Show OpenCV debug windows while processing
```

### Replaying a JSONL file

```bash
python scripts/replay_events.py pipeline/outputs/events.jsonl http://localhost:8000
```

---

## Part E — Live Dashboard (Real-Time Replay)

To demonstrate live pipeline → API → dashboard connectivity, use the simulated real-time replay:

```bash
# Terminal 1: start the API
uvicorn app.main:app --reload

# Terminal 2: start the dashboard
streamlit run dashboard/streamlit_app.py

# Terminal 3: replay sample events in real time (60x speed = 20min footage in ~20s)
python -m pipeline.simulate_realtime --file data/sample_events.jsonl --raw --store ST1076 --speed 60

# Or replay your own processed events
python -m pipeline.simulate_realtime --store STORE_BLR_002 --speed 60
```

Watch the dashboard update live as events flow in. The terminal shows a live metric display simultaneously.

Dashboard URL: **http://localhost:8501**

---

## API Reference

### Acceptance Gate Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/events/ingest` | Ingest up to 500 events; idempotent by `event_id` |
| `GET` | `/stores/{store_id}/metrics` | Real-time KPIs: visitors, conversion, dwell, queue depth |
| `GET` | `/stores/{store_id}/funnel` | 4-stage conversion funnel with drop-off % |
| `GET` | `/stores/{store_id}/heatmap` | Zone visit frequency + dwell, normalised 0-100 |
| `GET` | `/stores/{store_id}/anomalies` | Active anomalies with severity and suggested actions |
| `GET` | `/health` | Service health, per-store last event timestamp, stale feed warnings |

### Quick test

```bash
# Ingest a test event
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d '[{
    "event_id": "550e8400-e29b-41d4-a716-446655440000",
    "visitor_id": "VIS_001001",
    "store_id": "STORE_BLR_002",
    "camera_id": "CAM_ENTRY_01",
    "event_type": "ENTRY",
    "timestamp": "2026-03-03T14:22:10Z",
    "zone_id": null,
    "dwell_ms": 0,
    "is_staff": false,
    "confidence": 0.91,
    "metadata": {"session_seq": 1}
  }]'

# Check metrics
curl http://localhost:8000/stores/STORE_BLR_002/metrics

# Check health
curl http://localhost:8000/health
```

---

## Running Tests

```bash
# Full test suite
pytest tests/ -v

# With coverage report
pytest tests/ --cov=app --cov-report=term-missing

# Single test file
pytest tests/test_ingest.py -v
```

---

## Architecture

```
CCTV Footage (mp4)
     │
     ▼
YOLOv8n — person detection (class_id=0 filter)
     │
     ▼
ByteTrack — multi-object tracking (lost_track_buffer=90)
     │
     ▼
EventGenerator — ENTRY/EXIT/ZONE_ENTER/ZONE_DWELL/BILLING events
     │  (visitor_id, confidence, session_seq, is_staff heuristic)
     ▼
POST /events/ingest (batches of 100, idempotent)
     │
     ▼
SQLite (events + sessions + pos_transactions tables)
     │
     ▼
FastAPI Analytics Layer
  ├── /metrics   — KPIs + POS correlation
  ├── /funnel    — session-based 4-stage funnel
  ├── /heatmap   — normalised zone heat scores
  ├── /anomalies — 5 anomaly detectors
  └── /health    — stale feed detection
     │
     ▼
Streamlit Dashboard (10s live refresh)
```

---

## Event Schema

```json
{
  "event_id": "uuid-v4",
  "store_id": "STORE_BLR_002",
  "camera_id": "CAM_ENTRY_01",
  "visitor_id": "VIS_c8a2f1",
  "event_type": "ZONE_DWELL",
  "timestamp": "2026-03-03T14:22:10Z",
  "zone_id": "LEFT_SHELF",
  "zone_name": "LEFT_SHELF",
  "dwell_ms": 8400,
  "is_staff": false,
  "confidence": 0.91,
  "metadata": {
    "queue_depth": null,
    "sku_zone": "MOISTURISER",
    "session_seq": 5
  }
}
```

### Event type catalogue

| Event Type | When Emitted |
|------------|-------------|
| `ENTRY` | Visitor crosses entry threshold inbound |
| `EXIT` | Visitor crosses entry threshold outbound |
| `REENTRY` | Same visitor seen after a prior EXIT (within 5 min) |
| `ZONE_ENTER` | Visitor enters a named zone |
| `ZONE_EXIT` | Visitor leaves a named zone |
| `ZONE_DWELL` | Visitor has been in zone continuously for 30+ seconds |
| `BILLING_QUEUE_JOIN` | Visitor enters billing zone while queue_depth > 0 |
| `BILLING_QUEUE_ABANDON` | Visitor leaves billing zone before a POS transaction |

---

## Project Structure

```
store-intelligence/
├── app/
│   ├── api/
│   │   ├── ingest.py            ← POST /events/ingest
│   │   ├── ingest_raw.py        ← POST /events/ingest/raw (translates sample data)
│   │   ├── metrics.py           ← GET /stores/{id}/metrics
│   │   ├── funnel.py            ← GET /stores/{id}/funnel
│   │   ├── heatmap.py           ← GET /stores/{id}/heatmap
│   │   ├── anomalies.py         ← GET /stores/{id}/anomalies
│   │   ├── health.py            ← GET /health
│   │   ├── cv.py                ← GET /cv/summary,zones,dwell
│   │   ├── stores.py            ← GET /stores (for dashboard dropdown)
│   │   └── debug.py             ← GET /debug/sessions
│   ├── models/
│   │   ├── event.py             ← Pydantic ingest model
│   │   ├── response.py          ← Pydantic response models
│   │   ├── db_event.py          ← SQLAlchemy EventDB
│   │   ├── session.py           ← SQLAlchemy SessionDB
│   │   ├── pos_transaction.py   ← SQLAlchemy POSTransaction
│   │   └── cv_event.py          ← Legacy CV events table
│   ├── services/
│   │   ├── session_service.py   ← Session lifecycle from events
│   │   ├── metrics_service.py   ← KPI computation + POS correlation
│   │   ├── funnel_service.py    ← 4-stage funnel
│   │   ├── heatmap_service.py   ← Normalised zone heatmap
│   │   └── anomaly_service.py   ← 5 anomaly detectors
│   ├── database.py              ← DB Engine and SessionLocal
│   └── main.py                  ← FastAPI app + middleware
├── pipeline/
│   ├── run_pipeline.py          ← Master runner (all clips → API)
│   ├── detector.py              ← YOLOv8 + ByteTrack wrapper
│   ├── event_generator.py       ← Frame detections → structured events
│   ├── session_manager.py       ← In-pipeline visitor session state
│   ├── simulate_realtime.py     ← Replay events in simulated real-time
│   ├── event_writer.py          ← Legacy: DB writer for CV events
│   ├── tracker.py               ← ByteTrack instance setup
│   ├── zone_utils.py            ← Helper for point-in-polygon
│   ├── zones.py                 ← Hardcoded zone coordinates
│   ├── run_entry_event.py       ← Legacy: entry detection script
│   ├── run_zone.py              ← Legacy: zone tracking script
│   ├── run_dwell_time.py        ← Legacy: dwell time script
│   └── inputs/                  ← Place mp4 clips here
├── dashboard/
│   └── streamlit_app.py         ← Live dashboard (10s auto-refresh)
├── data/
│   ├── store_layout.json        ← Zone definitions per store
│   └── POS - sample transactionsb1e826f.csv
├── scripts/
│   ├── load_pos.py              ← Load POS CSV into DB
│   ├── load_sample_events.py    ← Post sample JSONL to API
│   ├── replay_events.py         ← Replay JSONL to ingest endpoint
│   ├── check_cv_events.py       ← Inspect CV events in DB
│   └── test_cv_event.py         ← Quick DB insert test
├── tests/
│   ├── conftest.py              ← Pytest fixtures & isolated DB setup
│   ├── test_ingest.py
│   ├── test_metrics.py
│   ├── test_funnel.py
│   ├── test_heatmap.py
│   ├── test_anomalies.py
│   └── test_health.py
├── assertions.py                ← Acceptance gate check script
├── Dockerfile                   ← API container build
├── Dockerfile.dashboard         ← Dashboard container build
├── docker-compose.yml           ← Multi-container orchestration
├── run.sh / run.bat             ← One-command pipeline runners
├── DESIGN.md                    ← Architecture & engineering decisions
├── CHOICES.md                   ← Explanations of AI suggestions vs. choices
└── requirements.txt             ← Python dependencies```

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Detection | YOLOv8n (ultralytics) |
| Tracking | ByteTrack (supervision) |
| API Framework | FastAPI |
| ORM | SQLAlchemy 2.x |
| Database | SQLite (swappable to PostgreSQL) |
| Data Processing | Pandas, NumPy |
| Dashboard | Streamlit |
| Testing | pytest + httpx |
| Containerisation | Docker + docker-compose |

---

## Author

Anshika Shrivastava — Purplle Tech Challenge 2026
