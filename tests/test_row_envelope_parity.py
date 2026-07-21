"""Lock on the scan-row envelope engine.run_scan builds (via _success_row/_error_row).

The first 8 envelope keys (dest, origin, dep, ret, dep_wd, ret_wd, trip_days, url)
and their ORDER are the legacy contract validated against the pre-refactor
run_*_scan functions at commit 1848d25: content-equal for all routes; key order
matches legacy egyptair/ams exactly.

The resilience refactor extracted row-building into pure helpers
(engine._success_row / engine._error_row) and appends ONE new typed field,
`outcome`, so a min_price=None row is no longer indistinguishable between empty,
drift, partial, and blocked. This test calls those helpers directly (no AST
extraction) and pins both the preserved legacy envelope and the new field.

Run standalone (no pytest needed):  .venv/bin/python tests/test_row_envelope_parity.py
Or via pytest:                      pytest tests/test_row_envelope_parity.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(ROOT))

from app.engine import _error_row, _success_row  # noqa: E402
from app.route_defs import BUILTIN_ROUTES  # noqa: E402

GOLDEN_PARSES = json.loads((TESTS / "fixtures" / "golden_parses.json").read_text())

DEP, RET = dt.date(2026, 8, 7), dt.date(2026, 8, 10)
URL = "https://example.invalid/search?tfs=TEST"

# Legacy envelope (order is the contract); `outcome` is the new typed tail field.
ENVELOPE = {
    "dest": None,  # per-case below
    "origin": "DUB",
    "dep": "2026-08-07",
    "ret": "2026-08-10",
    "dep_wd": "Fri",
    "ret_wd": "Mon",
    "trip_days": 3,
    "url": URL,
    "outcome": "priced",
}
BASE_ERROR_ROW = {
    "dep": "2026-08-07",
    "ret": "2026-08-10",
    "min_price": None,
    "error": "Timeout 45000ms exceeded.",
    "outcome": "timeout",
}


def _success(route_id: str, dest: str) -> dict:
    parsed = dict(GOLDEN_PARSES["routes"][route_id]["parsed"])  # fresh copy per call
    return _success_row(parsed, BUILTIN_ROUTES[route_id], dest, DEP, RET, URL, "priced")


def test_single_destination_envelope() -> None:
    for route_id, dest in (("egyptair", "CAI"), ("ams", "AMS")):
        success = _success(route_id, dest)
        parse = GOLDEN_PARSES["routes"][route_id]["parsed"]
        expected = {**parse, **ENVELOPE, "dest": dest}
        assert success == expected, f"{route_id} success row drifted"
        assert list(success) == list(parse) + list(ENVELOPE), f"{route_id} key order drifted"
        # Single-destination routes do NOT tag error rows with dest (legacy quirk).
        error = _error_row(BUILTIN_ROUTES[route_id], dest, DEP, RET, BASE_ERROR_ROW["error"], "timeout")
        assert error == BASE_ERROR_ROW, f"{route_id} error row drifted"
        assert list(error) == list(BASE_ERROR_ROW)


def test_multi_destination_envelope() -> None:
    success = _success("turkey", "IST")
    parse = GOLDEN_PARSES["routes"]["turkey"]["parsed"]
    assert success == {**parse, **ENVELOPE, "dest": "IST"}
    # Multi-destination routes DO tag error rows with dest, dest-first (legacy order).
    error = _error_row(BUILTIN_ROUTES["turkey"], "IST", DEP, RET, BASE_ERROR_ROW["error"], "timeout")
    assert error == {"dest": "IST", **BASE_ERROR_ROW}
    assert list(error) == ["dest"] + list(BASE_ERROR_ROW)


def test_outcome_field_is_appended_last() -> None:
    """The new typed field must be the final key, after the legacy envelope."""
    success = _success("ams", "AMS")
    assert list(success)[-1] == "outcome"
    assert success["outcome"] == "priced"


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
