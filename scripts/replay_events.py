"""
replay_events.py — Replay a JSONL events file into the Intelligence API.

Usage:
    python scripts/replay_events.py pipeline/outputs/events.jsonl http://localhost:8000
    python scripts/replay_events.py pipeline/outputs/events.jsonl  # defaults to localhost:8000
"""

import json
import sys
import time
import requests

BATCH_SIZE = 100
DEFAULT_API = "http://localhost:8000"


def replay(jsonl_path: str, api_base: str = DEFAULT_API, realtime: bool = False):
    url = f"{api_base.rstrip('/')}/events/ingest"
    total_events = 0
    total_ingested = 0
    total_duplicates = 0

    with open(jsonl_path, encoding="utf-8") as f:
        batch = []
        prev_ts = None

        for line in f:
            line = line.strip()
            if not line:
                continue

            event = json.loads(line)
            batch.append(event)
            total_events += 1

            if len(batch) >= BATCH_SIZE:
                result = _post(url, batch)
                total_ingested += result.get("ingested", 0)
                total_duplicates += result.get("duplicates", 0)
                batch.clear()

        # Flush remaining
        if batch:
            result = _post(url, batch)
            total_ingested += result.get("ingested", 0)
            total_duplicates += result.get("duplicates", 0)

    print(f"\nReplay complete:")
    print(f"  Events read   : {total_events}")
    print(f"  Ingested      : {total_ingested}")
    print(f"  Duplicates    : {total_duplicates}")


def _post(url: str, events: list) -> dict:
    try:
        resp = requests.post(url, json=events, timeout=30)
        if resp.ok:
            data = resp.json()
            print(
                f"  Batch: ingested={data.get('ingested', 0)} "
                f"duplicates={data.get('duplicates', 0)} "
                f"rejected={data.get('rejected_count', 0)}"
            )
            return data
        else:
            print(f"  ERROR {resp.status_code}: {resp.text[:200]}")
            return {}
    except Exception as e:
        print(f"  FAILED: {e}")
        return {}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/replay_events.py <events.jsonl> [api_base_url]")
        sys.exit(1)

    jsonl = sys.argv[1]
    api = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_API
    replay(jsonl, api)
