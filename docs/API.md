# API Reference — FareSweep

Base URL (local): `http://localhost:8000`  ·  API version: `0.2.0`

All endpoints are under `/api`. Requests and responses are JSON
(`Content-Type: application/json`). Interactive docs are at **`/docs`**
(Swagger UI) and **`/redoc`**; the OpenAPI spec is at **`/openapi.json`**
(static copy: [`openapi.json`](openapi.json)).

## Conventions

### Search is asynchronous

A search drives a headless browser across every `(destination × date-pair)`
combination and takes minutes. So `POST /api/search` (and `POST /api/scans`)
**enqueue a job** and return immediately; you then poll the job for progress and
results. Only **one scan runs at a time** (a single shared browser) — starting a
second while one is active returns `409`.

### Error format

Errors use FastAPI's envelope. A handled error:

```json
{ "detail": "origin is locked to DUB: ..." }
```

A request-validation error (`422`) lists the offending fields:

```json
{ "detail": [ { "loc": ["body", "max_trip_days"], "msg": "...", "type": "..." } ] }
```

### Shared objects

**Job** — returned by search/scan endpoints:

| Field | Type | Notes |
|---|---|---|
| `id` | string (uuid) | Job id. |
| `route_id` | string | Saved route id, or the derived id of an ad-hoc search (e.g. `ist-saw`). |
| `status` | enum | `queued` · `running` · `completed` · `failed` · `cancelled`. |
| `total` | int | Total `(dest × date-pair)` combinations to scan. |
| `done` | int | Combinations processed so far. |
| `progress_pct` | float | `100 * done / total`. |
| `results_count` | int | Result rows recorded so far. |
| `error` | string\|null | Failure reason when `status = failed`. |
| `message` | string\|null | Human-readable progress line. |
| `config` | object | Echo of the search/scan parameters. |
| `winner` | Result\|null | Cheapest priced row so far. |
| `top_results` | Result[] | Up to 20 cheapest priced rows. |

`POST /api/search` additionally returns `links` (`status`/`results`/`cancel`
URLs) and `capability_notes` (string[] describing applied provider constraints).

**Result row** — one scanned combination:

| Field | Type | Notes |
|---|---|---|
| `dest` | string | Destination IATA. |
| `origin` | string | Always `DUB`. |
| `dep`, `ret` | string | ISO dates. |
| `dep_wd`, `ret_wd` | string | Weekday abbreviations. |
| `trip_days` | int | `ret - dep` in days. |
| `min_price` | int\|null | Cheapest matching round-trip total; `null` = unpriced/error. |
| `entries` | object[] | Matching itineraries (price, class, airline/stops or snippet). |
| `cheapest_tab` | int | Only on airline-filtered/nonstop routes: Google's "Cheapest from" banner. |
| `url` | string | The Google Flights URL scanned. |
| `error` | string | Present instead of pricing fields when navigation failed. |

---

## Endpoints

### `GET /api/health`  ·  *system*

Liveness probe.

**200**
```json
{ "status": "ok", "version": "0.2.0", "provider": "google-flights (...)" }
```
```bash
curl http://localhost:8000/api/health
```

---

### `POST /api/search`  ·  *search*

Run an **ad-hoc** flight search built from the request body (no saved route
needed). Enqueues a background job.

**Request body**

| Field | Type | Default | Notes |
|---|---|---|---|
| `origin` | string | `DUB` | Must be `DUB` (else `400`). |
| `destinations` | string \| string[] | — *(required)* | 1–5 IATA codes; array or comma-separated string. |
| `currency` | string(3) | `EUR` | ISO-4217. |
| `airline` | string(2)\|null | `null` | 2-letter IATA, `^[A-Z0-9]{2}$`. Stored only; does not filter (see notes). |
| `airline_name` | string\|null | `null` | Display name; when set, results are restricted to rows naming this carrier. |
| `nonstop` | bool | `false` | Direct only. Supported for `CAI` only (else `400`). |
| `start` | string\|null | today | Earliest departure `YYYY-MM-DD`. |
| `window_days` | int | `60` | 7–366. |
| `min_trip_days` | int | `3` | 1–30. |
| `max_trip_days` | int | `14` | 2–60; must be ≥ `min_trip_days`. |
| `weekdays` | string | `Sat,Sun,Tue,Thu` | 3-letter operating days for dep & ret. |
| `adults` | int | `1` | 1–9. Accepted and echoed but does **not** change pricing yet. |

**Responses**

| Code | When |
|---|---|
| `202` | Job created. Returns a **Job** + `links` + `capability_notes`. |
| `400` | `origin != DUB`, `nonstop` for a non-CAI destination, or a bad IATA code. |
| `409` | Another scan is already running. |
| `422` | `max_trip_days < min_trip_days`, malformed `start`, empty `destinations`, or window yields zero date pairs. |

```bash
curl -s -X POST http://localhost:8000/api/search \
  -H 'Content-Type: application/json' \
  -d '{"origin":"DUB","destinations":["IST","SAW"],"start":"2026-07-01",
       "window_days":21,"min_trip_days":3,"max_trip_days":7,"weekdays":"Fri,Sat,Sun"}'
```

---

### `GET /api/scans/{job_id}`  ·  *scans*

Get a job's status, progress and current best fare.

**200** → a **Job**.  **404** → unknown id.

```bash
curl http://localhost:8000/api/scans/$JOB
```

