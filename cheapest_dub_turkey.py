"""Find the cheapest Dublin -> Turkey round-trip for August 2026 weekends + bank holiday.

Adapted from cheapest_dub_cai_egyptair.py. Reuses the captured EgyptAir tfs= blob
and performs two byte-level transformations:

1. Strip the EgyptAir (`MS`) airline filter so we see all carriers (and adjust
   the protobuf length prefix of each leg message).
2. Replace destination IATA (CAI -> IST / SAW / AYT) per scan.

Date pairs target the Irish August Bank Holiday long weekend (Mon 3 Aug 2026)
plus every Fri-Sun and Fri-Mon weekend in August 2026.
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
    "network.trr.mode": 3,
    "network.trr.uri": "https://1.1.1.1/dns-query",
    "network.trr.bootstrapAddress": "1.1.1.1",
    "network.trr.confirmationNS": "skip",
}

# Original DUB -> CAI EgyptAir captured tfs (seed dates 2026-06-13 / 2026-06-18).
BASE_TFS = (
    "CBwQAhonEgoyMDI2LTA2LTEzMgJNU2oMCAISCC9tLzAyY2Z0cgcIARIDQ0FJ"
    "GicSCjIwMjYtMDYtMTgyAk1TagcIARIDQ0FJcgwIAhIIL20vMDJjZnRAAUgB"
    "cAGCAQsI____________AZgBAQ"
)
SEED_DEP = b"2026-06-13"
SEED_RET = b"2026-06-18"

DESTINATIONS = [
    ("IST", "Istanbul (IST)"),
    ("SAW", "Istanbul Sabiha (SAW)"),
    ("AYT", "Antalya (AYT)"),
]


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def strip_airline_filter(raw: bytes, airline_iata: bytes = b"MS") -> bytes:
    """Remove `\\x32\\x02<iata>` from each leg block and decrement the leg length.

    Protobuf structure (in this captured blob):
      \\x1a<len><leg-bytes>   -- repeated for outbound + return
    The airline filter `\\x32\\x02MS` is 4 bytes inside <leg-bytes>.
    Leg lengths are single-byte varints (< 128), so adjusting is trivial.
    """
    pattern = b"\x32\x02" + airline_iata  # 4 bytes
    out = bytearray()
    i = 0
    while i < len(raw):
        # `\x1a` = field 3 (leg), wire type 2 (length-delimited).
        if raw[i] == 0x1A and i + 1 < len(raw):
            leg_len = raw[i + 1]
            if leg_len < 0x80:  # single-byte varint length
                leg = raw[i + 2 : i + 2 + leg_len]
                if pattern in leg:
                    new_leg = leg.replace(pattern, b"", 1)
                    out.append(0x1A)
                    out.append(len(new_leg))
                    out.extend(new_leg)
                    i = i + 2 + leg_len
                    continue
        out.append(raw[i])
        i += 1
    return bytes(out)


# Pre-strip the airline filter once.
BASE_RAW_NO_AIRLINE = strip_airline_filter(_b64u_decode(BASE_TFS), b"MS")


def build_url(dep: dt.date, ret: dt.date, dest_iata: str, currency: str = "EUR") -> str:
    """Build Google Flights URL for DUB -> <dest_iata>, dates swapped, no airline filter."""
    raw = BASE_RAW_NO_AIRLINE
    # Swap destination IATA (appears twice: outbound dest + return origin).
    raw = raw.replace(b"CAI", dest_iata.encode())
    # Swap dates.
    raw = raw.replace(SEED_DEP, dep.strftime("%Y-%m-%d").encode(), 1)
    raw = raw.replace(SEED_RET, ret.strftime("%Y-%m-%d").encode(), 1)
    return (
        "https://www.google.com/travel/flights/search?"
        f"tfs={_b64u_encode(raw)}&tfu=EgYIABAAGAA&hl=en&curr={currency}"
    )


def _reject_consent(page) -> None:
    if "consent.google.com" in page.url:
        page.get_by_role("button", name=re.compile(r"^Reject all$", re.I)).click(timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2500)


def august_2026_pairs() -> list[tuple[dt.date, dt.date]]:
    """Aug 2026 weekend + bank-holiday long-weekend combinations."""
    pairs = [
        # Bank Holiday long weekend (Mon 3 Aug is the Irish August Bank Holiday).
        ("2026-07-31", "2026-08-03"),  # Fri -> Mon
        ("2026-07-31", "2026-08-04"),  # Fri -> Tue
        ("2026-08-01", "2026-08-03"),  # Sat -> Mon
        ("2026-08-01", "2026-08-04"),  # Sat -> Tue
        # Weekend Aug 7-10
        ("2026-08-07", "2026-08-09"),  # Fri -> Sun
        ("2026-08-07", "2026-08-10"),  # Fri -> Mon
        # Weekend Aug 14-17
        ("2026-08-14", "2026-08-16"),
        ("2026-08-14", "2026-08-17"),
        # Weekend Aug 21-24
        ("2026-08-21", "2026-08-23"),
        ("2026-08-21", "2026-08-24"),
        # Weekend Aug 28-31
        ("2026-08-28", "2026-08-30"),
        ("2026-08-28", "2026-08-31"),
    ]
    return [(dt.date.fromisoformat(d), dt.date.fromisoformat(r)) for d, r in pairs]


def parse_results(page) -> dict:
    """Read the cheapest 'From X euros round trip total' price (any airline)."""
    snap = page.locator("body").aria_snapshot()

    entries: list[dict] = []
    pattern = r"From\s+(\d[\d,]*)\s+euros round trip total\.([^\"]{0,500})"
    for m in re.finditer(pattern, snap):
        price = int(m.group(1).replace(",", ""))
        snippet = m.group(0)
        rest = m.group(2)

        airline_match = re.search(
            r"(?:flight|flights)\s+(?:with|operated by)\s+([A-Z][A-Za-z0-9 &\-\.]+?)\.", rest
        )
        airline = airline_match.group(1).strip() if airline_match else None

        cls = next(
            (c for c in ("Business Class", "Premium economy", "First Class") if c in snippet),
            "Economy",
        )
        stops_match = re.search(r"(Nonstop|\d+\s+stops?)", rest)
        stops = stops_match.group(0) if stops_match else None

        entries.append(
            {
                "price": price,
                "airline": airline,
                "stops": stops,
                "class": cls,
                "snippet_head": snippet[:280],
            }
        )

    return {
        "min_price": min((e["price"] for e in entries), default=None),
        "entries": entries[:5],
    }


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

    dests = [d.strip().upper() for d in args.destinations.split(",") if d.strip()]
    pairs = august_2026_pairs()
    total = len(pairs) * len(dests)
    print(
        f"Scanning {total} combinations ({len(pairs)} date pairs x {len(dests)} dests: {dests})",
        flush=True,
    )

    results: list[dict] = []
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=not args.headed, firefox_user_prefs=FIREFOX_PREFS)
        ctx = browser.new_context(viewport={"width": 1280, "height": 1800}, locale="en-IE")
        page = ctx.new_page()

        # Prime consent once.
        page.goto(
            "https://www.google.com/travel/flights?hl=en&curr=" + args.currency,
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(2500)
        _reject_consent(page)

        n = 0
        for dest in dests:
            print(f"\n--- DUB -> {dest} ---", flush=True)
            for dep, ret in pairs:
                n += 1
                url = build_url(dep, ret, dest, currency=args.currency)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    print(f"  #{n:2}/{total} nav error {dep}->{ret}: {e}", flush=True)
                    continue
                page.wait_for_timeout(2500)
                data = parse_results(page)
                data.update(
                    dest=dest,
                    dep=dep.isoformat(),
                    ret=ret.isoformat(),
                    dep_wd=dep.strftime("%a"),
                    ret_wd=ret.strftime("%a"),
                    trip_days=(ret - dep).days,
                    url=url,
                )
                results.append(data)
                top = (data["entries"] or [{}])[0]
                print(
                    f"  #{n:2}/{total}  DUB->{dest}  {dep} ({data['dep_wd']}) -> {ret} ({data['ret_wd']})"
                    f"  {data['trip_days']}d  ->  EUR {data['min_price']}"
                    f"  ({top.get('airline', '?')}, {top.get('stops', '?')})",
                    flush=True,
                )

        browser.close()

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
