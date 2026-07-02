# FareSweep — Backend Inventory & Resilience Acceptance Criteria

> A map of the FastAPI/Playwright backend (`app/`) and its offline replay harness (`tests/`).
> Every surface and failure mode below is tagged with a resilience-weighted **acceptance
> criterion (AC1..AC5)** and a finite set of **offline edge cases** to test (RUN MODE: OFFLINE
> ONLY — no live Google Flights hits; everything reproducible via recorded fixtures/replay).
>
> File:line citations point at the real source as of this writing. Read the files; this is a map.

## AC reference (from the brief)

| AC | One-line meaning |
|----|------------------|
| **AC1** | Parser correctness on drifted/partial/empty pages — drift must not masquerade as `min_price=None` "no flights"; a partial ("Loading…") page must not be parsed as a final empty result. |
| **AC2** | Anti-bot / consent / sign-in redirect / CAPTCHA / "unusual traffic" must be a **distinct typed state**, never an indistinguishable empty row. |
| **AC3** | Diagnosability: every empty/failed pair explainable from structured logs+traces ALONE (URL, attempt count, why unpriced, snapshot length, regex match count, detected wall type). The scan path currently has **NO logging**. |
| **AC4** | A golden/regression gate must guard the parser AND failure-mode handling on every change (drift/partial/blocked fixtures, not only happy snapshots). |
| **AC5** | (secondary) retry/timeout/backoff correctness; concurrency/resource/browser-context budgets; no leaks; bounded work. |

---

## 1. HTTP entry points

All routes are declared in `app/main.py`. FastAPI returns `422` automatically for Pydantic
validation failures (body schema), and the documented `HTTPException`s for capability/state errors.

| # | Method & Path | Request model / params | Success | Error codes | What it does (file:line) |
|---|---------------|------------------------|---------|-------------|--------------------------|
| 1 | `GET /api/health` | none | 200 | — | Liveness; returns `{status, version, provider}`. `main.py:151-154` |
| 2 | `GET /api/routes` | none | 200 | — | Lists built-in + user routes, each with estimated `combinations` (best-effort; `None` on estimate failure). `main.py:187-190`, `_route_entry_dict` `main.py:157-184` |
| 3 | `POST /api/routes` | `RouteCreate` `main.py:134-148` | 201 | 400 (origin≠DUB, `RegistryError`), 409 (`RouteConflict`), 422 (schema) | Creates a window-only, DUB-locked user route; persists to `routes.user.json`. `main.py:193-217` |
| 4 | `DELETE /api/routes/{route_id}` | path `route_id` | 200 `{status:"deleted"}` | 409 (route has running/queued scan), 403 (built-in), 404 (unknown) | Deletes a user route after checking active jobs. `main.py:220-235` |
| 5 | `POST /api/search` | `SearchRequest` `main.py:90-131` | 202 (job handle + `links` + `capability_notes`) | 400 (origin≠DUB, Route `ValueError` e.g. nonstop≠CAI / bad IATA, estimate failure), 422 (no dests / >5 dests / schema / zero pairs), 409 (scan already running) | Builds an ephemeral `Route`, estimates pairs, creates a job, spawns the worker thread. `main.py:270-324`; `_build_search_route` `main.py:238-267` |
| 6 | `POST /api/scans` | `ScanCreate` `main.py:74-87` | 200 (job dict) | 404 (unknown route_id), 400 (estimate failure), 409 (scan already running) | Runs a **saved** route by id over a date window. `main.py:327-345` |
| 7 | `GET /api/scans` | none | 200 (list, ≤20, newest first) | — | Lists recent jobs. `main.py:348-351` → `store.list_jobs()` `jobs.py:60-64` |
| 8 | `GET /api/scans/{job_id}` | path `job_id` | 200 (job dict) | 404 (unknown job) | Single job status/progress/best fare. `main.py:354-360` |
| 9 | `GET /api/scans/{job_id}/results` | path `job_id` | 200 `{id,status,results,ranked,winner}` | 404 (unknown job) | Full rows + price-ranked view + winner (priced rows only). `main.py:363-377` |
| 10 | `POST /api/scans/{job_id}/cancel` | path `job_id` | 200 `{status:"cancelled"}` | 404 (unknown job) | Sets the cancel `Event`; flips QUEUED/RUNNING→CANCELLED. `main.py:380-385` → `store.cancel` `jobs.py:85-95` |
| 11 | `GET /api/saved-trips` | none | 200 (≤6 trips) | — | Reads bundled result JSONs (`dub_turkey_*`, `results-2026-05-27.json`), returns cheapest per file. No scan. `main.py:388-423` |

### Cross-cutting HTTP surface notes

