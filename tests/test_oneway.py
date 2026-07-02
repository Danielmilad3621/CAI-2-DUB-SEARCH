"""One-way Cairo->Dublin support: URL builder + one-way result parser.

The one-way blob was captured live (all airlines, no stops filter). One-way is
encoded by the trailing trip-type field \\x98\\x01\\x02; only the ISO departure
date is byte-swapped. parse_oneway reads the 'From N euros. <stops> flight with
<airline>.' labels (no 'round trip total') and can filter to nonstop EgyptAir.

Run standalone:  .venv/bin/python tests/test_oneway.py
Or via pytest:   pytest tests/test_oneway.py
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


class _FakePage:
    def __init__(self, text: str) -> None:
        self._t = text

    def locator(self, _sel: str) -> _FakePage:
        return self

    def aria_snapshot(self) -> str:
        return self._t


_SNAP = (
    '- tab "Cheapest from 218 euros Learn more about ranking":\n'
    '- link "From 307 euros. 1 stop flight with Etihad. Leaves Cairo International Airport '
    'at 8:15 AM on Thursday, August 6 and arrives at Dublin Airport at 1:00 PM. Select flight"\n'
    '- link "From 442 euros. Nonstop flight with EgyptAir. Leaves Cairo International Airport '
    'at 9:30 AM on Thursday, August 6 and arrives at Dublin Airport at 1:20 PM. Select flight"\n'
    '- link "From 218 euros. 1 stop flight with Aegean. Leaves Cairo International Airport '
    'at 8:00 PM on Thursday, August 6 and arrives at Dublin Airport at 11:00 AM. Select flight"\n'
)


def _dates(url: str) -> list[bytes]:
    tfs = parse_qs(urlparse(url).query)["tfs"][0]
    return re.findall(rb"\d{4}-\d{2}-\d{2}", engine._b64u_decode(tfs))


def test_oneway_url_is_cai_dub_oneway_with_swapped_date() -> None:
    url = engine.build_oneway_url(dt.date(2026, 8, 9))
    tfs = parse_qs(urlparse(url).query)["tfs"][0]
    raw = engine._b64u_decode(tfs)
    assert _dates(url) == [b"2026-08-09"]               # single, correct date
    assert raw.endswith(b"\x98\x01\x02")                # trip type = one-way
    assert b"/m/01w2v" in raw and b"/m/02cft" in raw    # Cairo origin, Dublin dest
    assert "curr=EUR" in url


def test_parse_oneway_nonstop_egyptair_only() -> None:
    out = engine.parse_oneway(_FakePage(_SNAP), airline_name="EgyptAir", nonstop_only=True)
    assert out["min_price"] == 442
    assert len(out["entries"]) == 1
    e = out["entries"][0]
    assert e["airline"] == "EgyptAir" and e["stops"] == "Nonstop"


def test_parse_oneway_unfiltered_takes_global_min() -> None:
    out = engine.parse_oneway(_FakePage(_SNAP))
    assert out["min_price"] == 218                      # Aegean 1-stop is cheapest overall
    airlines = {e["airline"] for e in out["entries"]}
    assert {"Etihad", "EgyptAir", "Aegean"} <= airlines


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
