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
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from playwright.sync_api import sync_playwright

from app.browser_env import (
    expected_firefox_revision,
    prepare_playwright_browsers,
    resolve_firefox_executable,
)
from app.jobs import ScanJob

# Structured logging for the scan path (mirrors app/registry.py's "dub.registry").
# Every per-pair outcome is logged with an `extra=` field set so an empty/failed
# pair is explainable from logs alone (no live re-run needed).
log = logging.getLogger("dub.engine")

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
# page, and the result list lazy-loads after navigation. These tunables drive the
# per-pair navigate/settle/retry loop; all are overridable via the scan `config`.
_GOTO_ATTEMPTS = 4          # navigate+reload attempts per pair before giving up
_GOTO_TIMEOUT_MS = 45000    # per-navigation timeout
_SETTLE_MS = 2500           # wait after each goto for the result list to render
_BACKOFF_BASE_MS = 1500     # base wait between retry attempts (grows exponentially)
_BACKOFF_MAX_MS = 8000      # cap on the per-attempt backoff wait

# Hard upper bound on (destination x date-pair) combinations a single scan may
# enqueue. Each combination is a real headless navigation, so an unbounded sweep
# (366 days x 7 weekdays x 5 dests) would schedule a multi-day job; the API
# rejects anything above this with 422 (see app/main.py).
MAX_PAIRS_PER_SCAN = 1500

# If a consent wall survives its one-shot dismissal on this many consecutive pairs,
# the session is effectively stuck — abort rather than burn the whole pair budget
# re-hitting it (consent is normally transient/dismissible, hence a small threshold).
_MAX_CONSECUTIVE_CONSENT = 3


class ScanOutcome(str, Enum):
    """Typed classification of a per-pair page state.

    The whole point of AC1/AC2/AC3 resilience: a `min_price=None` row is
    meaningless on its own — it could be a genuinely empty date pair, a page that
    was still loading, a drifted price-label format, or an anti-bot/consent/
    sign-in wall. Every recorded row and every log line carries one of these so
    those cases are never conflated.
    """

    PRICED = "priced"                  # at least one fare parsed
    EMPTY = "empty"                    # page rendered, genuinely no flights
    LOADING = "loading"                # still populating after all attempts
    DRIFT_SUSPECTED = "drift_suspected"  # rows rendered but 0 price-regex matches
    PROVIDER_ERROR = "provider_error"  # "something went wrong" after all attempts
    WALL_CONSENT = "wall_consent"      # consent.google.com / "Before you continue"
    WALL_SIGNIN = "wall_signin"        # accounts.google.com / ServiceLogin redirect
    WALL_CAPTCHA = "wall_captcha"      # "unusual traffic" / reCAPTCHA / /sorry/
    TIMEOUT = "timeout"                # navigation/parse timed out
    PARSE_ERROR = "parse_error"        # parser raised on this page
    ERROR = "error"                    # other navigation exception

# A wall that means the browser session is burned: abort the whole scan rather
# than silently recording every remaining pair as empty.
_BLOCKING_OUTCOMES = frozenset({ScanOutcome.WALL_SIGNIN, ScanOutcome.WALL_CAPTCHA})


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


def _replace_dates(
    raw: bytes, dep_seed: bytes, ret_seed: bytes, dep: dt.date, ret: dt.date
) -> bytes:
    """Swap the outbound + return seed dates collision-safely.

    A naive `raw.replace(dep_seed, dep, 1)` then `raw.replace(ret_seed, ret, 1)`
    is WRONG when the new departure string equals ret_seed (e.g. dep == the seed
    return date): the first replace creates a second copy of ret_seed, so the
    second replace targets the outbound leg instead of the return one and the two
    dates end up swapped. Route each date through a unique placeholder so every
    leg is rewritten exactly once. Output is byte-identical to the naive form for
    every non-colliding pair, so URL-parity goldens are unaffected.
    """
    raw = raw.replace(dep_seed, b"\x00__DEP__\x00", 1)
    raw = raw.replace(ret_seed, b"\x00__RET__\x00", 1)
    raw = raw.replace(b"\x00__DEP__\x00", dep.strftime("%Y-%m-%d").encode(), 1)
    raw = raw.replace(b"\x00__RET__\x00", ret.strftime("%Y-%m-%d").encode(), 1)
    return raw


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
    raw = _replace_dates(raw, SEED_DEP, SEED_RET, dep, ret)
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
    raw = _replace_dates(NONSTOP_CAIRO_RAW, NS_SEED_DEP, NS_SEED_RET, dep, ret)
    raw = _add_nonstop_filter(raw)
    return (
        "https://www.google.com/travel/flights/search?"
        f"tfs={_b64u_encode(raw)}&tfu=KgIIAw&hl=en&curr={currency}"
    )


