#!/usr/bin/env bash
# Starts the PerkPilot backend (port 8000) and frontend (port 3000).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

# Python 3.10+ is required.
PY="$(command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)"
if [ ! -d "$ROOT/backend/.venv" ]; then
  "$PY" -m venv "$ROOT/backend/.venv"
  "$ROOT/backend/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt"
fi
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  npm --prefix "$ROOT/frontend" install --no-audit --no-fund
fi

(cd "$ROOT/backend" && .venv/bin/uvicorn app.main:app --port 8000) &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT
npm --prefix "$ROOT/frontend" run dev