- **CORS** `main.py:59-71`: `ALLOWED_ORIGINS` (comma-sep) → list; defaults to `["*"]`. `allow_credentials` is enabled only when origins is **not** `["*"]` (CORS-spec safe), methods/headers wildcard.
- **`202` async contract** `main.py:270-324`: `/api/search` returns a job handle, not results — clients must poll `links.status` / `links.results`.
- **`409` single-active gate**: both `/api/search` and `/api/scans` go through `store.create`, which raises `RuntimeError` if a job is QUEUED/RUNNING → HTTP 409. `jobs.py:66-83`, `main.py:304-306 / 339-342`.

#### Acceptance criteria — HTTP surface

- **AC2/AC3**: A `GET /api/scans/{id}/results` response where `winner is None` MUST be distinguishable (via row-level typed state/log correlation) from "all pairs scanned, genuinely no flights." Today both look identical: `winner` is `None` and rows carry `min_price:None` with no wall-type/reason field (`main.py:369-376`, `jobs.py:30-46`).
- **AC3**: `/api/scans/{id}` exposes `done/total/progress_pct/message/error` but no per-pair diagnostics; the worker writes no logs (`jobs.py:101-126`, `engine.run_scan` `engine.py:502-605` has zero `log.*` calls).
- **AC5**: The `409` gate is the only concurrency control; verify it cannot be raced (two creates interleaving inside the lock — `jobs.py:66-83` holds `_lock` for the whole check-and-set, good; assert it).

#### Edge cases to test (offline)
1. `POST /api/search` while a job is QUEUED/RUNNING → expect **409** (drive with the `_patched_worker` fake from `tests/test_search_api.py`, but make the first fake block until released).
2. `GET /api/scans/{unknown}` and `/results` and `/cancel` → **404** each.
3. `DELETE /api/routes/{id}` while that route has an active job → **409** (`main.py:222-228`); after cancel → **200**.
4. `/api/saved-trips` with (a) a missing file, (b) a `JSONDecodeError` file, (c) a file with all `min_price:None` rows → each skipped, never 500 (`main.py:399-406`).
5. CORS: with `ALLOWED_ORIGINS=https://a.com` assert `allow_credentials=True`; with default `*` assert `False` (`main.py:59-63`).
6. `/results` ranking is stable and excludes `min_price:None` rows; `winner` is the global min (`main.py:369-376`).

---

## 2. Pipeline stages: request → ranked results

The ad-hoc path (`POST /api/search`) and saved path (`POST /api/scans`) converge on the same
engine. Stages, with inputs → outputs:

| Stage | Where | Input | Output | Notes / hazards |
|-------|-------|-------|--------|-----------------|
| **S1 Schema validation** | Pydantic `SearchRequest`/`ScanCreate` `main.py:74-131` | raw JSON body | typed model or **422** | `max_trip_days≥min_trip_days`, ISO `start`, currency regex `^[A-Za-z]{3}$`, airline `^[A-Z0-9]{2}$`. `_validate` `main.py:122-131` |
| **S2 Origin lock** | `main.py:277-282 / 195-200` | `origin` | pass or **400** | Pinned to `DUB` (`LOCKED_ORIGIN`, `registry.py:31`); blob encodes Dublin as KG-id `/m/02cft`. |
| **S3 Route build** | `_build_search_route` `main.py:238-267` (ad-hoc) / `registry.get` `main.py:330` (saved) | dest codes, flags | frozen `Route` or **400/422** | dest dedup/upper/cap≤5 (`main.py:245-249`); `Route.__post_init__` validates IATA, nonstop=CAI-only, date_strategy. `engine.py:314-330` |
| **S4 Estimate pairs** | `estimate_pairs` `engine.py:397-398` (search) / `estimate_total` `scanner.py:26` (scans) | route + config | `int` total; **422** if 0 (`main.py:300-301`) | `len(pairs) × len(dests)`; pairs from `enumerate_pairs` `engine.py:354-384`. |
| **S5 Job create** | `store.create` `jobs.py:66-83` | route_id, total, config | `ScanJob(QUEUED)` or **409** | Single-active gate; allocates `uuid4` id + cancel `Event`. |
| **S6 Worker spawn** | `store.start_worker` `jobs.py:101-126` | job_id, runner closure | daemon thread | search passes `lambda: run_scan(route,...)` `main.py:308`; scans pass `run_job` `main.py:344`. Sets RUNNING, wraps runner in try/except, clears `_active_id` in `finally`. |
| **S7 Browser launch** | `run_scan` `engine.py:522-534` | currency | one Firefox→context→page, consent rejected | `prepare_playwright_browsers()`, `_launch_firefox` `engine.py:480-499`, `_reject_consent` `engine.py:473-477`. ONE browser/ctx/page reused for ALL pairs. |
| **S8 Per-pair scan loop** | `run_scan` `engine.py:536-602` | dests × pairs | appends one row per combo to `job.results` | nested `for dest: for dep,ret:`; updates `job.done/message`; cancel-checked at both loop levels (`engine.py:538,541`). |
| **S9 Navigate + retry** | `engine.py:565-571` | URL | populated page or transient-error retry | `_GOTO_ATTEMPTS=4` (`engine.py:66`); retries ONLY on `"something went wrong"` substring; 2.5 s settle + 3 s backoff. |
| **S10 Parse** | `parse_results` `engine.py:401-470` (RT) / `parse_oneway` `engine.py:260-292` | `page.locator("body").aria_snapshot()` | `{min_price, entries[, cheapest_tab]}` | Regex over aria text. Two RT branches: airline-filtered vs any-airline. |
| **S11 Row envelope** | `engine.py:588-602` (ok) / `573-583` (error) | parse dict | row with `dest/origin/dep/ret/dep_wd/ret_wd/trip_days/url` | Error rows get `{min_price:None, error}`; `dest` key only added for multi-dest/configurable routes (legacy quirk, `engine.py:581-582`). |
| **S12 Rank** | `to_dict` `jobs.py:30-46` & `/results` `main.py:369-376` | `job.results` | `winner` (min) + `ranked`/`top_results[:20]` | Filters `min_price is not None`, sorts ascending. |

