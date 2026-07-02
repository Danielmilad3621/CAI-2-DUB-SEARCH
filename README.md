# FareSweep

> Flexible cheap-fare search engine over Google Flights.
> _(Formerly the Cairo-Dublin search prototype, `CAI-2-DUB-SEARCH`.)_

A **headless flight-search engine** exposed as a documented HTTP/JSON API. It
finds the cheapest round-trip fares out of **Dublin (DUB)** by sweeping every
valid `(departure, return)` date pair for one or more destinations on
**Google Flights**, and returns ranked results.

There is **no UI** — this is a backend service. Drive it with `curl`, an HTTP
client, or any program that speaks JSON. Interactive API docs (Swagger UI) are
served at **`/docs`** and the machine-readable spec at **`/openapi.json`**
([static copy](docs/openapi.json)); a human-readable reference lives in
[`docs/API.md`](docs/API.md).

> Origin from a recent sweep (2026-05-27, 60-day window, EgyptAir nonstop
> DUB↔CAI): **cheapest €692 round trip, Thu 2026-06-04 → Thu 2026-06-11**, both
> legs EgyptAir nonstop economy.

---

## What it does

Google Flights has no way to ask *"what is the cheapest round trip across this
range of dates, restricted to particular operating days?"* This engine does
exactly that:

- pick an origin (Dublin) + one or more destination airports,
- enumerate every `(dep, ret)` pair within a date window, constrained to a set
  of operating weekdays and a min/max trip length,
- open each generated Google Flights search URL headless and read the cheapest
  round-trip total off the page,
- rank the priced results and return the winner.

Because a single search drives a real browser across dozens of pages (minutes,
not milliseconds), the API is **asynchronous**: you `POST` a search, get a job
id back immediately, then poll for progress and results.

## Provider

The one and only data source is **Google Flights**, reached via its
reverse-engineered `tfs=` search-URL encoding and scraped with
**Playwright + headless Firefox**. See [How it works](#how-it-works) for the
details — this is the project's existing provider and is reused unchanged.

## Architecture

```
            HTTP/JSON
client  ───────────────▶  FastAPI (app/main.py)
                              │  POST /api/search  ─▶ build ephemeral Route
                              │  POST /api/scans   ─▶ look up saved Route
                              ▼
                         JobStore (app/jobs.py)  — in-memory, one scan at a time
                              │  background thread
                              ▼
                         engine.run_scan (app/engine.py)
                              │  build tfs= URL  ·  Playwright/Firefox  ·  parse
                              ▼
                         Google Flights
```

| Module | Responsibility |
|---|---|
| `app/main.py` | FastAPI app + all HTTP endpoints |
| `app/engine.py` | Core search: `tfs=` URL builder, page parser, `run_scan`, `Route`, date enumeration |
| `app/registry.py` | Saved routes (built-in from `app/routes.json` + user-created) |
| `app/jobs.py` | In-memory job store + background worker threads |
| `app/scanner.py` | Adapter between the job store and the engine |
| `app/browser_env.py` | Selects a Firefox build matching the installed Playwright |
| `cheapest_dub_*.py` | Optional CLI entrypoints over the same engine |

## Requirements

- Python **3.9+**
- Playwright's **Firefox** build (installed via `playwright install firefox`)
- Or just **Docker** (the image bundles a matching Firefox + system libraries)

---

## Setup & run (local)

From a clean checkout:

```bash
cd faresweep

# 1. Create a venv and install runtime deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Install the matching Playwright Firefox build
python -m playwright install firefox

# 3. (optional) configure via env
cp .env.example .env        # edit if you want; all values have defaults

# 4. Run the API
uvicorn app.main:app --host 0.0.0.0 --port 8000
#   or, with autoreload + auto-setup:
bash scripts/dev.sh
```

The server is now at `http://localhost:8000`. Check it:

```bash
curl http://localhost:8000/api/health
# {"status":"ok","version":"0.2.0","provider":"google-flights (...)"}
```

Open `http://localhost:8000/docs` for interactive Swagger UI.

### Run with Docker

```bash
docker compose up -d --build        # builds the image, runs on 127.0.0.1:8010
curl http://127.0.0.1:8010/api/health
```

The image is `FROM mcr.microsoft.com/playwright/python` so Firefox is already
present — no `playwright install` step needed.

## Configuration (environment variables)

All optional; copy [`.env.example`](.env.example) to `.env` to override.

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8000` | Port uvicorn listens on. |
| `HOST` | `0.0.0.0` (Docker) / `127.0.0.1` (`dev.sh`) | Interface to bind. |
| `ALLOWED_ORIGINS` | `*` | Comma-separated CORS allow-list, or `*` for any. Credentials are enabled only for an explicit (non-`*`) list. |
| `PLAYWRIGHT_BROWSERS_PATH` | unset | Override the Playwright browser cache location. The image bundles one. |

---

## Example requests

### Run a search (Dublin → Istanbul/Sabiha, short trips)

```bash
curl -s -X POST http://localhost:8000/api/search \
  -H 'Content-Type: application/json' \
  -d '{
        "origin": "DUB",
        "destinations": ["IST", "SAW"],
        "start": "2026-07-01",
        "window_days": 21,
        "min_trip_days": 3,
        "max_trip_days": 7,
        "weekdays": "Fri,Sat,Sun",
        "currency": "EUR"
      }'