# --- One-way Cairo -> Dublin search -------------------------------------------
#
# Captured 2026-06 from a live one-way CAI->DUB search (all airlines, no stops
# filter). One-way is encoded by the TRAILING trip-type field \x98\x01\x02
# (round trip = \x98\x01\x01; field 2 stays \x10\x02 in both). Cairo/Dublin are
# knowledge-graph ids (/m/01w2v, /m/02cft), so this builder is CAI->DUB only and
# only the literal ISO departure date is byte-swapped (the schema-stable part).
ONEWAY_CAI_DUB_TFS = (
    "CBwQAhooEgoyMDI2LTA4LTA2agwIAxIIL20vMDF3MnZyDAgDEggvbS8wMmNmdEABSAFwAYIBCwj"
    "___________8BmAEC"
)
ONEWAY_CAI_DUB_RAW = _b64u_decode(ONEWAY_CAI_DUB_TFS)
OW_SEED_DEP = b"2026-08-06"


def build_oneway_url(dep: dt.date, *, currency: str = "EUR") -> str:
    """Build a one-way Cairo->Dublin Google Flights search URL (all airlines).

    Date-swaps the captured one-way CAI->DUB blob. Cairo/Dublin are baked in as
    knowledge-graph ids so this is CAI->DUB only; results include every carrier,
    so filter to nonstop / a given airline in parse_oneway().
    """
    raw = ONEWAY_CAI_DUB_RAW.replace(OW_SEED_DEP, dep.strftime("%Y-%m-%d").encode(), 1)
    return (
        "https://www.google.com/travel/flights/search?"
        f"tfs={_b64u_encode(raw)}&hl=en&curr={currency}"
    )


def parse_oneway(
    page, *, airline_name: str | None = None, nonstop_only: bool = False
) -> dict:
    """Read one-way fares from the result list.

    One-way labels read 'From N euros. <Nonstop|N stops> flight with <airline>.
    Leaves <origin> ... arrives at <dest> ...' -- note NO 'round trip total'
    (that is the round-trip shape parsed by parse_results). Optionally keep only
    nonstop rows and/or rows whose carrier matches airline_name.
    """
    snap = page.locator("body").aria_snapshot()
    pattern = (
        r"From\s+(\d[\d,]*)\s+euros\.\s*(Nonstop|\d+\s+stops?)?\s*"
        r"flight with\s+([A-Za-z][A-Za-z0-9 &\-\.]+?)\.([^\"]{0,300})"
    )
    entries: list[dict] = []
    for m in re.finditer(pattern, snap):
        price = int(m.group(1).replace(",", ""))
        stops = m.group(2)
        airline = m.group(3).strip() if m.group(3) else None
        cls = next(
            (c for c in ("Business Class", "Premium economy", "First Class") if c in m.group(0)),
            "Economy",
        )
        if nonstop_only and stops != "Nonstop":
            continue
        if airline_name and (not airline or airline_name.lower() not in airline.lower()):
            continue
        entries.append(
            {"price": price, "airline": airline, "stops": stops, "class": cls,
             "snippet_head": m.group(0)[:280]}
        )
    return {"min_price": min((e["price"] for e in entries), default=None), "entries": entries[:5]}


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
    """Parse a comma-separated weekday spec into weekday indices.

    Raises ValueError (NOT SystemExit) on an unknown token or an empty result.
    SystemExit is a BaseException that the API's `except Exception` estimate
    guard could not catch, so a bad weekday returned an opaque HTTP 500 (and a
    worker thread could die leaving a job stuck RUNNING). ValueError flows through
    the existing 400 wrapper. Blank/trailing-comma tokens are skipped so 'Sat,'
    parses to {Sat} — matching registry._validate_weekdays.
    """
    out: set[int] = set()
    for token in spec.split(","):
        t = token.strip().title()[:3]
        if not t:
            continue
        if t not in _WEEKDAY_MAP:
            raise ValueError(f"unknown weekday {token!r}")
        out.add(_WEEKDAY_MAP[t])
    if not out:
        raise ValueError(f"no valid weekdays in {spec!r}")
    return out