### URL construction sub-pipeline (engine)

- `build_url` `engine.py:130-166` — byte-patches `BASE_TFS` `engine.py:51-55`: optional airline swap (`engine.py:152-159`), dest swap (both occurrences, `engine.py:160-161`), collision-safe date swap `_replace_dates` `engine.py:110-127`. **Run path always calls with `airline=None`** (`engine.py:558`) because the captured per-leg airline filter is now rejected by Google (`engine.py:144-150`, `546-554`).
- `build_nonstop_url` `engine.py:216-228` — fresh-schema `NONSTOP_CAIRO_TFS` `engine.py:184-187` + `_add_nonstop_filter` `engine.py:193-213`; DUB→Cairo only. Used for `route.nonstop` (`egyptair`) (`engine.py:555-556`).
- `build_oneway_url` `engine.py:246-257` + `parse_oneway` — CAI→DUB one-way; **not reachable** from any HTTP endpoint (only exercised by `tests/test_oneway.py`). Dead-ish surface worth flagging.

#### Acceptance criteria — pipeline

- **AC1 (critical, live defect)**: S10 cannot tell a **partial** page from a **final empty** one. The recorded `egyptair_snapshot.txt` literally contains `- text: Loading results` (line 29) and `Cheapest from Fetching results 893 euros` (lines 68-69) yet `parse_results` extracted `min_price=893` from it (`golden_parses.json`: egyptair `min_price:893, attempts:1`). So a still-loading page WILL be accepted as final. The settle is a blind `wait_for_timeout(2500)` (`engine.py:567`) with no "results ready" assertion. A drifted aria format (e.g. `round trip total` rephrased) yields `entries=[] → min_price=None` indistinguishable from genuine no-flights (`engine.py:439-440, 467-469`).
- **AC2**: S9 detects only the `"something went wrong"` string (`engine.py:568`). Consent re-walls mid-scan, sign-in redirect, CAPTCHA, "unusual traffic", and `consent.google.com` re-appearing are NOT detected — they fall through to an empty parse → `min_price:None` row (no typed state). `_reject_consent` runs once before the loop only (`engine.py:534`).
- **AC3**: S8–S11 emit nothing to logs/traces. There is no record of URL attempted, attempt count actually used, snapshot length, regex match count, or why a row is unpriced. Reconstructing a failure post-hoc is impossible from telemetry alone.
- **AC5**: S7 reuses ONE page across all pairs — a single hung `page.goto` (45 s × 4 attempts) blocks the whole scan; no per-scan wall-clock budget or pair cap beyond `window_days/trip_days` math (`enumerate_pairs` can produce large products, e.g. 366-day window × many weekdays × 5 dests).

#### Edge cases to test (offline)
1. **Partial page**: replay `egyptair_snapshot.txt` (Loading/Fetching markers) — assert parser/loop classify it as `partial`, NOT a final priced row. *(Currently fails the AC1 intent.)*
2. **Drifted label**: fixture where `round trip total` → `round-trip total` (hyphen) — assert it is flagged as `drift`/`parser_miss`, not `min_price:None` no-flights (`engine.py:439`).
3. **Empty vs no-match**: fixture of a valid results page with literally zero fares vs a page whose every fare line failed the regex — these must yield distinct states.
4. **Collision-safe dates**: `dep == SEED_RET (2026-06-18)` round-trips correctly (covered by `test_date_swap.py:43`); add the nonstop seed-collision case (`test_date_swap.py:37`).
5. **Estimate=0**: `window_days=7, weekdays` that yield no valid deps → `/api/search` **422** (`main.py:300-301`).
6. **Large product bound**: `window_days=366, min=2,max=60, 5 dests` — assert `estimate_pairs` and an enforced upper bound (AC5) rather than an unbounded scan.
7. **Multi-dest error-row envelope**: confirm `dest` key present on error rows for configurable/multi-dest routes and absent for single-dest (`engine.py:581-582`; parity in `test_row_envelope_parity.py`).

