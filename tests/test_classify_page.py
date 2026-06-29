"""Typed page-outcome classification + parser-anchoring regression gate (offline).

Pins the resilience backbone: classify_page maps every failure-mode fixture to a
distinct ScanOutcome (so an empty page is never conflated with a wall, a partial
load, or a drifted format), and the parser anchors the airline filter to the
carrier clause. No browser, no network — pure replay of tests/fixtures/.

Run standalone:  .venv/bin/python tests/test_classify_page.py
Or via pytest:   pytest tests/test_classify_page.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine import (  # noqa: E402
    ScanOutcome,
    classify_page,
    count_price_rows,
    parse_snapshot,
)

FIX = ROOT / "tests" / "fixtures"
FLIGHTS_URL = "https://www.google.com/travel/flights/search?tfs=CBwQ&hl=en&curr=EUR"

# (fixture stem, page url, expected outcome). URL-keyed walls (consent/signin/
# captcha) pass the redirect URL; content-keyed states pass a normal flights URL.
CASES = [
    ("loading_partial", FLIGHTS_URL, ScanOutcome.LOADING),
    ("empty_no_flights", FLIGHTS_URL, ScanOutcome.EMPTY),
    ("oops_error", FLIGHTS_URL, ScanOutcome.PROVIDER_ERROR),
    ("drift_round_trip", FLIGHTS_URL, ScanOutcome.DRIFT_SUSPECTED),
    ("consent_wall", "https://consent.google.com/m?continue=x", ScanOutcome.WALL_CONSENT),
    ("signin_redirect", "https://accounts.google.com/ServiceLogin?x=1", ScanOutcome.WALL_SIGNIN),
    ("unusual_traffic", "https://www.google.com/sorry/index?q=x", ScanOutcome.WALL_CAPTCHA),
]


def _classify(stem: str, url: str, *, airline_name: str | None = None) -> ScanOutcome:
    snap = (FIX / f"{stem}_snapshot.txt").read_text()
    parsed = parse_snapshot(snap, airline_name=airline_name)
    return classify_page(
        snap, url, match_count=count_price_rows(snap), min_price=parsed["min_price"]
    )


def test_failure_mode_fixtures_map_to_distinct_outcomes() -> None:
    for stem, url, expected in CASES:
        got = _classify(stem, url)
        assert got == expected, f"{stem}: classified {got!r}, expected {expected!r}"
    # All seven outcomes are distinct — the whole point of the enum.
    assert len({e for _, _, e in CASES}) == len(CASES)


def test_consent_and_captcha_detected_from_content_too() -> None:
    # Even without the URL signal (e.g. consent served on the flights host),
    # content markers classify the wall rather than letting it read as empty.
    assert _classify("consent_wall", FLIGHTS_URL) == ScanOutcome.WALL_CONSENT
    assert _classify("unusual_traffic", FLIGHTS_URL) == ScanOutcome.WALL_CAPTCHA


def test_happy_fixtures_classify_priced_never_signin() -> None:
    # The critical false-positive guard: every happy fixture carries a header
    # "Sign in" link to accounts.google.com/ServiceLogin in its SNAPSHOT text.
    # classify_page must key sign-in on the page URL only, so these stay PRICED.
    for stem in ("egyptair", "turkey", "ams"):
        airline = "EgyptAir" if stem == "egyptair" else None
        got = _classify(stem, FLIGHTS_URL, airline_name=airline)
        assert got == ScanOutcome.PRICED, f"{stem}: {got!r}"
        assert got != ScanOutcome.WALL_SIGNIN


def test_signin_body_without_redirect_url_is_not_signin() -> None:
    # The sign-in BODY on a normal flights URL must not be misread as a wall
    # (proves detection is redirect-URL-driven, not snapshot-text-driven).
    got = _classify("signin_redirect", FLIGHTS_URL)
    assert got != ScanOutcome.WALL_SIGNIN


def test_captcha_detected_by_not_a_robot_without_unusual_traffic() -> None:
    # Guards the broadened CAPTCHA marker: a challenge page whose ONLY bot signal
    # is "not a robot" (no "unusual traffic") must still be WALL_CAPTCHA. The older
    # marker "are not a robot" did not match real copy ("...and not a robot.").
    snap = (FIX / "captcha_robot_snapshot.txt").read_text()
    assert "unusual traffic" not in snap.lower()  # the ONLY signal is "not a robot"
    assert "are not a robot" not in snap.lower()
    got = classify_page(snap, FLIGHTS_URL, match_count=0, min_price=None)
    assert got == ScanOutcome.WALL_CAPTCHA, got


def test_loading_fixture_has_no_priced_rows() -> None:
    # Guards the partial-page premise: the loading fixture must have zero priced
    # rows, so it can only be LOADING (not accidentally PRICED).
    snap = (FIX / "loading_partial_snapshot.txt").read_text()
    assert count_price_rows(snap) == 0
    assert parse_snapshot(snap, airline_name="EgyptAir")["min_price"] is None


# --- Parser anchoring (cluster 2) --------------------------------------------

_CROSS_MENTION = (
    '- list:\n'
    '  - listitem:\n'
    '    - link "From 450 euros round trip total. Nonstop flight with Lufthansa. '
    'Cheaper than EgyptAir on this route. Select flight"\n'
    '  - listitem:\n'
    '    - link "From 893 euros round trip total. Nonstop flight with EgyptAir. '
    'Leaves Dublin Airport at 2:20 PM. Select flight"\n'
)
_LOWERCASE_CARRIER = (
    '- link "From 250 euros round trip total. Nonstop flight with easyJet. '
    'Leaves Dublin Airport at 6:00 AM. Select flight"\n'
)


def test_airline_filter_anchored_to_carrier_clause() -> None:
    # A cheaper competitor row that merely MENTIONS EgyptAir must not be
    # attributed to it (was: unanchored [^"]* bound the 450 fare to EgyptAir).
    out = parse_snapshot(_CROSS_MENTION, airline_name="EgyptAir")
    assert out["min_price"] == 893, out
    assert len(out["entries"]) == 1
    assert "EgyptAir" in out["entries"][0]["snippet"]
    assert "Lufthansa" not in out["entries"][0]["snippet"]


def test_lowercase_initial_carrier_kept_in_parse_results() -> None:
    out = parse_snapshot(_LOWERCASE_CARRIER, airline_name=None)
    assert out["min_price"] == 250
    assert out["entries"][0]["airline"] == "easyJet"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
