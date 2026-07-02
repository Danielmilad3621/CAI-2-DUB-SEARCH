"""Regression test for the date-swap collision bug.

build_url / build_nonstop_url byte-swap two seed dates into a captured tfs blob.
When the requested departure string equals the builder's seed RETURN date, the
old sequential `.replace(..., 1)` rewrote the wrong leg and the outbound/return
dates came out swapped — which produced a bogus "cheapest" fare in a live scan
(dep 2026-07-09 == NONSTOP seed return 2026-07-09).

Run standalone:  .venv/bin/python tests/test_date_swap.py
Or via pytest:   pytest tests/test_date_swap.py
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import engine  # noqa: E402


def _dates_in_url(url: str) -> list[bytes]:
    """Decoded tfs dates in blob order: [outbound, return]."""
    tfs = parse_qs(urlparse(url).query)["tfs"][0]
    return re.findall(rb"\d{4}-\d{2}-\d{2}", engine._b64u_decode(tfs))


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def test_nonstop_url_not_swapped_on_seed_collision() -> None:
    # dep == NS_SEED_RET (2026-07-09): the exact regression trigger.
    url = engine.build_nonstop_url(_d("2026-07-09"), _d("2026-07-26"))
    assert _dates_in_url(url) == [b"2026-07-09", b"2026-07-26"]


def test_build_url_not_swapped_on_seed_collision() -> None:
    # dep == SEED_RET (2026-06-18).
    url = engine.build_url(_d("2026-06-18"), _d("2026-06-25"), dest="CAI", airline=None)
    assert _dates_in_url(url) == [b"2026-06-18", b"2026-06-25"]


def test_dates_correct_for_non_colliding_pairs() -> None:
    assert _dates_in_url(engine.build_nonstop_url(_d("2026-08-23"), _d("2026-09-13"))) == [
        b"2026-08-23",
        b"2026-09-13",
    ]
    assert _dates_in_url(
        engine.build_url(_d("2026-08-01"), _d("2026-08-10"), dest="CAI", airline=None)
    ) == [b"2026-08-01", b"2026-08-10"]


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
