"""Generic route-scan engine extracted from the three legacy scrapers.

This consolidates the ~90%-duplicated logic of cheapest_dub_cai_egyptair.py,
cheapest_dub_turkey.py and cheapest_dub_ams.py behind a single Route dataclass
and one run_scan() loop. Behavior is preserved byte-for-byte where observable:

  * URL generation reuses the captured DUB-CAI EgyptAir `tfs=` blob with the
    exact same byte-level transformations (date swap, destination IATA swap,
    airline-filter strip). Origin therefore remains pinned to Dublin — the
    origin is encoded as the knowledge-graph ID /m/02cft, not ASCII.
  * parse_results() keeps the two legacy variants verbatim as two branches:
    the airline-filtered shape (cheapest_tab + snippet, used by the EgyptAir
    route) and the any-airline shape (airline/stops/snippet_head, entries
    capped at 5, used by Turkey/AMS).
  * Date-pair enumeration supports the two legacy strategies: "window"
    (weekday-constrained departure x return pairs over a date window) and
    "fixed_pairs" (Turkey's hardcoded Aug-2026 weekend list).

Regression-tested against tests/golden_urls.json and tests/fixtures/ — both
generated from the pre-refactor code (see tests/make_golden.py and
tests/capture_fixtures.py).
"""

from __future__ import annotations

import base64
import datetime as dt
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Callable

from playwright.sync_api import sync_playwright

from app.browser_env import (
    expected_firefox_revision,
    prepare_playwright_browsers,
    resolve_firefox_executable,
)
from app.jobs import ScanJob

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
SEED_DEST = b"CAI"
SEED_AIRLINE = b"MS"

VIEWPORT = {"width": 1280, "height": 1800}
LOCALE = "en-IE"

# Google Flights intermittently serves a transient "Oops, something went wrong"
# page; this many goto+reload attempts per pair before recording it unpriced.
_GOTO_ATTEMPTS = 4


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


BASE_RAW = _b64u_decode(BASE_TFS)
BASE_RAW_NO_AIRLINE = strip_airline_filter(BASE_RAW, SEED_AIRLINE)


def build_url(
    dep: dt.date,
    ret: dt.date,
    *,
    dest: str = "CAI",
    airline: str | None = "MS",
    currency: str = "EUR",
) -> str:
    """Build a Google Flights round-trip search URL from the captured blob.

    airline="MS" keeps the captured EgyptAir filter, None strips it, and any
    other 2-letter IATA code is swapped in place of MS (length-preserving).
    The destination swap covers both occurrences (outbound dest + return
    origin); the trip origin stays Dublin (see module docstring).

    WARNING: passing any airline (MS or otherwise) currently yields a URL that
    Google rejects with an "Oops, something went wrong" error page — the
    captured per-leg filter bytes no longer match Google's tfs schema (verified
    2026-06). run_scan therefore always calls this with airline=None and filters
    by airline name in parse_results(). The airline param is retained only so the
    golden URL-parity tests keep exercising the byte-swap path.
    """
    if airline is None:
        raw = BASE_RAW_NO_AIRLINE
    elif airline == "MS":
        raw = BASE_RAW
    elif len(airline) == 2:
        raw = BASE_RAW.replace(b"\x32\x02" + SEED_AIRLINE, b"\x32\x02" + airline.encode())
    else:
        raise ValueError(f"airline must be a 2-letter IATA code or None, got {airline!r}")
    if dest != "CAI":
        raw = raw.replace(SEED_DEST, dest.encode())
    raw = raw.replace(SEED_DEP, dep.strftime("%Y-%m-%d").encode(), 1)
    raw = raw.replace(SEED_RET, ret.strftime("%Y-%m-%d").encode(), 1)
    return (
        "https://www.google.com/travel/flights/search?"
        f"tfs={_b64u_encode(raw)}&tfu=EgYIABAAGAA&hl=en&curr={currency}"
    )


