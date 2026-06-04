"""
simulate_realtime.py — Simulated real-time event replay for Part E (Live Dashboard).

Reads a JSONL events file and replays events to the API with realistic timing,
simulating a live camera feed. Events are sent in timestamp order with configurable
speed multiplier (default 10x = 1 minute of store time per 6 seconds of replay).

This proves the pipeline and API are genuinely connected — the dashboard metrics
update in real time as this script runs.

Usage:
    python -m pipeline.simulate_realtime
    python -m pipeline.simulate_realtime --file pipeline/outputs/STORE_BLR_002_events.jsonl
    python -m pipeline.simulate_realtime --file data/sample_events.jsonl --speed 1
    python -m pipeline.simulate_realtime --store STORE_BLR_002 --speed 20

Options:
    --file      JSONL file to replay (default: pipeline/outputs/<store>_events.jsonl)
    --store     Store ID (used to find default file, default: STORE_BLR_002)
    --api       API base URL (default: http://localhost:8000)
    --speed     Time multiplier (default: 60 = 1hr of events in ~1min wall time)
    --raw       Use /events/ingest/raw endpoint (for sample_events.jsonl format)
"""

import argparse
import json
import sys
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_API = "http://localhost:8000"
DEFAULT_SPEED = 60       # 60x = 20 minutes of footage plays in ~20 seconds
BATCH_SIZE = 10          # events per POST (small batch = more frequent updates)


def load_events(path: Path, is_raw: bool):
    """Load and sort events from JSONL file."""
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass

    # Sort by timestamp field (handles both schema variants)
    def get_ts(e):
        for field in ["timestamp", "event_timestamp", "event_time", "queue_join_ts"]:
            v = e.get(field)
            if v:
                try:
                    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                except Exception:
                    pass
        return datetime.now(timezone.utc)

    events.sort(key=get_ts)
    return events, [get_ts(e) for e in events]


def post_batch(api_base: str, events: list, is_raw: bool) -> dict:
    endpoint = "/events/ingest/raw" if is_raw else "/events/ingest"
    url = f"{api_base.rstrip('/')}{endpoint}"
    try:
        resp = requests.post(url, json=events, timeout=10)
        if resp.ok:
            return resp.json()
        else:
            print(f"  HTTP {resp.status_code}: {resp.text[:100]}")
            return {}
    except Exception as e:
        print(f"  POST failed: {e}")
        return {}


def get_metrics(api_base: str, store_id: str) -> dict:
    try:
        r = requests.get(f"{api_base}/stores/{store_id}/metrics", timeout=5)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return {}


def print_dashboard(metrics: dict, ingested_total: int, elapsed: float, store_id: str):
    """Simple terminal dashboard."""
    bar = "─" * 60
    print(f"\r{bar}")
    print(f"  🏪 LIVE: {store_id}  |  ⏱  {elapsed:.0f}s elapsed")
    print(f"  👥 Visitors: {metrics.get('unique_visitors', 0):<6}  "
          f"📈 Conversion: {metrics.get('conversion_rate', 0):.1f}%  "
          f"🔢 Queue: {metrics.get('current_queue_depth', 0)}")
    print(f"  🛒 Transactions: {metrics.get('total_transactions', 0):<6}  "
          f"💰 Revenue: ₹{metrics.get('total_revenue', 0):,.0f}  "
          f"🚪 Abandon: {metrics.get('abandonment_rate', 0):.1f}%")
    print(f"  📦 Events sent: {ingested_total}")
    print(bar, end="", flush=True)


