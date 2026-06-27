#!/usr/bin/env bash
# Run the headless flight-search API locally with autoreload.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
  .venv/bin/python -m playwright install firefox
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# Some sandboxed editors set PLAYWRIGHT_BROWSERS_PATH to a copy that SIGABRTs on
# macOS; app.browser_env also guards against this at runtime.
case "${PLAYWRIGHT_BROWSERS_PATH:-}" in
  *cursor-sandbox-cache*) unset PLAYWRIGHT_BROWSERS_PATH ;;
esac

exec uvicorn app.main:app --reload --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
