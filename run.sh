#!/usr/bin/env bash
# run.sh — Process all CCTV clips and feed events into the API.
# Usage: ./run.sh [STORE_ID] [API_URL]
# Example: ./run.sh STORE_BLR_002 http://localhost:8000

STORE=${1:-STORE_BLR_002}
API=${2:-http://localhost:8000}

echo "Running Store Intelligence Pipeline"
echo "  Store : $STORE"
echo "  API   : $API"
echo ""

python -m pipeline.run_pipeline --store "$STORE" --api "$API"