# --- Nonstop (direct-flight) search via a fresh, current-schema blob ----------
#
# BASE_TFS above is stale-schema: Google accepts it for a bare search but returns
# an "Oops, something went wrong" error page the moment ANY filter field is
# byte-patched in — verified 2026-06 for both the airline filter (\x32\x02<IATA>)
# and the stops filter (\x28\x00). Filtered searches therefore cannot be built
# from BASE_TFS at all.
#
# This blob was re-captured 2026-06 from a fresh DUB->Cairo round-trip search in
# the live UI, so it is current-schema and DOES accept an added stops filter.
# Dublin/Cairo are encoded as knowledge-graph IDs (/m/02cft, /m/01w2v), not the
# literal "CAI" airport code, so the blob is Cairo-specific; only the literal ISO
# dates are byte-swapped (that part of the encoding is schema-stable). EgyptAir is
# the only carrier flying DUB-CAI nonstop, so a nonstop-only search returns
# exactly the EgyptAir direct round trip at its real cheapest economy fare.
NONSTOP_CAIRO_TFS = (
    "CBwQAhooEgoyMDI2LTA3LTAyagwIAhIIL20vMDJjZnRyDAgDEggvbS8wMXcydho"
    "oEgoyMDI2LTA3LTA5agwIAxIIL20vMDF3MnZyDAgCEggvbS8wMmNmdEABSAFwAYIBCwj___________8BmAEB"
)
NONSTOP_CAIRO_RAW = _b64u_decode(NONSTOP_CAIRO_TFS)
NS_SEED_DEP = b"2026-07-02"
NS_SEED_RET = b"2026-07-09"


def _add_nonstop_filter(raw: bytes) -> bytes:
    """Insert the stops filter (\\x28\\x00 = field 5, value 0 = nonstop) per leg.

    Each leg is `\\x1a<len>\\x12\\n<10-byte ISO date>...`; we splice \\x28\\x00 in
    right after the date field and bump the single-byte leg length by 2 — the
    exact transform observed when toggling "Nonstop only" in the live UI.
    """
    out = bytearray()
    i = 0
    while i < len(raw):
        if raw[i] == 0x1A and i + 1 < len(raw) and raw[i + 1] < 0x80:
            leg_len = raw[i + 1]
            leg = raw[i + 2 : i + 2 + leg_len]
            if leg[:2] == b"\x12\n":  # leg starts with the date field
                new_leg = b"\x12\n" + leg[2:12] + b"\x28\x00" + leg[12:]
                out += bytes([0x1A, len(new_leg)]) + new_leg
                i += 2 + leg_len
                continue
        out.append(raw[i])
        i += 1
    return bytes(out)


def build_nonstop_url(dep: dt.date, ret: dt.date, *, currency: str = "EUR") -> str:
    """Build a nonstop-only round-trip DUB->Cairo Google Flights search URL.

    Date-swaps the current-schema NONSTOP_CAIRO blob and applies the stops=nonstop
    filter. Cairo is baked into the blob as a knowledge-graph ID, so this builder
    is DUB-Cairo only (see Route.nonstop's __post_init__ guard).
    """
    raw = NONSTOP_CAIRO_RAW.replace(NS_SEED_DEP, dep.strftime("%Y-%m-%d").encode(), 1)
    raw = raw.replace(NS_SEED_RET, ret.strftime("%Y-%m-%d").encode(), 1)
    raw = _add_nonstop_filter(raw)
    return (
        "https://www.google.com/travel/flights/search?"
        f"tfs={_b64u_encode(raw)}&tfu=KgIIAw&hl=en&curr={currency}"
    )


@dataclass(frozen=True)
class Route:
    """A scannable route. Phase 1: instances live in app/route_defs.py;
    Phase 2 moves them to a JSON registry."""

    id: str
    name: str
    subtitle: str
    origin: str
    destinations: tuple[str, ...]
    airline: str | None = None  # IATA code kept in the tfs filter; None = all airlines
    airline_name: str | None = None  # display name the airline-filtered parser matches on
    date_strategy: str = "window"  # "window" | "fixed_pairs"
    weekdays: str | None = None  # pinned operating days; None = honor config["weekdays"]
    fixed_pairs: tuple[tuple[str, str], ...] = ()
    configurable_destinations: bool = False  # honor config["destinations"] (Turkey)
    eta_minutes: int = 5
    nonstop: bool = False  # direct flights only, via the fresh Cairo blob (DUB-CAI only)

    def __post_init__(self) -> None:
        if self.date_strategy not in ("window", "fixed_pairs"):
            raise ValueError(f"unknown date_strategy {self.date_strategy!r}")
        if self.nonstop and self.destinations != ("CAI",):
            raise ValueError(
                f"route {self.id!r}: nonstop search is currently DUB→Cairo only "
                "(the nonstop tfs blob has Cairo baked in as a knowledge-graph ID)"
            )
        if self.date_strategy == "fixed_pairs" and not self.fixed_pairs:
            raise ValueError(f"route {self.id!r}: fixed_pairs strategy needs fixed_pairs")
        if not self.destinations:
            raise ValueError(f"route {self.id!r}: needs at least one destination")
        for code in self.destinations:
            if len(code) != 3 or not code.isalpha() or not code.isupper():
                raise ValueError(f"route {self.id!r}: bad destination IATA {code!r}")
        if self.airline is not None and len(self.airline) != 2:
            raise ValueError(f"route {self.id!r}: bad airline IATA {self.airline!r}")


