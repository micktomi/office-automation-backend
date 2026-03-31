#!/usr/bin/env bash
set -euo pipefail

if [[ ! -x "./venv/bin/python" ]]; then
    echo "Missing virtualenv python at ./venv/bin/python"
    echo "Create it and install deps: python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"
    exit 1
fi

PORT="${PORT:-3001}"
./venv/bin/python -m uvicorn app.main:app --reload --reload-dir app --port "${PORT}"
