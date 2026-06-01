#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# Cursor sets PLAYWRIGHT_BROWSERS_PATH to a sandbox copy that SIGABRT on macOS.
case "${PLAYWRIGHT_BROWSERS_PATH:-}" in
  *cursor-sandbox-cache*) unset PLAYWRIGHT_BROWSERS_PATH ;;
esac

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 &
API_PID=$!

cd frontend
npm run dev &
WEB_PID=$!

trap 'kill $API_PID $WEB_PID 2>/dev/null || true' EXIT
wait