---

## 3. Job state machine

States are `JobStatus` (`jobs.py:10-15`): `QUEUED → RUNNING → {COMPLETED | FAILED | CANCELLED}`.
Terminal = COMPLETED, FAILED, CANCELLED.

| From | To | Trigger | Who sets it | File:line |
|------|----|---------|-------------|-----------|
| (none) | **QUEUED** | `store.create` | `JobStore.create` | `jobs.py:73-79` |
| QUEUED | **RUNNING** | worker thread starts | `_run` in `start_worker` | `jobs.py:108-109` |
| RUNNING | **COMPLETED** | runner returns, not cancelled, status still not CANCELLED | `_run` | `jobs.py:114-116` |
| RUNNING | **FAILED** | runner raises any `Exception` | `_run` except-block | `jobs.py:117-120` |
| RUNNING/QUEUED | **CANCELLED** | `POST .../cancel` sets `Event`; status flipped directly; `_run` also flips on `cancel_check()` after runner | `JobStore.cancel` / `_run` | `jobs.py:92-95`, `jobs.py:111-113` |

- **Cancel mechanism**: a `threading.Event` per job (`jobs.py:54, 82`); `run_scan` polls `cancel_check()` at the top of both loops (`engine.py:538, 541`) — cancel takes effect only between pairs, not mid-`goto`.
- **Single-active**: `_active_id` (`jobs.py:53`) is set under lock at create (`jobs.py:81`) and cleared in `_run`'s `finally` (`jobs.py:121-124`). Released even on FAILED/CANCELLED.
- **Store eviction**: none. `self._jobs` grows unbounded; `list_jobs` only *displays* the newest 20 (`jobs.py:60-64`). Memory leak over a long-lived process (AC5).

#### Acceptance criteria — state machine
- **AC5**: Every terminal transition MUST clear `_active_id` so the next create is not wrongly 409'd (`jobs.py:121-124`). A runner that hangs (no exception, no return) leaves the job RUNNING forever and the gate stuck — there is no timeout/watchdog.
- **AC3**: FAILED jobs surface `job.error = str(exc)` and `message="Scan failed"` (`jobs.py:117-120`) — but mid-scan per-pair failures are swallowed into rows, so a job can COMPLETE "successfully" with every row unpriced and no top-level signal.
- **AC2**: Cancel during a wall (consent/CAPTCHA) only lands between pairs; a single pair stuck in `goto` retries (`engine.py:565-571`) ignores the cancel `Event`.

#### Edge cases to test (offline)
1. Runner raises → FAILED, `error` populated, `_active_id` cleared, next create succeeds (drive `run_scan` to raise via a fake page).
2. Cancel a QUEUED job before the thread flips RUNNING → CANCELLED, runner sees `cancel_check()` true early and breaks (`engine.py:538`).
3. Cancel mid-scan → loop breaks at next pair boundary, status CANCELLED not COMPLETED (`jobs.py:111-113`).
4. Completed job with all `min_price:None` rows → today COMPLETED with `winner:None`; assert a desired "completed-but-no-fares-with-reasons" distinction (AC2/AC3).
5. Hung runner simulation → assert a watchdog/timeout marks FAILED and frees the gate (AC5, currently absent).

---

## 4. External dependencies & how each is reached

| Dependency | Reached via | File:line | Failure surface |
|------------|-------------|-----------|-----------------|
| **Google Flights** (scraped target) | `page.goto(...travel/flights/search?tfs=...)`, parsed via `aria_snapshot()` | `engine.py:528-532, 566, 270, 407` | site drift, consent wall, anti-bot, "something went wrong", slow/dropped nav — all AC1/AC2/AC5 |
| **Playwright + headless Firefox** (browser pool of ONE) | `sync_playwright()` → `_launch_firefox` → `browser.new_context` → `new_page` | `engine.py:523-526, 480-499` | SIGABRT on build mismatch (`browser_env.py:47-76`); launch wrapped into `RuntimeError` with install hint (`engine.py:487-499`) → job FAILED |
| **Browser-build resolution** | `resolve_firefox_executable`, `expected_firefox_revision`, `prepare_playwright_browsers` | `browser_env.py` (whole file) | Cursor sandbox path redirect (`browser_env.py:79-100`); `PLAYWRIGHT_BROWSERS_PATH` env clobber |
| **DNS-over-HTTPS** (corporate-DNS workaround) | `FIREFOX_PREFS` (`network.trr.*` → `1.1.1.1`) | `engine.py:42-48` | DoH endpoint unreachable → `NS_ERROR_UNKNOWN_HOST` style nav failures |
| **Filesystem: built-in routes** | `routes.json` read at startup (fail-loud) | `registry.py:128-135` | missing/empty/corrupt → `RuntimeError` at import |
| **Filesystem: user routes** | `routes.user.json` read (fail-soft) + write on create/delete | `registry.py:137-160` | corrupt → logged + ignored (`registry.py:142-144`); not persistent across image rebuild |
| **Filesystem: sample data** | `dub_turkey_all_results.json`, `dub_turkey_saw_ayt.json`, `results-2026-05-27.json` | `main.py:391-421` | missing/`JSONDecodeError` → skipped |

