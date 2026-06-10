"""Find the cheapest Dublin -> Turkey round-trip for August 2026 weekends + bank holiday.

Adapted from cheapest_dub_cai_egyptair.py. Reuses the captured EgyptAir tfs= blob
and performs two byte-level transformations:

1. Strip the EgyptAir (`MS`) airline filter so we see all carriers (and adjust
   the protobuf length prefix of each leg message).
2. Replace destination IATA (CAI -> IST / SAW / AYT) per scan.

Date pairs target the Irish August Bank Holiday long weekend (Mon 3 Aug 2026)
plus every Fri-Sun and Fri-Mon weekend in August 2026.

This script is now a thin CLI shim over the shared engine (app/engine.py); the
route (including the fixed Aug-2026 date pairs) is defined in app/route_defs.py.
CLI flags, stdout format and the legacy module-level helpers are preserved.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from app import engine
from app.engine import (  # noqa: F401  (legacy re-exports)
    BASE_RAW_NO_AIRLINE,
    BASE_TFS,
    FIREFOX_PREFS,
    SEED_DEP,
    SEED_RET,
    _b64u_decode,
    _b64u_encode,
    _reject_consent,
    strip_airline_filter,
)
from app.jobs import JobStatus, ScanJob
from app.route_defs import BUILTIN_ROUTES, TURKEY_AUGUST_2026_PAIRS

ROUTE = BUILTIN_ROUTES["turkey"]

DESTINATIONS = [
    ("IST", "Istanbul (IST)"),
    ("SAW", "Istanbul Sabiha (SAW)"),
    ("AYT", "Antalya (AYT)"),
]


def build_url(dep: dt.date, ret: dt.date, dest_iata: str, currency: str = "EUR") -> str:
    """Build Google Flights URL for DUB -> <dest_iata>, dates swapped, no airline filter."""
    return engine.build_url(dep, ret, dest=dest_iata, airline=None, currency=currency)


def parse_results(page) -> dict:
    """Read the cheapest 'From X euros round trip total' price (any airline)."""
    return engine.parse_results(page)


def august_2026_pairs() -> list[tuple[dt.date, dt.date]]:
    """Aug 2026 weekend + bank-holiday long-weekend combinations."""
    return [
        (dt.date.fromisoformat(d), dt.date.fromisoformat(r)) for d, r in TURKEY_AUGUST_2026_PAIRS
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cheapest DUB -> Turkey round-trip, Aug 2026 weekends.")
    ap.add_argument("--headed", action="store_true", help="Show browser window.")
    ap.add_argument(
        "--destinations",
        default=",".join(d[0] for d in DESTINATIONS),
        help="Comma-separated Turkey IATA codes to scan (default: IST,SAW,AYT).",
    )
    ap.add_argument("--currency", default="EUR")
    ap.add_argument("--output", default="dub_turkey_results.json")
    args = ap.parse_args(argv)

    config = {"destinations": args.destinations, "currency": args.currency}
    dests = list(engine.destinations_for(ROUTE, config))
    pairs = engine.enumerate_pairs(ROUTE, config)
    total = len(pairs) * len(dests)
    print(
        f"Scanning {total} combinations ({len(pairs)} date pairs x {len(dests)} dests: {dests})",
        flush=True,
    )

    job = ScanJob(id="cli", scanner=ROUTE.id, status=JobStatus.RUNNING, total=total)
    state = {"n": 0, "dest": None}

    def on_result(row: dict) -> None:
        state["n"] += 1
        n = state["n"]
        if row.get("dest") != state["dest"]:
            state["dest"] = row.get("dest")
            print(f"\n--- DUB -> {state['dest']} ---", flush=True)
        if row.get("error"):
            print(f"  #{n:2}/{total} nav error {row['dep']}->{row['ret']}: {row['error']}", flush=True)
            return
        top = (row["entries"] or [{}])[0]
        print(
            f"  #{n:2}/{total}  DUB->{row['dest']}  {row['dep']} ({row['dep_wd']}) -> {row['ret']} ({row['ret_wd']})"
            f"  {row['trip_days']}d  ->  EUR {row['min_price']}"
            f"  ({top.get('airline', '?')}, {top.get('stops', '?')})",
            flush=True,
        )

    engine.run_scan(
        ROUTE, job, lambda: False, config, headless=not args.headed, on_result=on_result
    )
    results = job.results

    Path(args.output).write_text(json.dumps(results, indent=2))

    priced = [r for r in results if r["min_price"] is not None]
    if not priced:
        print("\nNo priced results.")
        return 1

    priced.sort(key=lambda r: r["min_price"])

    print("\n" + "=" * 78)
    print("TOP 10 CHEAPEST DUB -> TURKEY  (Aug 2026 weekends + bank-holiday long weekend)")
    print("=" * 78)
    for i, r in enumerate(priced[:10], 1):
        top = (r["entries"] or [{}])[0]
        print(
            f"  {i:2}. EUR {r['min_price']:>4}  DUB->{r['dest']}  "
            f"{r['dep']} ({r['dep_wd']}) -> {r['ret']} ({r['ret_wd']})  "
            f"{r['trip_days']}d  ({top.get('airline', '?')}, {top.get('stops', '?')})"
        )

    best = priced[0]
    top = (best["entries"] or [{}])[0]
    print("\n" + "=" * 78)
    print(f"WINNER:  EUR {best['min_price']}  DUB -> {best['dest']}")
    print(f"  {best['dep']} ({best['dep_wd']}) -> {best['ret']} ({best['ret_wd']})  ({best['trip_days']} days)")
    print(f"  Airline: {top.get('airline', '?')}   Stops: {top.get('stops', '?')}   Class: {top.get('class', '?')}")
    print(f"  URL: {best['url']}")
    print("=" * 78)
    print(f"  Saved {len(results)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