def _valid_iata(code: str) -> bool:
    """A destination IATA code is exactly 3 alphabetic characters."""
    return len(code) == 3 and code.isalpha()


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
        # Validate before these reach build_url's protobuf byte-replace
        # (dest.encode()): a configurable-destinations route is the one path where
        # caller-supplied codes bypass Route.__post_init__ validation.
        for code in dests:
            if not _valid_iata(code):
                raise ValueError(f"bad destination IATA code: {code!r} (expected 3 letters)")
        if dests:
            return dests
    return route.destinations


def estimate_pairs(route: Route, config: dict[str, Any]) -> int:
    return len(enumerate_pairs(route, config)) * len(destinations_for(route, config))


# A priced round-trip row: "From <N> euros round trip total. <rest up to the
# closing aria quote>". group(1)=price, group(2)=the rest of the label.
_RT_ROW_RE = re.compile(r"From\s+(\d[\d,]*)\s+euros round trip total\.([^\"]{0,500})")
# Carrier clause inside a row label: "... flight with EgyptAir." / "operated by KLM".
# Lowercase initial allowed (easyJet, flydubai, airBaltic).
_CARRIER_RE = re.compile(
    r"(?:flight|flights)\s+(?:with|operated by)\s+([A-Za-z][A-Za-z0-9 &\-\.]+?)\."
)
_CHEAPEST_TAB_RE = re.compile(r"Cheapest from\s+(\d[\d,]*) euros")
# A rendered-but-unrecognized price token: a number adjacent to a currency glyph
# or word. Distinguishes a drifted price-label format (rows rendered, our row
# regex matched 0) from a genuinely flight-less page (no price tokens at all).
# NOTE: EUR-only, like _RT_ROW_RE — a non-EUR currency drift carries neither '€'
# nor 'euros', so it would classify EMPTY rather than DRIFT_SUSPECTED. Pre-existing
# (the price regex was always EUR-only); revisit both if non-EUR routes are enabled.
_PRICE_TOKEN_RE = re.compile(r"€\s*\d|\b\d[\d,]*\s+euros\b", re.I)


def count_price_rows(snap: str) -> int:
    """Number of 'round trip total' rows present, regardless of airline filter.

    This is the 'did the result list render in the expected shape' signal used by
    classify_page; it is independent of which rows survive an airline filter.
    """
    return sum(1 for _ in _RT_ROW_RE.finditer(snap))


def classify_page(
    snap: str, url: str = "", *, match_count: int, min_price: int | None
) -> ScanOutcome:
    """Classify a post-navigation page into a typed ScanOutcome. Pure / offline-safe.

    URL-keyed wall checks come FIRST and key on the page URL, NEVER the snapshot
    text: every happy results page carries a header "Sign in" link whose href is
    accounts.google.com/ServiceLogin (see tests/fixtures/egyptair_snapshot.txt:28),
    so detecting sign-in from the snapshot would misclassify 100% of successful
    scans as blocked. A genuine sign-in/consent/CAPTCHA wall is a *redirect*,
    visible in page.url.

    Resilience bias: when uncertain between "rendered-but-unparseable" and
    "genuinely empty", prefer DRIFT_SUSPECTED (investigate) over EMPTY (trust).
    """
    u = url or ""
    if "consent.google.com" in u:
        return ScanOutcome.WALL_CONSENT
    if "accounts.google.com" in u or "/ServiceLogin" in u or "/signin/" in u:
        return ScanOutcome.WALL_SIGNIN
    if "/sorry/" in u:
        return ScanOutcome.WALL_CAPTCHA

    low = snap.lower()
    if "something went wrong" in low:
        return ScanOutcome.PROVIDER_ERROR
    if "unusual traffic" in low or "not a robot" in low or "recaptcha" in low:
        return ScanOutcome.WALL_CAPTCHA
    if "before you continue to google" in low:
        # Domain-specific heading; no need to also require the "Reject all" button
        # text (a consent variant could rename it). URL-keyed consent is the primary
        # path; this is the content fallback.
        return ScanOutcome.WALL_CONSENT

    if min_price is not None:
        return ScanOutcome.PRICED
    if match_count > 0:
        # Rows rendered in the expected shape but none usable (e.g. an airline
        # filter excluded them all) — a legitimate "no matching flights", not drift.
        return ScanOutcome.EMPTY
    if "loading results" in low or "fetching results" in low:
        return ScanOutcome.LOADING
    if _PRICE_TOKEN_RE.search(snap):
        return ScanOutcome.DRIFT_SUSPECTED
    return ScanOutcome.EMPTY