---

### `GET /api/scans/{job_id}/results`  ·  *scans*

Full results for a job.

**200**
```json
{
  "id": "…", "status": "completed",
  "results": [ /* every Result row, scan order */ ],
  "ranked":  [ /* priced rows, min_price ascending */ ],
  "winner":  { /* ranked[0] or null */ }
}
```
**404** → unknown id.

```bash
curl http://localhost:8000/api/scans/$JOB/results
```

---

### `POST /api/scans/{job_id}/cancel`  ·  *scans*

Request cancellation of a queued/running job.

**200** → `{ "status": "cancelled" }`.  **404** → unknown id.

```bash
curl -X POST http://localhost:8000/api/scans/$JOB/cancel
```

---

### `GET /api/scans`  ·  *scans*

List recent jobs (most recent first, max 20). **200** → **Job**[].

```bash
curl http://localhost:8000/api/scans
```

---

### `POST /api/scans`  ·  *scans*

Run a **saved route** by id (the registry-driven counterpart to `/api/search`).

**Request body**

| Field | Type | Default | Notes |
|---|---|---|---|
| `route_id` | string | — *(required)* | An id from `GET /api/routes`. |
| `destinations` | string | `IST,SAW,AYT` | Honored only by routes with `configurable_destinations`. |
| `currency` | string | `EUR` | |
| `start` | string\|null | today | `YYYY-MM-DD`. |
| `window_days` | int | `60` | 7–366. |
| `min_trip_days` | int | `3` | 1–30. |
| `max_trip_days` | int | `14` | 2–60. |
| `weekdays` | string | `Sat,Sun,Tue,Thu` | Ignored by routes that pin their own days. |

**Responses:** `200` (a **Job**) · `404` unknown route · `409` scan running ·
`422` missing `route_id`.

```bash
curl -s -X POST http://localhost:8000/api/scans \
  -H 'Content-Type: application/json' -d '{"route_id":"turkey"}'
```

---

### `GET /api/routes`  ·  *routes*

List built-in and user-created routes.

**200** → array of route objects:

| Field | Type | Notes |
|---|---|---|
| `id`, `name`, `subtitle` | string | |
| `origin` | string | `DUB`. |
| `destinations` | string[] | |
| `default_dest` | string | `destinations[0]`. |
| `airline`, `airline_name` | string\|null | |
| `date_strategy` | enum | `window` · `fixed_pairs`. |
| `weekdays` | string\|null | |
| `configurable_destinations` | bool | |
| `eta_minutes` | int | Rough scan-time estimate. |
| `nonstop` | bool | |
| `combinations` | int\|null | Estimated combinations for a default window. |
| `builtin`, `persistent` | bool | User routes are non-persistent (container fs only). |
| `origin_locked` | bool | Always `true` (see provider constraints). |

```bash
curl http://localhost:8000/api/routes
```

---

### `POST /api/routes`  ·  *routes*

Create a user route (window-only, DUB-origin-only). Persists to a
container-local file.

**Request body**

| Field | Type | Default | Notes |
|---|---|---|---|
| `destinations` | string \| string[] | — *(required)* | 1–5 IATA codes. |
| `id` | string\|null | derived | `^[a-z0-9][a-z0-9_-]*$`, ≤32. |
| `name`, `subtitle` | string\|null | derived | |
| `origin` | string | `DUB` | Must be `DUB`. |
| `airline` | string(2)\|null | `null` | `^[A-Z0-9]{2}$`. |
| `airline_name` | string\|null | `null` | |
| `weekdays` | string\|null | `null` | 3-letter days. |
| `eta_minutes` | int | `30` | 1–600. |
| `configurable_destinations` | bool | `false` | |

**Responses:** `201` (route object) · `400` non-DUB origin or invalid data ·
`409` id already exists · `422` field pattern violation.

```bash
curl -s -X POST http://localhost:8000/api/routes \
  -H 'Content-Type: application/json' \
  -d '{"destinations":"CDG,ORY","airline":"AF","airline_name":"Air France"}'
```

---

### `DELETE /api/routes/{route_id}`  ·  *routes*

Delete a user route.

**Responses:** `200` → `{ "status": "deleted" }` · `403` built-in route ·
`404` unknown · `409` route has a running scan.

```bash
curl -X DELETE http://localhost:8000/api/routes/cdg-ory
```

---

### `GET /api/saved-trips`  ·  *system*

Best fares from the bundled cached result files (offline sample data — runs no
scan). **200** → up to 6 objects:

```json
{ "id": "...", "label": "DUB → CAI", "price": 692, "currency": "EUR",
  "dep": "2026-06-04", "ret": "2026-06-11", "airline": "EgyptAir",
  "source_file": "results-2026-05-27.json" }
```
```bash
curl http://localhost:8000/api/saved-trips
```

---

## End-to-end example

```bash
BASE=http://localhost:8000

# 1. Start a search
JOB=$(curl -s -X POST $BASE/api/search -H 'Content-Type: application/json' \
  -d '{"destinations":"CAI","nonstop":true}' | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# 2. Poll until done
while :; do
  S=$(curl -s $BASE/api/scans/$JOB | python -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  echo "status=$S"; [ "$S" = completed ] || [ "$S" = failed ] && break; sleep 5
done

# 3. Read the winner
curl -s $BASE/api/scans/$JOB/results | python -m json.tool
```