No DB, no cache, jobs in-memory (`jobs.py:51`).

#### Acceptance criteria — dependencies
- **AC2/AC3**: Google Flights responses must be classified before parse — at minimum: `consent.google.com` in `page.url`, sign-in redirect host, "unusual traffic"/CAPTCHA text, "something went wrong" — each a distinct typed state with the offending URL + snapshot length logged.
- **AC5**: Exactly one browser/context/page is created per scan and closed in the normal path (`engine.py:604`), but `browser.close()` is **not** in a `finally` — an exception mid-loop leaks the browser process (the `sync_playwright()` context manager closes the driver, but the launched Firefox may dangle). Verify cleanup on exception.
- **AC4**: `routes.json` integrity (3 built-ins: `turkey` fixed_pairs, `egyptair` nonstop, `ams` daily) is regression-pinned by `test_registry.py:89` and `test_url_parity.py`.

#### Edge cases to test (offline)
1. Corrupt `routes.user.json` → registry boots, logs warning, ignores it (`test_registry.py:182`).
2. User route shadowing a built-in id → skipped (`registry.py:152-153`, `test_registry.py:182`).
3. `_launch_firefox` failure path → `RuntimeError` carries the expected-revision hint (`engine.py:490-499`); job → FAILED with that message.
4. `prepare_playwright_browsers` with a sandbox `PLAYWRIGHT_BROWSERS_PATH` → redirected/cleared per branch (`browser_env.py:87-98`).
5. Browser-cleanup-on-exception: force `parse_results` to raise mid-loop, assert no leaked browser (needs a `finally`/context guard — AC5).

---

## 5. Config flags / env vars / request params

| Name | Where | Default | Notes |
|------|-------|---------|-------|
| `ALLOWED_ORIGINS` | env, `main.py:59-63` | `*` | comma-sep; non-`*` enables `allow_credentials`. |
| `PLAYWRIGHT_BROWSERS_PATH` | env, `browser_env.py:84-98` | (unset) | sandbox paths redirected to user cache; missing path cleared. |
| `PORT` / `HOST` | **not read in app code** | n/a | Served by the ASGI runner (Dockerfile/uvicorn), not `app/`. Confirm in `Dockerfile`/`docker-compose.yml`. |
| `FIREFOX_PREFS` | constant, `engine.py:42-48` | TRR-only DoH via `1.1.1.1` | DNS workaround; differs from CLAUDE.md's `mozilla.cloudflare-dns.com` URL. |
| `_GOTO_ATTEMPTS` | constant, `engine.py:66` | `4` | retries only on "something went wrong". |
| `VIEWPORT` | constant, `engine.py:61` | `1280×1800` | passed to `new_context` (`engine.py:525`). |
| `LOCALE` | constant, `engine.py:62` | `en-IE` | `new_context` locale. |
| `BASE_TFS` / `NONSTOP_CAIRO_TFS` / `ONEWAY_CAI_DUB_TFS` | constants, `engine.py:51,184,238` | captured blobs | seed dates `2026-06-13/18`, `2026-07-02/09`, `2026-08-06`. |
| `LOCKED_ORIGIN` | constant, `registry.py:31` | `DUB` | origin pin. |
| `MAX_DESTINATIONS` | constant, `registry.py:32` | `5` | dest cap. |
| Goto timeouts | inline, `engine.py:531 (60 s init), 566 (45 s/attempt)` | — | settle 2500 ms (`engine.py:533,567`), backoff 3000 ms (`engine.py:571`). |
| **Request params** | `SearchRequest`/`ScanCreate` | see below | `currency=EUR`, `window_days=60 (7..366)`, `min_trip_days=3 (1..30)`, `max_trip_days=14 (2..60)`, `weekdays=Sat,Sun,Tue,Thu`, `adults=1 (1..9, echoed only)`, `nonstop=False`, `start=None→today`. `main.py:78-120` |