def parse_snapshot(snap: str, *, airline_name: str | None = None) -> dict:
    """Parse cheapest round-trip totals out of a raw aria-snapshot string.

    Split from parse_results so run_scan can snapshot the page ONCE and reuse the
    exact text for both classification and the recorded row. Output shapes are
    preserved verbatim from the legacy scrapers (tests/test_parser_parity.py pins
    them): airline-filtered (cheapest_tab + uncapped {price,class,snippet}) and
    any-airline ({price,airline,stops,class,snippet_head}, capped at 5).
    """
    if airline_name:
        # Airline-filtered shape (legacy cheapest_dub_cai_egyptair.parse_results).
        cheapest_tab = None
        m = _CHEAPEST_TAB_RE.search(snap)
        if m:
            cheapest_tab = int(m.group(1).replace(",", ""))

        entries = []
        for m in _RT_ROW_RE.finditer(snap):
            rest = m.group(2)
            # Anchor the airline to the carrier clause rather than accepting the
            # name appearing anywhere in the label: a cheaper competitor row whose
            # label merely *mentions* the target carrier (e.g. a comparison clause)
            # must NOT be attributed to it.
            carrier_m = _CARRIER_RE.search(rest)
            carrier = carrier_m.group(1).strip() if carrier_m else None
            if not carrier or airline_name.lower() not in carrier.lower():
                continue
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
    for m in _RT_ROW_RE.finditer(snap):
        price = int(m.group(1).replace(",", ""))
        snippet = m.group(0)
        rest = m.group(2)

        airline_match = _CARRIER_RE.search(rest)
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


def parse_results(page, *, airline_name: str | None = None) -> dict:
    """Read the cheapest round-trip totals from a Playwright page's result list.

    Thin wrapper over parse_snapshot for callers that hold a page object (the CLI
    shims and the parity tests' SnapshotPage). run_scan calls parse_snapshot
    directly on the snapshot it already captured.
    """
    return parse_snapshot(page.locator("body").aria_snapshot(), airline_name=airline_name)


class ScanBlocked(RuntimeError):
    """Abort a scan because an anti-bot / sign-in / CAPTCHA wall was hit.

    Once the session is walled, every subsequent pair would be blocked too, so we
    stop rather than silently recording the rest as 'empty'. The worker marks the
    job FAILED and surfaces job.error_kind (the ScanOutcome value) so a wall is a
    distinct, diagnosable terminal state — not an opaque timeout (AC2).
    """

    def __init__(self, outcome: ScanOutcome) -> None:
        self.outcome = outcome
        super().__init__(f"scan aborted: {outcome.value} wall detected")


def _page_url(page) -> str:
    return getattr(page, "url", "") or ""


def _backoff_ms(attempt: int) -> int:
    """Exponential backoff (deterministic small jitter) between retry attempts.

    Jitter is derived from the attempt index rather than random() so the backoff
    is testable, while still de-synchronizing successive retries enough to avoid
    a fixed-cadence pattern that re-trips anti-bot heuristics.
    """
    base = min(_BACKOFF_MAX_MS, _BACKOFF_BASE_MS * (2 ** attempt))
    jitter = (attempt * 137) % 250
    return min(_BACKOFF_MAX_MS, base + jitter)