```

Response (`202 Accepted`) — a job handle:

```json
{
  "id": "1ef4b4c8-c95a-4f52-8f24-e2afe50a5fcc",
  "route_id": "ist-saw",
  "status": "running",
  "total": 48,
  "done": 0,
  "progress_pct": 0.0,
  "links": {
    "status":  "/api/scans/1ef4b4c8-.../",
    "results": "/api/scans/1ef4b4c8-.../results",
    "cancel":  "/api/scans/1ef4b4c8-.../cancel"
  },
  "capability_notes": ["origin pinned to DUB"]
}
```

### Poll progress, then read ranked results

```bash
JOB=1ef4b4c8-c95a-4f52-8f24-e2afe50a5fcc
curl -s http://localhost:8000/api/scans/$JOB           # progress + current winner
curl -s http://localhost:8000/api/scans/$JOB/results   # full + ranked + winner
```

### Nonstop Dublin → Cairo (EgyptAir)

```bash
curl -s -X POST http://localhost:8000/api/search \
  -H 'Content-Type: application/json' \
  -d '{"destinations": "CAI", "nonstop": true, "weekdays": "Sat,Sun,Tue,Thu"}'
```

See [`docs/API.md`](docs/API.md) for every endpoint, all parameters, response
schemas, status codes, and more `curl` examples.

---

## How it works

### The `tfs=` trick

Google Flights encodes the entire search state — origin, destination, dates,
cabin, and any airline filter — in a single base64url-encoded protobuf URL
parameter, `tfs=`. A captured search URL decodes to bytes in which the **dates,
airport codes, and airline IATA codes are literal ASCII**:

```
b"\x08\x1c...\x12\n2026-06-13\x32\x02MS...\x12\x03CAI...2026-06-18...CAI..."
```

So a new search URL for the same route with different dates is just a
**length-preserving byte swap** of the date strings (any ISO date is 10 bytes),
re-base64-encoded. No protobuf library required. `app/engine.py`'s `build_url`
/ `build_nonstop_url` do exactly this.

The origin (Dublin) and, for the nonstop blob, Cairo are encoded as Google
*knowledge-graph IDs* (`/m/02cft`, `/m/01w2v`) rather than ASCII, which is why
**the origin is pinned to DUB** and nonstop search is currently DUB→CAI only.

### Scraping the result

Each generated URL is opened headless in Firefox; the result list is read via
Playwright's `aria_snapshot()` and a regex pulls the lowest
`From NNN euros round trip total …` price (plus cabin/airline/stops where
present). Transient *"Oops, something went wrong"* pages are retried a few times
per pair.

### Why headless Firefox (not Chromium)

1. **Fingerprinting** — some Google properties intermittently return
   `ERR_HTTP2_PROTOCOL_ERROR` for headless Chromium but work in Firefox.
2. **Flaky DNS** — on networks where the OS resolver SERVFAILs, Firefox's TRR
   (DNS-over-HTTPS) prefs route DNS straight to `1.1.1.1` (`FIREFOX_PREFS` in
   `app/engine.py`). On a normal network these are harmless.

### Consent wall

First navigation from an EU IP redirects to `consent.google.com`; the engine
clicks **Reject all** once per session and continues.

---

## Optional CLI shims

The three `cheapest_dub_*.py` scripts are thin CLI wrappers over the same engine
(handy for one-off runs and exercised by the parity tests):

```bash
python cheapest_dub_cai_egyptair.py --start 2026-09-01 --window-days 90
python cheapest_dub_turkey.py --max-trip-days 7
python cheapest_dub_ams.py --headed       # show the browser
```

---

## Limitations / honest caveats

- **Origin pinned to DUB.** The captured blob encodes Dublin as a knowledge-graph
  id; non-DUB origins return `400`.
- **Single adult, economy.** The blob encodes 1 adult. `adults` is accepted and
  echoed back but does **not** yet change provider pricing.
- **Airline filtering is by name, not code.** Google's per-leg airline filter
  bytes are stale (rejected as of 2026-06), so the engine searches unfiltered and
  filters result rows by `airline_name`. The nonstop path sidesteps this with a
  fresh DUB→CAI blob (EgyptAir is the only nonstop carrier).
- **Aggregator prices, not booking prices** — checkout fares can differ slightly.
- **In-memory jobs, one at a time.** Job state lives in process memory (lost on
  restart) and a single browser means one scan runs at a time; a second concurrent
  search returns `409`.
- **Window cap** — Google Flights only sells fares ~330 days out.

---

## Testing & linting

```bash
pip install -r requirements-dev.txt

pytest -q            # 33 tests: URL/parser/registry parity + API surface
ruff check .         # lint
```

Tests launch **no browser** (the worker is monkeypatched or fixture-replayed),
so the full suite runs in well under a second.

## Deployment

Any host that can run a long-lived container with a port exposed works (the
provided `docker-compose.yml`, or Render/Railway/Fly.io). The service needs a
persistent process — scans run minutes-long — so it is **not** serverless-friendly.

```bash
# On the host:
export ALLOWED_ORIGINS="https://your-client.example.com"   # or leave as *
docker compose up -d --build
curl http://127.0.0.1:8010/api/health
```

## Project layout

```
app/                  FastAPI service + search engine
  main.py             HTTP endpoints
  engine.py           tfs= URL builder, parser, run_scan, Route
  registry.py         saved routes (routes.json + user routes)
  jobs.py             in-memory job store + workers
  scanner.py          job-store ↔ engine adapter
  browser_env.py      Firefox build resolution
  routes.json         built-in routes
cheapest_dub_*.py     optional CLI shims over the engine
docs/                 openapi.json + API.md
tests/                parity + API tests
Dockerfile            Playwright-python base image
docker-compose.yml    localhost:8010 → 8000
.env.example          configuration template
```