#### Acceptance criteria — config
- **AC3**: Timeouts/attempts/settle/backoff are bare literals; when a pair is unpriced after retries, the actual attempts-used and timeouts must be logged so the cause is reconstructable.
- **AC5**: `window_days≤366 × trip-day range × ≤5 dests` is the only bound on work; add an explicit total-pairs ceiling.
- **Doc drift to flag**: project `CLAUDE.md` prescribes `network.trr.uri = https://mozilla.cloudflare-dns.com/dns-query`, but `engine.py:45` uses `https://1.1.1.1/dns-query` (plus `confirmationNS=skip`). Pick one and pin it.

#### Edge cases to test (offline)
1. Param bounds: `window_days=6`→422, `=367`→422; `max_trip_days<min_trip_days`→422 (`test_search_api.py:137`).
2. `currency` injection: `EUR&hl=xx`, `A&B`, `EU` → 422 (`test_search_api.py:122-125`).
3. `start="not-a-date"` → 422 (`test_search_api.py:145`); `start=None` → today (`engine.py:360`).
4. Default weekday set produces deps; `weekdays="Xyz"` → `SystemExit`/error surfaced (`engine.py:341` raises `SystemExit` — should be a typed validation error, not `SystemExit`, in the API path).

---

## 6. Failure modes (with AC mapping & offline edge cases)

| Failure mode | Current behavior (file:line) | AC | Offline edge case |
|--------------|------------------------------|----|-------------------|
| **F1 Selector / aria-format drift** | Regex `From N euros round trip total.` no longer matches → `entries=[] → min_price=None`; looks like no-flights (`engine.py:439-440, 467-469`). | **AC1** | Fixture with rephrased label (hyphen/wording/locale) → expect `drift` state, not silent `None`. |
| **F2 Partial / "Loading" page** | Blind 2.5 s settle (`engine.py:567`); `egyptair_snapshot.txt` has `Loading results`/`Fetching results` yet parsed `min_price=893` (`golden_parses.json`). A not-yet-populated page parses as final. | **AC1** | Replay the egyptair partial fixture → must classify `partial`, retry/await readiness, never finalize. |
| **F3 Anti-bot / CAPTCHA / "unusual traffic" / sign-in redirect** | Not detected at all; only `"something went wrong"` is special-cased (`engine.py:568`). Wall → empty parse → `min_price:None` row. | **AC2/AC3** | Fixtures: CAPTCHA page, "unusual traffic" page, accounts.google.com redirect → each a distinct typed state. |
| **F4 Consent wall** | Rejected ONCE before the loop (`engine.py:534, 473-477`); if it re-appears mid-scan or `Reject all` button is renamed/absent, the 10 s click times out → exception → that single pair becomes an `error` row, scan continues. | **AC2** | Fixture/URL where `consent.google.com` re-appears mid-loop; fixture where button label differs. |
| **F5 "Something went wrong" transient** | Retried up to 4× with 3 s backoff (`engine.py:565-571`); if still bad → parsed as empty (`min_price:None`), NOT marked as error. | **AC1/AC5** | Fixture stuck on the error page for all 4 attempts → expect a typed `provider_error`, not unpriced. |
| **F6 Slow / dropped connection** | `goto` timeout 45 s/attempt → `TimeoutError` caught (`engine.py:572`) → `error` row, loop continues; init `goto` 60 s uncaught → whole scan FAILED (`engine.py:528-532`). | **AC3/AC5** | Fake page raising `TimeoutError` on goto → error row recorded with message; init-goto raise → job FAILED cleanly. |
| **F7 Malformed input** | Schema/validator → 422; Route `__post_init__` → 400 (bad IATA, nonstop≠CAI, date_strategy); non-alpha/4-letter dest blocked before URL (`engine.py:326-328`, `test_search_api.py:109-119`). | **AC1** (no injection into URL) | `CA;`, `C I`, `C1G`, `CDGX` → 400/422; currency injection → 422. |
| **F8 Concurrent requests** | 2nd create while one active → `RuntimeError` → 409 (`jobs.py:68-71`). | **AC5** | Two `/api/search` back-to-back → 2nd is 409; after first completes, 3rd succeeds. |
| **F9 Resource exhaustion / leaks** | `_jobs` dict never evicted (`jobs.py:51`); `browser.close()` not in `finally` (`engine.py:604`); one page reused — a hang stalls everything. | **AC5** | Many sequential jobs → assert job store eviction policy; exception mid-loop → assert browser closed. |
| **F10 Corrupt persistence** | `routes.json` corrupt → startup `RuntimeError` (fail-loud, `registry.py:131-135`); `routes.user.json` corrupt → ignored (fail-soft, `registry.py:142-144`); sample JSON corrupt → skipped (`main.py:402-403`). | **AC3** | Each corruption variant → correct loud/soft behavior; no 500 from `/api/saved-trips`. |
| **F11 Date-swap collision** | `_replace_dates` placeholder routing avoids swap bug (`engine.py:110-127`). | **AC4** | `dep==SEED_RET` and nonstop-seed collisions (`test_date_swap.py:37-49`). |
| **F12 Airline filter rejected by provider** | Known: filtered tfs → "Oops" page; run path forces `airline=None` and filters by `airline_name` in parser (`engine.py:144-150, 546-558`). | **AC1** | Confirm run path never builds an airline-filtered URL; assert `airline_name` filtering matches expected carriers on fixtures. |

