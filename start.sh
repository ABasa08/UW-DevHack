#!/usr/bin/env bash
# Starts the PerkPilot backend (port 8000) and frontend (port 3000). Ctrl+C stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

# Refuse to start on busy ports, and say what's holding them.
busy=0
for port in 8000 3000; do
  pid="$(lsof -ti tcp:$port -sTCP:LISTEN 2>/dev/null | head -1 || true)"
  if [ -n "$pid" ]; then
    echo "Port $port is already in use by: $(ps -o command= -p "$pid" | cut -c1-80) (pid $pid)"
    busy=1
  fi
done
if [ "$busy" = 1 ]; then
  if curl -s -m 2 http://localhost:8000/api/health | grep -q '"ok":true'; then
    echo "PerkPilot already looks like it's running: open http://localhost:3000"
  fi
  echo "To free the ports and start fresh, run:  lsof -ti tcp:8000,3000 | xargs kill"
  exit 1
fi

# Python 3.10+ is required.
PY="$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)"
if [ ! -d "$ROOT/backend/.venv" ]; then
  "$PY" -m venv "$ROOT/backend/.venv"
  "$ROOT/backend/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt"
fi
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  npm --prefix "$ROOT/frontend" install --no-audit --no-fund
fi

# --reload restarts the API automatically when backend code changes.
(cd "$ROOT/backend" && exec .venv/bin/uvicorn app.main:app --port 8000 --reload --reload-dir app) &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT

for _ in $(seq 1 30); do
  curl -s -m 1 http://localhost:8000/api/health >/dev/null && break
  if ! kill -0 "$BACK" 2>/dev/null; then echo "Backend failed to start; see the error above."; exit 1; fi
  sleep 1
done
echo "Backend ready on http://localhost:8000 — starting frontend on http://localhost:3000"
npm --prefix "$ROOT/frontend" run dev
