"""Find the cheapest EgyptAir round-trip Dublin <-> Cairo via Google Flights.

Iterates valid Sat/Sun/Tue/Thu departure × Sat/Sun/Tue/Thu return pairs (EgyptAir's
operating days on the DUB-CAI route) within a configurable window and reports the
cheapest fare it finds.

The route + airline are hardcoded via a captured Google Flights `tfs=` query
parameter (a base64-encoded protobuf). To repurpose this script for a different
route or airline, capture a fresh URL by running an interactive search on
google.com/travel/flights and paste its `tfs=` value into BASE_TFS below.

Usage:
    python cheapest_dub_cai_egyptair.py
    python cheapest_dub_cai_egyptair.py --start 2026-09-01 --window-days 90
    python cheapest_dub_cai_egyptair.py --min-trip-days 5 --max-trip-days 21
    python cheapest_dub_cai_egyptair.py --weekdays Sat,Sun,Tue,Thu --currency EUR

Args:
    --start: Earliest departure date (inclusive). Default: today.
    --window-days: Days from `start` to include. Default: 60.
    --min-trip-days: Minimum trip length. Default: 3.
    --max-trip-days: Maximum trip length. Default: 14.
    --weekdays: Comma-separated 3-letter operating days. Default: Sat,Sun,Tue,Thu.
    --currency: Display currency code. Default: EUR.
    --output: Path to write per-pair JSON results. Default: results.json.
    --headed: Show the browser window instead of running headless.

Dependencies: playwright (with `playwright install firefox`).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

FIREFOX_PREFS = {
    # Bypass corporate DNS via direct-IP DoH endpoint.
    "network.trr.mode": 3,
    "network.trr.uri": "https://1.1.1.1/dns-query",
    "network.trr.bootstrapAddress": "1.1.1.1",
    "network.trr.confirmationNS": "skip",
}

# Captured tfs: round-trip DUB-CAI 2026-06-13 / 2026-06-18 with EgyptAir (MS) filter.
BASE_TFS = (
    "CBwQAhonEgoyMDI2LTA2LTEzMgJNU2oMCAISCC9tLzAyY2Z0cgcIARIDQ0FJ"
    "GicSCjIwMjYtMDYtMTgyAk1TagcIARIDQ0FJcgwIAhIIL20vMDJjZnRAAUgB"
    "cAGCAQsI____________AZgBAQ"
)
SEED_DEP = b"2026-06-13"
SEED_RET = b"2026-06-18"


def _b64u_decode(s: str) -> bytes:
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def _b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


BASE_RAW = _b64u_decode(BASE_TFS)


def build_url(dep: dt.date, ret: dt.date, currency: str = "EUR") -> str:
    """Build a Google Flights search URL for a given DUB-CAI EgyptAir date pair."""
    raw = BASE_RAW.replace(SEED_DEP, dep.strftime("%Y-%m-%d").encode(), 1)
    raw = raw.replace(SEED_RET, ret.strftime("%Y-%m-%d").encode(), 1)
    return (
        "https://www.google.com/travel/flights/search?"
        f"tfs={_b64u_encode(raw)}&tfu=EgYIABAAGAA&hl=en&curr={currency}"
    )


def _reject_consent(page) -> None:
    if "consent.google.com" in page.url:
        page.get_by_role("button", name=re.compile(r"^Reject all$", re.I)).click(timeout=8000)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2000)


_WEEKDAY_MAP = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


def _parse_weekdays(spec: str) -> set[int]:
    out: set[int] = set()
    for token in spec.split(","):
        t = token.strip().title()[:3]
        if t not in _WEEKDAY_MAP:
            raise SystemExit(f"unknown weekday {token!r}")
        out.add(_WEEKDAY_MAP[t])
    return out


def _valid_days(start: dt.date, end: dt.date, weekdays: set[int]):
    d = start
    while d <= end:
        if d.weekday() in weekdays:
            yield d
        d += dt.timedelta(days=1)


def parse_results(page, airline_name: str = "EgyptAir") -> dict:
    """Read the cheapest matching round-trip total from the result list."""
    snap = page.locator("body").aria_snapshot()

    cheapest_tab = None
    m = re.search(r"Cheapest from\s+(\d[\d,]*) euros", snap)
    if m:
        cheapest_tab = int(m.group(1).replace(",", ""))

    entries = []
    pattern = (
        r"From\s+(\d[\d,]*)\s+euros round trip total\.[^\"]*"
        + re.escape(airline_name)
        + r"[^\"]*"
    )
    for m in re.finditer(pattern, snap):
        price = int(m.group(1).replace(",", ""))
        snippet = m.group(0)
        cls = next(
            (c for c in ("Business Class", "Premium economy", "First Class", "Economy") if c in snippet),
            None,
        )
        entries.append({"price": price, "class": cls, "snippet": snippet[:300]})

    return {
        "cheapest_tab": cheapest_tab,
        "min_price": min((e["price"] for e in entries), default=None),
        "entries": entries,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cheapest EgyptAir DUB-CAI round-trip via Google Flights.")
    ap.add_argument("--start", default=dt.date.today().isoformat(), help="Earliest departure date (YYYY-MM-DD).")
    ap.add_argument("--window-days", type=int, default=60)
    ap.add_argument("--min-trip-days", type=int, default=3)
    ap.add_argument("--max-trip-days", type=int, default=14)
    ap.add_argument("--weekdays", default="Sat,Sun,Tue,Thu", help="Comma-separated operating days (3-letter).")
    ap.add_argument("--currency", default="EUR")
    ap.add_argument("--output", default="results.json")
    ap.add_argument("--headed", action="store_true", help="Show browser window.")
    args = ap.parse_args(argv)

    start = dt.date.fromisoformat(args.start)
    end = start + dt.timedelta(days=args.window_days)
    weekdays = _parse_weekdays(args.weekdays)

    deps = list(_valid_days(start + dt.timedelta(days=1), end, weekdays))
    pairs = [
        (dep, ret)
        for dep in deps
        for ret in _valid_days(
            dep + dt.timedelta(days=args.min_trip_days),
            dep + dt.timedelta(days=args.max_trip_days),
            weekdays,
        )
    ]

    print(
        f"Scanning {len(pairs)} pairs ({len(deps)} departures × valid returns), "
        f"window {start} → {end}, trip {args.min_trip_days}-{args.max_trip_days}d, "
        f"weekdays {sorted(weekdays)}",
        flush=True,
    )

    results = []
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=not args.headed, firefox_user_prefs=FIREFOX_PREFS)
        ctx = browser.new_context(viewport={"width": 1280, "height": 1800}, locale="en-IE")
        page = ctx.new_page()

        page.goto("https://www.google.com/travel/flights?hl=en&curr=" + args.currency,
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        _reject_consent(page)

        for i, (dep, ret) in enumerate(pairs, start=1):
            url = build_url(dep, ret, currency=args.currency)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                print(f"  #{i} nav error {dep}->{ret}: {e}", flush=True)
                continue
            page.wait_for_timeout(2500)
            data = parse_results(page)
            data.update(
                dep=dep.isoformat(),
                ret=ret.isoformat(),
                dep_wd=dep.strftime("%a"),
                ret_wd=ret.strftime("%a"),
                trip_days=(ret - dep).days,
                url=url,
            )
            results.append(data)
            print(
                f"  #{i}/{len(pairs)}  {dep} ({data['dep_wd']}) → {ret} ({data['ret_wd']})  "
                f"{data['trip_days']}d  →  €{data['min_price']}",
                flush=True,
            )

        browser.close()

    Path(args.output).write_text(json.dumps(results, indent=2))

    priced = [r for r in results if r["min_price"] is not None]
    if not priced:
        print("No priced results.")
        return 1

    priced.sort(key=lambda r: r["min_price"])
    cheapest = priced[0]
    print()
    print("=" * 60)
    print(f"CHEAPEST: €{cheapest['min_price']}")
    print(f"  Outbound: {cheapest['dep']} ({cheapest['dep_wd']})")
    print(f"  Return:   {cheapest['ret']} ({cheapest['ret_wd']})")
    print(f"  Trip:     {cheapest['trip_days']} days")
    print(f"  URL:      {cheapest['url']}")
    print("=" * 60)
    print(f"  (Saved {len(results)} rows to {args.output})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