_WEEKDAY_MAP = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


def _parse_weekdays(spec: str) -> set[int]:
    out: set[int] = set()
    for token in spec.split(","):
        t = token.strip().title()[:3]
        if t not in _WEEKDAY_MAP:
            raise SystemExit(f"unknown weekday {token!r}")
        out.add(_WEEKDAY_MAP[t])
    return out


def _valid_days(start: dt.date, end: dt.date, weekdays: set[int]) -> Iterator[dt.date]:
    d = start
    while d <= end:
        if d.weekday() in weekdays:
            yield d
        d += dt.timedelta(days=1)


def enumerate_pairs(route: Route, config: dict[str, Any]) -> list[tuple[dt.date, dt.date]]:
    """Enumerate (departure, return) date pairs exactly as the legacy scrapers did."""
    if route.date_strategy == "fixed_pairs":
        return [
            (dt.date.fromisoformat(d), dt.date.fromisoformat(r)) for d, r in route.fixed_pairs
        ]
    start_raw = config.get("start") or dt.date.today().isoformat()
    start = dt.date.fromisoformat(start_raw)
    end = start + dt.timedelta(days=int(config.get("window_days", 60)))
    weekdays = _parse_weekdays(route.weekdays or config.get("weekdays") or "Sat,Sun,Tue,Thu")
    min_days = int(config.get("min_trip_days", 3))
    max_days = int(config.get("max_trip_days", 14))
    deps = list(_valid_days(start + dt.timedelta(days=1), end, weekdays))
    if min_days == max_days:
        # Exact stay length: pin the return to dep + N regardless of weekday so
        # non-multiples-of-7 (e.g. 30) still yield a pair. Returns past the
        # window's end are dropped to match the windowed search bounds.
        return [
            (dep, ret)
            for dep in deps
            if (ret := dep + dt.timedelta(days=min_days)) <= end
        ]
    return [
        (dep, ret)
        for dep in deps
        for ret in _valid_days(
            dep + dt.timedelta(days=min_days),
            dep + dt.timedelta(days=max_days),
            weekdays,
        )
    ]


def destinations_for(route: Route, config: dict[str, Any]) -> tuple[str, ...]:
    if route.configurable_destinations and config.get("destinations"):
        dests = tuple(
            d.strip().upper() for d in str(config["destinations"]).split(",") if d.strip()
        )
        if dests:
            return dests
    return route.destinations


def estimate_pairs(route: Route, config: dict[str, Any]) -> int:
    return len(enumerate_pairs(route, config)) * len(destinations_for(route, config))


def parse_results(page, *, airline_name: str | None = None) -> dict:
    """Read the cheapest round-trip totals from the result list.

    Both branches are kept verbatim from the legacy scrapers (see module
    docstring); tests/test_parser_parity.py pins them to recorded outputs.
    """
    snap = page.locator("body").aria_snapshot()

    if airline_name:
        # Airline-filtered shape (legacy cheapest_dub_cai_egyptair.parse_results).
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

    # Any-airline shape (legacy cheapest_dub_turkey / cheapest_dub_ams parse_results).
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


def _reject_consent(page) -> None:
    if "consent.google.com" in page.url:
        page.get_by_role("button", name=re.compile(r"^Reject all$", re.I)).click(timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2500)


