# Headless flight-search API. The Playwright base image ships the matching
# Firefox build + system libs so engine.run_scan can drive the browser.
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Core service: the engine + API live under app/ (routes.json ships with it).
COPY app/ app/
# Optional CLI shims over the same engine (handy for one-off runs / debugging).
COPY cheapest_dub_turkey.py cheapest_dub_cai_egyptair.py cheapest_dub_ams.py ./
# Cached sample results served read-only by GET /api/saved-trips.
COPY results-2026-05-27.json dub_turkey_all_results.json dub_turkey_saw_ayt.json ./

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,sys,urllib.request as u; p=os.getenv('PORT','8000'); sys.exit(0 if u.urlopen(f'http://127.0.0.1:{p}/api/health').status==200 else 1)"

# HOST/PORT are overridable via env (see .env.example). Shell form so they expand.
CMD uvicorn app.main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8000}