def _reject_consent(page) -> None:
    if "consent.google.com" in _page_url(page):
        page.get_by_role("button", name=re.compile(r"^Reject all$", re.I)).click(timeout=10000)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(_SETTLE_MS)
        log.info("consent wall rejected")


def _launch_firefox(p, *, headless: bool = True):
    resolved = resolve_firefox_executable()
    launch_kwargs: dict = {"headless": headless, "firefox_user_prefs": FIREFOX_PREFS}
    if resolved:
        launch_kwargs["executable_path"] = resolved
    log.info("launching firefox", extra={"executable": resolved, "headless": headless})
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
        log.exception("firefox launch failed")
        raise RuntimeError(
            f"Failed to launch Firefox.{hint}"
            "run `python -m playwright install firefox` to install the matching build."
        ) from exc


def _scan_one_pair(page, url: str, airline_name: str | None, config: dict[str, Any]):
    """Navigate to `url`, polling until a terminal page state, classify it.

    Returns (outcome, parsed, snap, match_count, attempt). All browser I/O lives
    here. Retries on LOADING / PROVIDER_ERROR with exponential backoff (so a
    still-populating page is NOT misread as empty); re-handles a consent wall once;
    stops immediately on a usable price, a true-empty/drift page, or a blocking
    wall. Parses ONCE per attempt and reuses that snapshot for both the decision
    and the recorded row.
    """
    attempts = int(config.get("goto_attempts", _GOTO_ATTEMPTS))
    timeout_ms = int(config.get("goto_timeout_ms", _GOTO_TIMEOUT_MS))
    settle_ms = int(config.get("settle_ms", _SETTLE_MS))
    consent_retried = False
    outcome = ScanOutcome.EMPTY
    parsed: dict[str, Any] = {"min_price": None, "entries": []}
    snap = ""
    match_count = 0
    attempt = 0
    for attempt in range(attempts):
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_timeout(settle_ms)
        snap = page.locator("body").aria_snapshot()
        parsed = parse_snapshot(snap, airline_name=airline_name)
        match_count = count_price_rows(snap)
        outcome = classify_page(
            snap, _page_url(page), match_count=match_count, min_price=parsed["min_price"]
        )
        if outcome == ScanOutcome.WALL_CONSENT and not consent_retried:
            _reject_consent(page)
            consent_retried = True
            continue  # re-read the page after dismissing consent
        # On an airline-filtered route, an EMPTY page that DID render competing-carrier
        # rows (match_count>0, but none ours) may simply still be loading OUR carrier —
        # keep polling within budget so a late-arriving target fare is not dropped (AC1).
        # A page with zero rows, or a non-filtered route, accepts EMPTY immediately.
        # classify_page itself is unchanged on purpose: the "Loading results" text
        # persists on fully-loaded pages, so the retry decision (not the classifier) is
        # where the still-loading-vs-genuinely-no-match distinction belongs.
        filtered_maybe_loading = (
            outcome == ScanOutcome.EMPTY and bool(airline_name) and match_count > 0
        )
        terminal = (
            outcome in (ScanOutcome.PRICED, ScanOutcome.DRIFT_SUSPECTED)
            or outcome in _BLOCKING_OUTCOMES
            or (outcome == ScanOutcome.EMPTY and not filtered_maybe_loading)
        )
        if terminal:
            break
        # LOADING / PROVIDER_ERROR / filtered-maybe-loading: back off + retry.
        if attempt < attempts - 1:
            page.wait_for_timeout(_backoff_ms(attempt))
    return outcome, parsed, snap, match_count, attempt