def _launch_firefox(p, *, headless: bool = True):
    resolved = resolve_firefox_executable()
    launch_kwargs: dict = {"headless": headless, "firefox_user_prefs": FIREFOX_PREFS}
    if resolved:
        launch_kwargs["executable_path"] = resolved
    try:
        return p.firefox.launch(**launch_kwargs)
    except Exception as exc:
        # The usual culprit is a Firefox build whose juggler protocol does not
        # match the installed Playwright, which aborts with SIGABRT on macOS.
        rev = expected_firefox_revision()
        hint = (
            f" Playwright expects Firefox build {rev}; "
            if rev
            else " "
        )
        raise RuntimeError(
            f"Failed to launch Firefox.{hint}"
            "run `python -m playwright install firefox` to install the matching build."
        ) from exc


def run_scan(
    route: Route,
    job: ScanJob,
    cancel_check: Callable[[], bool],
    config: dict[str, Any],
    *,
    headless: bool = True,
    on_result: Callable[[dict], None] | None = None,
) -> None:
    """Scan every destination x date-pair combination for a route.

    Appends one result row per combination to job.results and keeps
    job.total/done/message updated. on_result (used by the CLI shims for
    progress printing) fires after each row, success or error.
    """
    currency = config.get("currency", "EUR")
    dests = destinations_for(route, config)
    pairs = enumerate_pairs(route, config)
    job.total = len(pairs) * len(dests)

    prepare_playwright_browsers()
    with sync_playwright() as p:
        browser = _launch_firefox(p, headless=headless)
        ctx = browser.new_context(viewport=VIEWPORT, locale=LOCALE)
        page = ctx.new_page()

        page.goto(
            f"https://www.google.com/travel/flights?hl=en&curr={currency}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(2500)
        _reject_consent(page)

        n = 0
        for dest in dests:
            if cancel_check():
                break
            for dep, ret in pairs:
                if cancel_check():
                    break
                n += 1
                job.done = n - 1
                job.message = f"{route.origin} → {dest} · {dep.isoformat()} → {ret.isoformat()}"
                # The captured per-leg airline filter (\x32\x02<IATA>) in BASE_TFS
                # is rejected by Google as of 2026-06 — any airline-filtered tfs
                # returns an "Oops, something went wrong" page (verified for MS and
                # QR), so those pairs came back unpriced. Two paths instead:
                #   * nonstop routes (egyptair): a fresh current-schema blob +
                #     stops=nonstop filter → exactly the EgyptAir direct round trip
                #     (EgyptAir is the only nonstop DUB-CAI carrier), real prices.
                #   * all-airline routes: search UNFILTERED (BASE_TFS still works
                #     bare) and restrict by route.airline_name in parse_results().
                if route.nonstop:
                    url = build_nonstop_url(dep, ret, currency=currency)
                else:
                    url = build_url(dep, ret, dest=dest, airline=None, currency=currency)
                try:
                    # Google Flights intermittently serves an "Oops, something went
                    # wrong" page that recovers on reload; retry a few times so a
                    # transient error doesn't silently drop a date pair. Only the
                    # explicit error page triggers a retry — a genuinely empty result
                    # breaks out immediately and is recorded as unpriced.
                    for attempt in range(_GOTO_ATTEMPTS):
                        page.goto(url, wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(2500)
                        if "something went wrong" not in page.locator("body").aria_snapshot():
                            break
                        if attempt < _GOTO_ATTEMPTS - 1:
                            page.wait_for_timeout(3000)
                except Exception as exc:
                    row: dict[str, Any] = {
                        "dep": dep.isoformat(),
                        "ret": ret.isoformat(),
                        "min_price": None,
                        "error": str(exc),
                    }
                    # Legacy quirk preserved: only the multi-destination scan
                    # (Turkey) tagged its error rows with the destination.
                    if route.configurable_destinations or len(route.destinations) > 1:
                        row = {"dest": dest, **row}
                    job.results.append(row)
                    job.done = n
                    if on_result:
                        on_result(row)
                    continue
                data = parse_results(page, airline_name=route.airline_name)
                data.update(
                    dest=dest,
                    origin=route.origin,
                    dep=dep.isoformat(),
                    ret=ret.isoformat(),
                    dep_wd=dep.strftime("%a"),
                    ret_wd=ret.strftime("%a"),
                    trip_days=(ret - dep).days,
                    url=url,
                )
                job.results.append(data)
                job.done = n
                if on_result:
                    on_result(data)

        browser.close()