---

## 7. Offline replay harness (test inventory)

The whole suite runs without a browser. Two replay shims named `SnapshotPage`/`_FakePage`
implement just `locator("body").aria_snapshot()` (`test_parser_parity.py:32-43`,
`capture_fixtures.py:63-78`, `test_oneway.py:26-34`); `test_search_api.py` swaps
`main.run_scan` for a synchronous priced-row fake (`test_search_api.py:28-64`).

| File | Guards | Key tests |
|------|--------|-----------|
| `tests/test_parser_parity.py` | parser output == legacy golden, both shapes | `test_parser_parity_all_routes`, `test_parser_shapes`, `test_shim_parse_results_signatures` |
| `tests/test_url_parity.py` | byte-swap URL parity + estimate counts + daily-weekday pin | `test_egyptair_engine_urls`, `test_turkey_engine_urls`, `test_ams_engine_urls`, `test_scanner_estimate_total_matches_golden` |
| `tests/test_date_swap.py` | collision-safe date swap | `test_*_not_swapped_on_seed_collision`, `test_dates_correct_for_non_colliding_pairs` |
| `tests/test_oneway.py` | one-way URL + `parse_oneway` filtering | `test_oneway_url_is_cai_dub_oneway_with_swapped_date`, nonstop/unfiltered |
| `tests/test_registry.py` | registry CRUD, validation, persistence, corrupt/shadow handling | `test_create_validation`, `test_corrupt_and_shadowing_user_file_ignored`, `test_user_routes_cannot_use_fixed_pairs` |
| `tests/test_row_envelope_parity.py` | single- vs multi-dest row envelope | `test_single_destination_envelope`, `test_multi_destination_envelope` |
| `tests/test_api_routes.py` | routes/scan HTTP surface, builtin-delete forbidden | `test_route_crud_via_api`, `test_delete_builtin_forbidden`, `test_scan_requires_route_id` |
| `tests/test_search_api.py` | `/api/search` surface (browser-free fake) | happy path, origin lock, nonstop-CAI, IATA/currency rejection, caps, adults note |
| Fixtures | `tests/fixtures/{egyptair,turkey,ams}_snapshot.txt`, `golden_parses.json`, `tests/golden_urls.json` | recorded **happy** snapshots + legacy outputs + URL parity |

### AC4 gap (the regression gate is happy-path-only)
- All three snapshot fixtures are **populated/priced** pages. There is **no** fixture for: drifted labels (F1), a genuinely-partial page asserted to be rejected (F2 — the egyptair fixture *contains* Loading text but is asserted to return `893`, i.e. the gate currently enforces the WRONG behavior), CAPTCHA / unusual-traffic / sign-in (F3), a stuck "something went wrong" page (F5), or an empty-but-valid results page (F1/AC1).
- **Required new fixtures + assertions (AC4):** `drift_snapshot.txt`, `partial_loading_snapshot.txt` (asserted → `partial`, NOT priced), `captcha_snapshot.txt`, `unusual_traffic_snapshot.txt`, `signin_redirect` (URL-based), `provider_error_snapshot.txt`, `empty_valid_snapshot.txt` — each pinned to a distinct typed state, so any future parser/loop edit that regresses failure-mode handling fails CI.

---

## 8. Top resilience defects (ranked, cite AC)

1. **Partial page parsed as final** — `egyptair_snapshot.txt` "Loading/Fetching results" → `min_price=893`; blind 2.5 s settle, no readiness gate. **AC1.** (`engine.py:567`, `golden_parses.json`)
2. **Walls indistinguishable from empty** — only `"something went wrong"` detected; consent-re-wall/CAPTCHA/unusual-traffic/sign-in fall through to `min_price:None`. **AC2.** (`engine.py:534, 568`)
3. **Zero scan-path logging/tracing** — no URL/attempts/snapshot-length/match-count/wall-type telemetry; empties are unexplainable. **AC3.** (`engine.py:502-605`)
4. **Drift looks like no-flights** — regex miss → `entries=[]` → `min_price:None` with no `parser_miss` signal. **AC1.** (`engine.py:439, 467`)
5. **Happy-path-only regression gate** — no drift/partial/blocked fixtures; the one partial fixture is asserted to the wrong (priced) result. **AC4.** (§7)
6. **No work/resource bounds** — unbounded `_jobs`, browser `close()` not in `finally`, one reused page, no per-scan timeout/pair-ceiling, cancel only between pairs. **AC5.** (`jobs.py:51`, `engine.py:604, 565-571`)

