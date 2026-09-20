#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

# Start backend parse API
cd "$ROOT/backend"
PYTHONPATH="$ROOT/backend" python3 -m uvicorn app.main:app --host 127.0.0.1 --port 3001 &
BACKEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Start frontend (exposed preview port)
cd "$ROOT/frontend"
npm run dev -- --host 0.0.0.0 --port 5173
