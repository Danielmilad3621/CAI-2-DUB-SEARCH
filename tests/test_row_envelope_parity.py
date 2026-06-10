"""Permanent lock on the scan-row envelope engine.run_scan builds.

The expected dicts below were validated against the legacy run_*_scan
functions (commit 1848d25) by tests/closeout_envelope_diff.py: content-equal
for all routes; key order matches legacy egyptair/ams exactly, while legacy
turkey serialized `origin` last (the legacy functions disagreed with each
other, so a single envelope cannot match both orders — content equality is
the contract).

The test extracts the row-building fragments from the CURRENT app/engine.py
source via AST and executes them, so any drift in run_scan's envelope fails
here without needing a browser.

Run standalone (no pytest needed):  .venv/bin/python tests/test_row_envelope_parity.py
Or via pytest:                      pytest tests/test_row_envelope_parity.py
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TESTS))

from closeout_envelope_diff import _error_and_update_segments, _fn, _run  # noqa: E402

from app.route_defs import BUILTIN_ROUTES  # noqa: E402

GOLDEN_PARSES = json.loads((TESTS / "fixtures" / "golden_parses.json").read_text())

DEP, RET = dt.date(2026, 8, 7), dt.date(2026, 8, 10)
URL = "https://example.invalid/search?tfs=TEST"
EXC = RuntimeError("Timeout 45000ms exceeded.")

ENVELOPE = {
    "dest": None,  # per-case below
    "origin": "DUB",
    "dep": "2026-08-07",
    "ret": "2026-08-10",
    "dep_wd": "Fri",
    "ret_wd": "Mon",
    "trip_days": 3,
    "url": URL,
}
BASE_ERROR_ROW = {
    "dep": "2026-08-07",
    "ret": "2026-08-10",
    "min_price": None,
    "error": str(EXC),
}


def _engine_rows(route_id: str, dest: str) -> tuple[dict, dict]:
    src = (ROOT / "app" / "engine.py").read_text()
    update_src, error_stmts = _error_and_update_segments(src, _fn(ast.parse(src), "run_scan"))
    env = {
        "dep": DEP, "ret": RET, "dest": dest, "url": URL, "exc": EXC,
        "route": BUILTIN_ROUTES[route_id], "str": str, "len": len,
        "data": GOLDEN_PARSES["routes"][route_id]["parsed"],
    }
    return _run(update_src, error_stmts, env)


def test_single_destination_envelope() -> None:
    for route_id, dest in (("egyptair", "CAI"), ("ams", "AMS")):
        success, error = _engine_rows(route_id, dest)
        parse = GOLDEN_PARSES["routes"][route_id]["parsed"]
        expected = {**parse, **ENVELOPE, "dest": dest}
        assert success == expected, f"{route_id} success row drifted"
        assert list(success) == list(parse) + list(ENVELOPE), f"{route_id} key order drifted"
        # Single-destination routes do NOT tag error rows with dest (legacy quirk).
        assert error == BASE_ERROR_ROW, f"{route_id} error row drifted"
        assert list(error) == list(BASE_ERROR_ROW)


def test_multi_destination_envelope() -> None:
    success, error = _engine_rows("turkey", "IST")
    parse = GOLDEN_PARSES["routes"]["turkey"]["parsed"]
    assert success == {**parse, **ENVELOPE, "dest": "IST"}
    # Multi-destination routes DO tag error rows with dest, dest-first (legacy order).
    assert error == {"dest": "IST", **BASE_ERROR_ROW}
    assert list(error) == ["dest"] + list(BASE_ERROR_ROW)


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