def _success_row(
    parsed: dict[str, Any], route: Route, dest: str, dep: dt.date, ret: dt.date,
    url: str, outcome: str,
) -> dict[str, Any]:
    """Parser output + the scan envelope + the typed outcome.

    The first 8 envelope keys (dest..url) and their order are the legacy contract
    pinned by tests/test_row_envelope_parity.py; `outcome` is the new typed field
    appended last (min_price=None rows are no longer indistinguishable). Returns a
    NEW dict (does not mutate `parsed`); the spread preserves the key-order contract.
    """
    return {
        **parsed,
        "dest": dest,
        "origin": route.origin,
        "dep": dep.isoformat(),
        "ret": ret.isoformat(),
        "dep_wd": dep.strftime("%a"),
        "ret_wd": ret.strftime("%a"),
        "trip_days": (ret - dep).days,
        "url": url,
        "outcome": outcome,
    }


def _error_row(
    route: Route, dest: str, dep: dt.date, ret: dt.date, error: str, outcome: str,
) -> dict[str, Any]:
    """Unpriced error/blocked row. dest-tagged only for multi-destination routes
    (legacy quirk preserved); `outcome` distinguishes timeout / parse_error / wall."""
    row: dict[str, Any] = {
        "dep": dep.isoformat(),
        "ret": ret.isoformat(),
        "min_price": None,
        "error": error,
        "outcome": outcome,
    }
    if route.configurable_destinations or len(route.destinations) > 1:
        row = {"dest": dest, **row}
    return row


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
    job.total/done/message updated. Every row carries a typed `outcome` and every
    pair emits a structured log record, so an empty/blocked pair is explainable
    from logs alone (AC3). A sign-in/CAPTCHA wall aborts the scan via ScanBlocked
    (AC2). The browser is always torn down (try/finally), and a per-scan deadline
    (config['max_scan_seconds'], optional) bounds wall-clock. on_result fires after
    each row, success or error (used by the CLI shims for progress printing).
    """
    currency = config.get("currency", "EUR")
    dests = destinations_for(route, config)
    pairs = enumerate_pairs(route, config)
    job.total = len(pairs) * len(dests)
    max_scan_seconds = config.get("max_scan_seconds")
    scan_started = time.monotonic()
    log.info(
        "scan start",
        extra={"job_id": job.id, "route_id": route.id, "dests": list(dests),
               "pairs": len(pairs), "total": job.total},
    )

    status = prepare_playwright_browsers()
    log.info("playwright browsers prepared", extra={"status": status})
    with sync_playwright() as p:
        browser = _launch_firefox(p, headless=headless)
        try:
            ctx = browser.new_context(viewport=VIEWPORT, locale=LOCALE)
            page = ctx.new_page()

            # Warm-up: land on the flights home, dismiss consent, and bail early
            # with a typed state if we are walled before any pair runs.
            page.goto(
                f"https://www.google.com/travel/flights?hl=en&curr={currency}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(_SETTLE_MS)
            _reject_consent(page)
            warm = classify_page(
                page.locator("body").aria_snapshot(), _page_url(page),
                match_count=0, min_price=None,
            )
            if warm in _BLOCKING_OUTCOMES:
                job.error_kind = warm.value
                log.error("blocked at warm-up", extra={"job_id": job.id, "outcome": warm.value})
                raise ScanBlocked(warm)

            n = 0
            consecutive_consent = 0  # pairs in a row stuck on an undismissable consent wall
            for dest in dests:
                if cancel_check():
                    break
                for dep, ret in pairs:
                    if cancel_check():
                        break
                    if (
                        max_scan_seconds is not None
                        and (time.monotonic() - scan_started) > max_scan_seconds
                    ):
                        job.message = "Scan deadline exceeded; stopping early"
                        log.warning(
                            "scan deadline exceeded",
                            extra={"job_id": job.id, "done": job.done, "total": job.total},
                        )
                        return
                    n += 1
                    job.done = n - 1
                    job.message = f"{route.origin} → {dest} · {dep.isoformat()} → {ret.isoformat()}"
                    # The captured per-leg airline filter (\x32\x02<IATA>) in BASE_TFS
                    # is rejected by Google as of 2026-06, so we never build a
                    # filtered URL: nonstop routes use the fresh current-schema blob
                    # (+ stops=nonstop), all-airline routes search UNFILTERED and are
                    # restricted by route.airline_name in parse_snapshot().
                    if route.nonstop:
                        url = build_nonstop_url(dep, ret, currency=currency)
                    else:
                        url = build_url(dep, ret, dest=dest, airline=None, currency=currency)

                    started = time.monotonic()
                    try:
                        outcome, parsed, snap, match_count, attempt = _scan_one_pair(
                            page, url, route.airline_name, config
                        )
                    except Exception as exc:
                        # Per-pair navigation/parse failure: record ONE typed error
                        # row and continue — a single bad page must never abort the
                        # whole scan (the parse used to run outside this try).
                        elapsed_ms = int((time.monotonic() - started) * 1000)
                        is_timeout = "timeout" in type(exc).__name__.lower() or "Timeout" in str(exc)
                        if is_timeout:
                            kind = ScanOutcome.TIMEOUT
                        elif isinstance(exc, (ValueError, AttributeError, IndexError, TypeError, re.error)):
                            # The parser raised (e.g. aria-format drift) rather than the
                            # navigation — distinguish it so a parse fault is diagnosable.
                            kind = ScanOutcome.PARSE_ERROR
                        else:
                            kind = ScanOutcome.ERROR
                        log.warning(
                            "pair failed",
                            extra={"job_id": job.id, "route_id": route.id, "dest": dest,
                                   "dep": dep.isoformat(), "ret": ret.isoformat(), "url": url,
                                   "error_type": type(exc).__name__, "outcome": kind.value,
                                   "elapsed_ms": elapsed_ms},
                            exc_info=True,
                        )
                        consecutive_consent = 0  # an error pair breaks a consent streak
                        row = _error_row(route, dest, dep, ret, str(exc), kind.value)
                        job.results.append(row)
                        job.done = n
                        if on_result:
                            on_result(row)
                        continue

                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    log.info(
                        "scan pair",
                        extra={"job_id": job.id, "route_id": route.id, "origin": route.origin,
                               "dest": dest, "dep": dep.isoformat(), "ret": ret.isoformat(),
                               "url": url, "attempt": attempt, "snapshot_len": len(snap),
                               "match_count": match_count, "min_price": parsed.get("min_price"),
                               "outcome": outcome.value, "elapsed_ms": elapsed_ms},
                    )

                    if outcome in _BLOCKING_OUTCOMES:
                        # Record the walled pair, then abort the scan (session burned).
                        row = _error_row(
                            route, dest, dep, ret,
                            f"{outcome.value} wall detected", outcome.value,
                        )
                        job.results.append(row)
                        job.done = n
                        if on_result:
                            on_result(row)
                        job.error_kind = outcome.value
                        raise ScanBlocked(outcome)

                    # A consent wall that survives its one-shot dismissal on this many
                    # pairs in a row means the session is stuck — abort like the other
                    # walls instead of burning the remaining budget re-hitting it (AC5).
                    if outcome == ScanOutcome.WALL_CONSENT:
                        consecutive_consent += 1
                        if consecutive_consent >= _MAX_CONSECUTIVE_CONSENT:
                            row = _error_row(
                                route, dest, dep, ret,
                                f"consent wall persisted across {consecutive_consent} consecutive pairs",
                                outcome.value,
                            )
                            job.results.append(row)
                            job.done = n
                            if on_result:
                                on_result(row)
                            job.error_kind = outcome.value
                            log.error(
                                "consent wall persisted; aborting scan",
                                extra={"job_id": job.id, "consecutive_consent": consecutive_consent},
                            )
                            raise ScanBlocked(outcome)
                    else:
                        consecutive_consent = 0

                    row = _success_row(parsed, route, dest, dep, ret, url, outcome.value)
                    job.results.append(row)
                    job.done = n
                    if on_result:
                        on_result(row)
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001 - teardown best-effort; never mask the real error
                log.debug("browser close failed during teardown", exc_info=True)
            # Emit the run-level summary on EVERY exit path (normal, deadline-return,
            # ScanBlocked abort, or re-raised exception) — an operator needs the
            # priced/unpriced tally most when a scan ended abnormally (AC3).
            priced = sum(1 for r in job.results if r.get("min_price") is not None)
            log.info(
                "scan end",
                extra={"job_id": job.id, "route_id": route.id, "done": job.done,
                       "total": job.total, "priced": priced,
                       "unpriced": len(job.results) - priced},
            )
