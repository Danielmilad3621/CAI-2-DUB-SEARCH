# CAI ↔ DUB Cheapest EgyptAir Round-Trip Finder

A Python script that scans Google Flights for the cheapest **EgyptAir** round-trip
between **Dublin (DUB)** and **Cairo (CAI)** across all valid Sat/Sun/Tue/Thu
date pairings (EgyptAir's operating days on this route) within a configurable
window, then reports the absolute minimum it finds.

> First run, 2026-05-27, 60-day window, 3–14-day trips, 245 date pairs scanned →
> **cheapest: €692 round trip, Thu 2026-06-04 → Thu 2026-06-11**, both legs
> EgyptAir nonstop economy. Google's banner: *"Prices are currently low — €136
> cheaper than usual for your search."*

---

## 1. Problem statement

Google Flights does not expose a way to ask "what's the cheapest round-trip on a
specific airline across a date range, restricted to that airline's actual
operating days?"

For DUB ↔ CAI on EgyptAir, flights only operate on **Saturday, Sunday, Tuesday,
Thursday** (and the same set is valid for the return leg). The standard UI lets
you pick one date pair and filter by airline; it does **not** let you sweep all
valid (departure, return) combinations and rank them.

So we want:

- Origin: Dublin Airport (DUB)
- Destination: Cairo International Airport (CAI)
- Trip type: round trip
- Airline: **EgyptAir only**
- Valid departure days: `{Sat, Sun, Tue, Thu}` (EgyptAir's schedule)
- Valid return days: `{Sat, Sun, Tue, Thu}` (same)
- Trip length: 3 to 14 days (configurable)
- Window: next 60 days from "today" (configurable)
- Output: the (dep, ret) pair with the lowest total round-trip economy fare

## 2. Approach (and why)

There are roughly three ways to attack this:

| Approach | Pros | Cons |
|---|---|---|
| (a) Use an aggregator API (Skyscanner, Kiwi, Amadeus) | Programmatic | Needs API key, paid, rate limits, often weaker UI parity |
| (b) Drive the Google Flights UI with Playwright (click date inputs, change form fields, scroll the Date Grid) | Free, no API key | Slow, brittle to UI changes, lots of clicks |
| (c) Reverse-engineer Google Flights' URL parameters and iterate URLs directly | Free, fast, very stable | Requires one-time URL capture per (origin, dest, airline) |

We picked **(c)** because Google Flights encodes the entire search state —
origin, destination, dates, cabin class, and any active airline filter — in a
single base64-encoded protobuf URL parameter called `tfs=`. Once you know the
encoding, you can generate any search URL programmatically and just `goto()` it.

### 2.1 The `tfs=` trick

Step-by-step, the one-time capture process:

1. Open <https://www.google.com/travel/flights> in a browser.
2. Pick **Round trip**, type **Dublin** → **Cairo**, choose any two valid dates
   (e.g. 2026-06-13 → 2026-06-18).
3. On the results page, click the **Airlines** filter, untick "Select all", tick
   only **EgyptAir**, close the dialog.
4. Copy the URL out of the address bar. It looks like:

   ```
   https://www.google.com/travel/flights/search?tfs=CBwQAhonEgoyMDI2LTA2LTEzMgJNU2oMCAISCC9tLzAyY2Z0cgcIARIDQ0FJGicSCjIwMjYtMDYtMTgyAk1TagcIARIDQ0FJcgwIAhIIL20vMDJjZnRAAUgBcAGCAQsI____________AZgBAQ&tfu=EgYIABAAGAA&hl=en&curr=EUR
   ```

5. Base64-URL-decode the `tfs=` value. The bytes look like:

   ```
   b"\x08\x1c\x10\x02\x1a'\x12\n2026-06-13\x32\x02MSj\x0c\x08\x02\x12\x08/m/02cftr\x07\x08\x01\x12\x03CAI\x1a'\x12\n2026-06-18\x32\x02MSj\x07\x08\x01\x12\x03CAIr\x0c\x08\x02\x12\x08/m/02cft@\x01H\x01p\x01\x82\x01\x0b\x08\xff…\xff\xff\x01\x98\x01\x01"
   ```

   Notice that the **dates are literal ASCII** (`2026-06-13`, `2026-06-18`), the
   **airline IATA code** is literal ASCII (`MS` = EgyptAir), and the **airport
   code** is literal ASCII (`CAI`). The other bytes are protobuf framing.

6. **Key insight:** swap the date strings in the raw bytes (length-preserving —
   any ISO date is 10 ASCII bytes) and re-encode with base64. That's a valid
   `tfs=` value for the same route + airline filter with new dates. No
   protobuf library needed.

```python
import base64
BASE_TFS = "CBwQAhonEgoyMDI2LTA2LTEzMgJNU2oMCAIS..."   # captured once
raw = base64.urlsafe_b64decode(BASE_TFS + "==")

def url_for(dep, ret):
    new = raw.replace(b"2026-06-13", dep.encode(), 1)
    new = new.replace(b"2026-06-18", ret.encode(), 1)
    b64 = base64.urlsafe_b64encode(new).decode().rstrip("=")
    return f"https://www.google.com/travel/flights/search?tfs={b64}&tfu=EgYIABAAGAA&hl=en&curr=EUR"
```

That single function is the heart of the scraper.

### 2.2 Iterating valid date pairs

EgyptAir flies DUB ↔ CAI on Sat/Sun/Tue/Thu. We enumerate every (dep, ret)
pair where:

- both dates are in the operating-day set,
- `min_trip_days ≤ (ret - dep) ≤ max_trip_days`,
- `today < dep ≤ today + window_days`.

For a 60-day window and 3–14-day trips, that's about **245 pairs**.

### 2.3 Scraping the result

For each generated URL, we open it headless in Firefox and read the result list
via Playwright's `aria_snapshot()`. The Best/Cheapest tab and each listed
itinerary expose `aria-label`s of the form:

```
From 692 euros round trip total. Nonstop flight with EgyptAir.
Leaves Dublin Airport at 2:20 PM on Thursday, June 4 and arrives at
Cairo International Airport at 9:45 PM on Thursday, June 4.
Total duration 5 hr 25 min. Select flight
```

A short regex extracts `692` plus the cabin class if mentioned (the snippet
contains literal "Business Class" / "Premium economy" when those tiers apply;
absence implies economy).

We store every pair's price into a JSON file and pick the minimum at the end.

### 2.4 Why headless Firefox (not Chromium)?

Two reasons:

1. **TLS / HTTP/2 fingerprinting** — some Google properties intermittently
   return `ERR_HTTP2_PROTOCOL_ERROR` for headless Chromium but work fine in
   headless Firefox.
2. **DNS workaround needed locally** — on this laptop the corporate DNS
   resolver occasionally SERVFAILs arbitrary hostnames, including
   `www.google.com`. Firefox supports TRR (DNS-over-HTTPS) prefs that route DNS
   directly to `1.1.1.1`, bypassing the OS resolver:

   ```python
   FIREFOX_PREFS = {
       "network.trr.mode": 3,                       # TRR-only
       "network.trr.uri": "https://1.1.1.1/dns-query",
       "network.trr.bootstrapAddress": "1.1.1.1",
       "network.trr.confirmationNS": "skip",
   }
   browser = p.firefox.launch(headless=True, firefox_user_prefs=FIREFOX_PREFS)
   ```

### 2.5 Consent wall

First navigation to `google.com/travel/flights` from an EU IP redirects to
`consent.google.com`. The script clicks **Reject all** once at session start
(cookie-less, so this happens every fresh run), then continues normally.

## 3. The full plan in extreme detail

This is the actual sequence the script (and the iterative exploration that
built it) executed:

### Phase A — Discovery (one-time, manual)

1. Launch headless Firefox via Playwright with the DNS-over-HTTPS prefs above.
2. `goto("https://www.google.com/travel/flights?hl=en&curr=EUR")`.
3. Dismiss the EU consent wall (`Reject all`).
4. Type "Cairo" into the "Where to?" combobox; pick "Cairo International (CAI)".
5. Fill Departure with `2026-06-13`, Return with `2026-06-18`. Press Enter.
6. Click the **Search** button.
7. On the results page, click the **Airlines** filter chip.
8. Toggle off "Select all airlines", then tick **EgyptAir** only.
9. Close the dialog. The URL bar now contains a `tfs=` value with `MS`
   (EgyptAir) baked into the airline-filter section of the protobuf.
10. Copy that `tfs=` value — it becomes `BASE_TFS` in the script.

This is captured once and committed; it does not need to be re-done unless
Google changes the protobuf schema.

### Phase B — Search-space enumeration

For each Sat/Sun/Tue/Thu date `dep` in `(today, today + 60d]`:
  For each Sat/Sun/Tue/Thu date `ret` in `[dep + 3d, dep + 14d]`:
    Append `(dep, ret)` to the pair list.

For the run on 2026-05-27 this produced **245 pairs**.

### Phase C — Per-pair scrape

For each pair `(dep, ret)`:

1. Build the URL via the `tfs=` byte-swap trick.
2. `page.goto(url)` (consent is already dismissed, so no extra step).
3. Wait ~2.5 s for the result list to render.
4. `aria_snapshot()` the page and regex-extract the **lowest** `From NNN euros
   round trip total. … EgyptAir …` price.
5. Append `{dep, ret, dep_wd, ret_wd, trip_days, min_price, entries, url}` to
   the results list.

Typical per-pair latency: 5–7 s. The 245-pair sweep took ~28 minutes.

### Phase D — Aggregation and verification

1. Sort results by `min_price` ascending.
2. Re-navigate to the cheapest pair's URL and screenshot:
   - The Best results list (showing the outbound EgyptAir flight at the target
     price, with the **EgyptAir** filter chip visible at the top to prove the
     filter is still applied).
   - The Returning flights list (clicked into the outbound to confirm the
     **return leg is also operated by EgyptAir nonstop**, not a code-share via
     a partner).
3. Write all 245 rows to `results.json` for offline ranking / grouping.

### Phase E — Reporting

Print the cheapest combo to stdout and also append it to `final_script_log.txt`.

## 4. Findings on the 2026-05-27 run

- **Cheapest price found:** **€692**
- **Best dates:** Thu **2026-06-04** → Thu **2026-06-11** (7-day trip)
- **Outbound:** EgyptAir nonstop, DUB 14:20 → CAI 21:45 (5 h 25 m)
- **Return:** EgyptAir nonstop, CAI 09:30 → DUB 13:20 (5 h 50 m)
- **Cabin:** Economy
- **Trips also at €692 (same price, different lengths):** Sun Jun 7 → Thu Jun 11 (4d), Sun Jun 7 → Tue Jun 16 (9d), Sun Jun 7 → Thu Jun 18 (11d), Sun Jun 7 → Sat Jun 20 (13d), Tue Jun 9 → Tue Jun 16 (7d), Tue Jun 9 → Thu Jun 18 (9d), Tue Jun 9 → Sat Jun 20 (11d), Thu Jun 4 → Tue Jun 16 (12d), Thu Jun 4 → Thu Jun 18 (14d), and more.
- **After mid-June** prices climb sharply (typical €730–€1005 for July).

See `results-2026-05-27.json` for the raw per-pair data; the two screenshots in
`screenshots/` are visual confirmation that both legs are EgyptAir nonstop with
the EgyptAir filter chip visible.

## 5. How to reproduce

### 5.1 Prerequisites

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install playwright
playwright install firefox
```

### 5.2 Run with defaults (60-day window, 3–14-day trips)

```bash
python cheapest_dub_cai_egyptair.py
```

### 5.3 Common variations

```bash
# Look further out (e.g. autumn 2026)
python cheapest_dub_cai_egyptair.py --start 2026-09-01 --window-days 90

# Allow longer stays
python cheapest_dub_cai_egyptair.py --max-trip-days 21

# Restrict to Saturday departures, any valid return
python cheapest_dub_cai_egyptair.py --weekdays Sat,Tue,Thu

# Save to a custom location
python cheapest_dub_cai_egyptair.py --output ~/Desktop/dub_cai_prices.json

# Show the browser window (debug)
python cheapest_dub_cai_egyptair.py --headed
```

### 5.4 All CLI options

| Flag | Default | Meaning |
|---|---|---|
| `--start` | today | Earliest departure date (`YYYY-MM-DD`). |
| `--window-days` | 60 | Days from `--start` to scan. |
| `--min-trip-days` | 3 | Minimum trip length (days). |
| `--max-trip-days` | 14 | Maximum trip length (days). |
| `--weekdays` | `Sat,Sun,Tue,Thu` | Operating days (3-letter, comma-separated). |
| `--currency` | `EUR` | Display currency code. |
| `--output` | `results.json` | Path to save per-pair JSON. |
| `--headed` | off | Show the browser window. |

### 5.5 Runtime

Each pair takes 5–7 seconds. With defaults (~245 pairs) plan for ~25 minutes.
Narrow `--window-days` or `--max-trip-days` to cut linearly. Use `--max-trip-days 7`
for a ~5-minute scan focused on short trips.

## 6. Repurposing for other routes / airlines

The script's route + airline are pinned by the captured `tfs=` blob at the top
of the file. To swap them:

1. Open Google Flights manually, fill in your origin/destination, dates, and
   airline filter.
2. Copy the URL once the filter chip is visible.
3. Decode `tfs=` to bytes and verify the airline IATA code and airport code
   appear as literal ASCII (most do).
4. Replace `BASE_TFS`, `SEED_DEP`, `SEED_RET` at the top of
   `cheapest_dub_cai_egyptair.py`.
5. Update the parser's airline-name pattern in `parse_results(...)` if the new
   airline's display name differs (default is `EgyptAir`).

The protobuf encoding has been stable for years — but if Google ever changes
it, the fallback is the slower form-driving approach (fill the form fields and
click filter buttons every iteration).

## 7. Limitations / honest caveats

- **Aggregator prices, not booking prices.** Google Flights surfaces fares
  from partner OTAs and the airline itself; final price at checkout can differ
  by a few euros (luggage, payment fees, dynamic pricing within ~24 h).
- **Single passenger, one-way-bagless.** The protobuf encodes `1 adult,
  Economy`. Add bags, change pax count, or change cabin → re-capture `tfs=`.
- **Window cap.** Google Flights itself only sells fares ~330 days ahead.
- **No partner / codeshare expansion.** Filtering on `EgyptAir` keeps only
  EgyptAir-marketed flights (which on DUB-CAI is the daily MS direct). If a
  codeshare with Lufthansa/EgyptAir would be cheaper via a connection, that's
  excluded — by design.
- **Headless Firefox + DoH prefs are required** on networks with flaky DNS.
  On a normal network you can remove the `FIREFOX_PREFS` block.
- **EU consent wall.** Geolocated outside the EU you may see a different
  consent flow; the `Reject all` click is the only one handled.

## 8. Files in this repo

| File | Purpose |
|---|---|
| `cheapest_dub_cai_egyptair.py` | The CLI script. |
| `results-2026-05-27.json` | Raw output from the 2026-05-27 run (245 pairs, 240 priced). |
| `screenshots/outbound-cheapest.png` | Evidence: outbound result list, EgyptAir filter chip visible. |
| `screenshots/return-egyptair.png` | Evidence: returning flights list, also EgyptAir nonstop. |
| `README.md` | This file. |

## 9. Deployment

The app is split into a **static frontend** (Vercel) and a **long-running backend** (Docker on a VPS). They cannot be merged: the backend needs a persistent Firefox process and scans that run 5–90 minutes, both incompatible with serverless.

### Environment variables

| Side | Variable | Required | Description |
|------|----------|----------|-------------|
| Frontend (Vercel) | `VITE_API_BASE_URL` | Yes (prod) | Full URL of the backend, e.g. `https://api.yourdomain.com`. Omit in local dev — Vite's proxy handles `/api` automatically. |
| Backend (Docker) | `ALLOWED_ORIGINS` | Yes (prod) | Comma-separated list of allowed CORS origins, e.g. `https://your-app.vercel.app`. Defaults to `http://localhost:5173,http://127.0.0.1:5173`. |
| Backend (Docker) | `PORT` | No | Uvicorn listen port. Defaults to `8000`. |
| Backend (Docker) | `CURRENCY` | No | Default currency code passed to scrapers. Currently hardcoded to `EUR` per scanner config — this is a placeholder for a future flag. |

> **Limitations (v1):** Job state is in-process memory. A backend restart loses any running job. Only one scan can run at a time. A future version could back jobs with SQLite or Redis.

---

### 9.1 Deploy the frontend to Vercel

1. Push the repo to GitHub (or connect the existing remote).
2. Create a new Vercel project and set **Root Directory** to `frontend/`.
3. Vercel auto-detects Vite; the `frontend/vercel.json` in this repo sets the build command, output dir, and SPA rewrite.
4. Add the environment variable `VITE_API_BASE_URL` = `https://<your-backend-host>` in the Vercel dashboard under **Settings → Environment Variables**.
5. Redeploy (or let Vercel trigger on the next push).

### 9.2 Deploy the backend to Hostinger VPS (Docker)

```bash
# 1. Copy the repo to the VPS (first time)
scp -r /path/to/CAI-2-DUB-SEARCH root@<VPS_IP>:/opt/dub-search

# 2. SSH in
ssh root@<VPS_IP>

# 3. Set the CORS origin (replace with your actual Vercel URL)
export ALLOWED_ORIGINS="https://your-app.vercel.app"

# 4. Build and start
cd /opt/dub-search
docker compose up -d --build

# 5. Verify
curl http://localhost:8000/api/health
# → {"status":"ok"}
```

To update after code changes:
```bash
git pull
docker compose up -d --build
```

**Alternatives to Hostinger VPS:** Render, Railway, and Fly.io can all run this Docker image. The only requirement is a long-lived container with port 8000 exposed; no special storage is needed.

### 9.3 Local development

```bash
# Start backend + frontend dev server (Vite proxies /api → localhost:8000)
bash scripts/dev.sh
```

Frontend: http://localhost:5173 — Backend: http://localhost:8000

---

## 10. Background

Originally built as a one-off task in a Webwright-style Playwright workspace,
then parameterized into the CLI shipped here. See commit history for the
exploration scripts and screenshots that led to the final approach.