def run(
    events_file: Path,
    store_id: str,
    api_base: str,
    speed: float,
    is_raw: bool,
):
    print(f"\n{'='*60}")
    print(f"  Store Intelligence — Simulated Real-Time Replay")
    print(f"  File    : {events_file.name}")
    print(f"  Store   : {store_id}")
    print(f"  API     : {api_base}")
    print(f"  Speed   : {speed}x  ({speed} minutes of footage per minute)")
    print(f"  Mode    : {'raw (sample_events format)' if is_raw else 'canonical schema'}")
    print(f"{'='*60}\n")

    # Verify API is reachable
    try:
        r = requests.get(f"{api_base}/health", timeout=5)
        print(f"  API status: {r.json().get('status', 'UNKNOWN')}")
    except Exception:
        print(f"  ERROR: Cannot reach API at {api_base}")
        print("  Start the API first: uvicorn app.main:app --reload")
        sys.exit(1)

    events, timestamps = load_events(events_file, is_raw)
    if not events:
        print("  No events found in file.")
        sys.exit(1)

    print(f"  Events loaded: {len(events)}")
    if timestamps:
        span = (timestamps[-1] - timestamps[0]).total_seconds() / 60
        wall_time = span / speed
        print(f"  Time span: {span:.1f} min → replay in ~{wall_time:.0f}s at {speed}x speed\n")

    batch = []
    ingested_total = 0
    start_wall = time.time()
    replay_start = timestamps[0] if timestamps else datetime.now(timezone.utc)

    for i, (event, event_ts) in enumerate(zip(events, timestamps)):
        batch.append(event)

        # How far into the data are we?
        data_offset = (event_ts - replay_start).total_seconds()
        # How much wall time should have passed?
        target_wall = data_offset / speed
        # Sleep if we're ahead of schedule
        elapsed_wall = time.time() - start_wall
        sleep_time = target_wall - elapsed_wall
        if sleep_time > 0:
            time.sleep(sleep_time)

        # Send batch when full or on last event
        if len(batch) >= BATCH_SIZE or i == len(events) - 1:
            result = post_batch(api_base, batch, is_raw)
            ingested_total += result.get("ingested", 0)
            batch.clear()

            # Print live dashboard every batch
            metrics = get_metrics(api_base, store_id)
            elapsed = time.time() - start_wall
            print_dashboard(metrics, ingested_total, elapsed, store_id)

    print(f"\n\n  ✅ Replay complete. {ingested_total} events ingested.")
    final_metrics = get_metrics(api_base, store_id)
    if final_metrics:
        print(f"\n  Final metrics for {store_id}:")
        print(f"    Unique visitors  : {final_metrics.get('unique_visitors', 0)}")
        print(f"    Conversion rate  : {final_metrics.get('conversion_rate', 0):.1f}%")
        print(f"    Total revenue    : ₹{final_metrics.get('total_revenue', 0):,.2f}")
        print(f"    Queue depth      : {final_metrics.get('current_queue_depth', 0)}")
        print(f"    Abandonment rate : {final_metrics.get('abandonment_rate', 0):.1f}%")


def main():
    parser = argparse.ArgumentParser(description="Simulated Real-Time Event Replay")
    parser.add_argument("--store", default="STORE_BLR_002")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--file", default=None)
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED)
    parser.add_argument("--raw", action="store_true",
                        help="Use /events/ingest/raw (for sample_events.jsonl format)")
    args = parser.parse_args()

    if args.file:
        events_file = Path(args.file)
    else:
        # Try canonical outputs first, then sample data
        candidates = [
            ROOT / "pipeline" / "outputs" / f"{args.store}_events.jsonl",
            ROOT / "data" / "sample_events.jsonl",
        ]
        events_file = next((p for p in candidates if p.exists()), None)
        if not events_file:
            print("No events file found. Run the pipeline first:")
            print("  python -m pipeline.run_pipeline --store STORE_BLR_002 --output pipeline/outputs/STORE_BLR_002_events.jsonl")
            print("Or use the sample data:")
            print("  python -m pipeline.simulate_realtime --file data/sample_events.jsonl --raw --store ST1076")
            sys.exit(1)

    # Auto-detect raw format from filename
    is_raw = args.raw or "sample_events" in events_file.name

    run(
        events_file=events_file,
        store_id=args.store,
        api_base=args.api,
        speed=args.speed,
        is_raw=is_raw,
    )


if __name__ == "__main__":
    main()
