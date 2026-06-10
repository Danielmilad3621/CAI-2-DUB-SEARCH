"""Find the cheapest EgyptAir round-trip Dublin <-> Cairo via Google Flights.

Iterates valid Sat/Sun/Tue/Thu departure × Sat/Sun/Tue/Thu return pairs (EgyptAir's
operating days on the DUB-CAI route) within a configurable window and reports the
cheapest fare it finds.

This script is now a thin CLI shim over the shared engine (app/engine.py); the
route is defined in app/route_defs.py. CLI flags, stdout format and the legacy
module-level helpers (build_url, parse_results, …) are preserved.

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
import datetime as dt
import json
import sys
from pathlib import Path

from app import engine
from app.engine import (  # noqa: F401  (legacy re-exports)
    BASE_RAW,
    BASE_TFS,
    FIREFOX_PREFS,
    SEED_DEP,
    SEED_RET,
    _b64u_decode,
    _b64u_encode,
    _parse_weekdays,
    _reject_consent,
    _valid_days,
)
from app.jobs import JobStatus, ScanJob
from app.route_defs import BUILTIN_ROUTES

ROUTE = BUILTIN_ROUTES["egyptair"]


def build_url(dep: dt.date, ret: dt.date, currency: str = "EUR") -> str:
    """Build a Google Flights search URL for a given DUB-CAI EgyptAir date pair."""
    return engine.build_url(dep, ret, dest="CAI", airline=ROUTE.airline, currency=currency)


def parse_results(page, airline_name: str = "EgyptAir") -> dict:
    """Read the cheapest matching round-trip total from the result list."""
    return engine.parse_results(page, airline_name=airline_name)


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

    config = {
        "start": args.start,
        "window_days": args.window_days,
        "min_trip_days": args.min_trip_days,
        "max_trip_days": args.max_trip_days,
        "weekdays": args.weekdays,
        "currency": args.currency,
    }
    deps = list(_valid_days(start + dt.timedelta(days=1), end, weekdays))
    pairs = engine.enumerate_pairs(ROUTE, config)

    print(
        f"Scanning {len(pairs)} pairs ({len(deps)} departures × valid returns), "
        f"window {start} → {end}, trip {args.min_trip_days}-{args.max_trip_days}d, "
        f"weekdays {sorted(weekdays)}",
        flush=True,
    )

    job = ScanJob(id="cli", scanner=ROUTE.id, status=JobStatus.RUNNING, total=len(pairs))
    counter = {"i": 0}

    def on_result(row: dict) -> None:
        counter["i"] += 1
        i = counter["i"]
        if row.get("error"):
            print(f"  #{i} nav error {row['dep']}->{row['ret']}: {row['error']}", flush=True)
            return
        print(
            f"  #{i}/{len(pairs)}  {row['dep']} ({row['dep_wd']}) → {row['ret']} ({row['ret_wd']})  "
            f"{row['trip_days']}d  →  €{row['min_price']}",
            flush=True,
        )

    engine.run_scan(
        ROUTE, job, lambda: False, config, headless=not args.headed, on_result=on_result
    )
    results = job.results

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