---

## 9. Resilience hardening — implemented

The §8 defects are now fixed. The backbone is a **typed per-pair outcome** so a
`min_price=None` row is never ambiguous, plus structured logging so every pair is
explainable from logs alone. All of it is verified **offline** (no live Google Flights).

### `ScanOutcome` + `classify_page` (AC1 / AC2)
`engine.classify_page(snap, url, *, match_count, min_price)` is a pure function returning one of:
`priced · empty · loading · drift_suspected · provider_error · wall_consent · wall_signin ·
wall_captcha · timeout · parse_error · error`. Decision order: **URL-keyed walls first**
(`consent.google.com` / `accounts.google.com`+`ServiceLogin` / `/sorry/`), then content walls
(`something went wrong`, `unusual traffic`/`recaptcha`, `Before you continue`+`Reject all`), then
`min_price` → `PRICED`, then rendered-rows → `EMPTY`, then loading markers → `LOADING`, then a
price-token-without-the-expected-label → `DRIFT_SUSPECTED`, else `EMPTY`.
**Sign-in is URL-only** — the `"Sign in"` header link (href `accounts.google.com/ServiceLogin`) is
present in *every* happy snapshot, so keying on snapshot text would mark all good scans as blocked.

### `run_scan` loop (AC1 / AC2 / AC3 / AC5)
`_scan_one_pair` parses **once per attempt**, classifies, and: retries `LOADING`/`PROVIDER_ERROR`
with exponential backoff (`_backoff_ms`); re-runs `_reject_consent` once on `WALL_CONSENT`; stops on a
usable price / true-empty / drift; **aborts the whole scan** (`raise ScanBlocked` → job `FAILED` +
`error_kind`) on `WALL_SIGNIN`/`WALL_CAPTCHA`. The parse now runs **inside** the per-pair `try` (one
bad page → one typed error row, scan continues), the warm-up goto+consent is wrapped and classified,
the browser is torn down in `try/finally`, and an optional `config['max_scan_seconds']` bounds wall-clock.

### Structured logging (AC3)
`log = getLogger("dub.engine")` / `"dub.jobs"`. One record per pair with
`{job_id, route_id, origin, dest, dep, ret, url, attempt, snapshot_len, match_count, min_price,
outcome, elapsed_ms}`; INFO at scan start/end; `log.exception(...)` (traceback) on failures; launch and
consent logs. Every result row also carries `outcome` (and error rows `error`), so `/api/scans/{id}/results`
explains an empty/blocked pair without a re-run.

### Parser anchoring (AC1), job state machine (AC5), boundaries & budgets (AC1/AC3/AC5)
- Airline filter anchored to the carrier clause (`_CARRIER_RE`), carrier class broadened to `[A-Za-z]`
  (lowercase-initial carriers). Legacy golden shapes preserved.
- `jobs.py`: terminal transitions are **write-once under the lock**; `cancel()` flips status under the
  lock only for `QUEUED`/`RUNNING`; `_run` catches `BaseException`; `_evict_locked` bounds `_jobs`.
- `_parse_weekdays` raises `ValueError` (not `SystemExit`) and skips blank tokens; `destinations_for`
  validates IATA before the protobuf byte-swap; `MAX_PAIRS_PER_SCAN` rejects runaway sweeps (422).

### AC → guarding test
| AC | Now enforced by |
|----|-----------------|
| **AC1** partial/drift/empty distinct | `test_classify_page` (loading→LOADING, drift→DRIFT, empty→EMPTY), `test_run_scan_resilience::{partial_page_is_retried_then_priced, drift_recorded_as_drift_not_empty}` |
| **AC2** walls typed, not empty | `test_classify_page` (consent/signin/captcha + happy-stay-PRICED guard), `test_run_scan_resilience::{persistent_provider_error_is_not_empty, signin_wall_aborts_scan_and_closes_browser}` |
| **AC3** diagnosable from logs | `test_run_scan_resilience::each_pair_emits_structured_log` (asserts outcome/match_count/snapshot_len/url/elapsed_ms) |
| **AC4** failure-mode gate | 7 `tests/fixtures/*_snapshot.txt` + `test_classify_page::failure_mode_fixtures_map_to_distinct_outcomes` |
| **AC5** budgets / no leak / state | `test_boundary_validation::scan_combination_budget_enforced`, `test_run_scan_resilience::{browser_closed_on_setup_exception, deadline_stops_scan_early}`, `test_job_state::*` |

> Note: the three **happy** fixtures and `golden_parses.json` are intentionally **unchanged**. The
> residual `Loading results` banner in `egyptair_snapshot.txt` is harmless because `classify_page`
> decides on `match_count`/`min_price`, not the banner — a populated page is `PRICED` regardless, while
> the new `loading_partial` fixture (zero priced rows) is `LOADING`, pinning the two states apart.
