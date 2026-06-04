"""
load_sample_events.py — Load sample_events.jsonl into the Intelligence API.

Reads the provided sample_events.jsonl file (which uses the real pipeline
output schema) and posts it to POST /events/ingest/raw.

Usage:
    python scripts/load_sample_events.py
    python scripts/load_sample_events.py path/to/sample_events.jsonl
    python scripts/load_sample_events.py path/to/sample_events.jsonl http://localhost:8000
"""

import json
import sys
import requests
from pathlib import Path

ROOT = Path(__file__).parent.parent
DEFAULT_JSONL = ROOT / "data" / "sample_events.jsonl"
DEFAULT_API = "http://localhost:8000"


def load(jsonl_path: Path, api_base: str):
    if not jsonl_path.exists():
        # Try next to the script or in root
        alt = ROOT / "sample_events.jsonl"
        if alt.exists():
            jsonl_path = alt
        else:
            print(f"ERROR: File not found: {jsonl_path}")
            print("Place your sample_events.jsonl in the data/ folder and retry.")
            sys.exit(1)

    events = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"  Skipping malformed line: {e}")

    print(f"Loaded {len(events)} events from {jsonl_path}")

    if not events:
        print("Nothing to ingest.")
        return

    # Post in batches of 100
    batch_size = 100
    total_ingested = 0
    total_dupes = 0
    total_rejected = 0

    for i in range(0, len(events), batch_size):
        batch = events[i : i + batch_size]
        try:
            resp = requests.post(
                f"{api_base.rstrip('/')}/events/ingest/raw",
                json=batch,
                timeout=30,
            )
            if resp.ok:
                data = resp.json()
                total_ingested += data.get("ingested", 0)
                total_dupes += data.get("duplicates", 0)
                total_rejected += data.get("rejected_count", 0)
                if data.get("rejected"):
                    for r in data["rejected"]:
                        print(f"  REJECTED: {r}")
            else:
                print(f"  HTTP {resp.status_code}: {resp.text[:300]}")
        except requests.exceptions.ConnectionError:
            print(f"ERROR: Cannot connect to API at {api_base}")
            print("Make sure the API is running: uvicorn app.main:app --reload")
            sys.exit(1)

    print(f"\nDone:")
    print(f"  Ingested  : {total_ingested}")
    print(f"  Duplicates: {total_dupes}")
    print(f"  Rejected  : {total_rejected}")

    # Show which store IDs were loaded
    store_ids = set()
    for e in events:
        sid = e.get("store_code") or e.get("store_id") or e.get("store_id")
        if sid:
            store_ids.add(sid)

    if store_ids:
        print(f"\nStore IDs found in file: {sorted(store_ids)}")
        print("Normalised to canonical form (e.g. store_1076 → ST1076)")
        print("\nQuery your store metrics:")
        for sid in sorted(store_ids):
            canonical = _normalise(sid)
            print(f"  curl {api_base}/stores/{canonical}/metrics")


def _normalise(raw: str) -> str:
    if raw.lower().startswith("store_") and raw[6:].isdigit():
        return f"ST{raw[6:]}"
    return raw


if __name__ == "__main__":
    jsonl = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JSONL
    api = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_API
    load(jsonl, api)
