"""System-boundary input validation: weekdays, configurable destinations, budget.

Locks the fixes for the SystemExit->HTTP 500 weekday bug, the unvalidated
configurable-destinations path into the protobuf byte-swap, and the missing
per-scan combination budget. The API cases all error BEFORE a job is created, so
no browser is launched.

Run standalone:  .venv/bin/python tests/test_boundary_validation.py
Or via pytest:   pytest tests/test_boundary_validation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402
from app import engine  # noqa: E402
from app.engine import Route, _parse_weekdays, destinations_for  # noqa: E402

client = TestClient(main.app)


def test_parse_weekdays_raises_valueerror_not_systemexit() -> None:
    for bad in ("Funday", "", "   ", ",,"):
        try:
            _parse_weekdays(bad)
            raise AssertionError(f"{bad!r} should have raised")
        except ValueError as exc:
            # Meaningful assertion: the message names the offending input.
            assert "weekday" in str(exc).lower(), str(exc)
        except SystemExit as exc:  # pragma: no cover - the bug we are guarding against
            raise AssertionError(f"{bad!r} raised SystemExit (regressed)") from exc


def test_parse_weekdays_skips_blank_tokens() -> None:
    # Trailing/empty tokens are skipped to match registry._validate_weekdays.
    assert _parse_weekdays("Sat,") == {5}
    assert _parse_weekdays("Sat,,Sun") == {5, 6}
    assert _parse_weekdays(" fri , SAT ,sun") == {4, 5, 6}


def test_bad_weekday_is_4xx_not_500() -> None:
    # The SystemExit bug surfaced as an opaque HTTP 500; now a clean 4xx.
    for body in (
        {"destinations": "CDG", "weekdays": "Funday"},
        {"destinations": "CDG", "weekdays": "Sat,Xyz,Sun"},
    ):
        resp = client.post("/api/search", json=body)
        assert resp.status_code in (400, 422), f"{body} -> {resp.status_code}: {resp.text}"
    # Same defense on the saved-route endpoint (egyptair honors config weekdays).
    resp = client.post("/api/scans", json={"route_id": "egyptair", "weekdays": "Funday"})
    assert resp.status_code in (400, 422), resp.text


def test_destinations_for_validates_iata() -> None:
    route = Route(
        id="cfg", name="cfg", subtitle="t", origin="DUB",
        destinations=("CAI",), configurable_destinations=True,
    )
    # Valid override passes through.
    assert destinations_for(route, {"destinations": "cai, ist"}) == ("CAI", "IST")
    # A non-3-alpha token must be rejected before it reaches build_url's byte-swap.
    for bad in ("CAI,@@@", "CAI,ZZ", "CAI,Z9X", "CA;"):
        try:
            destinations_for(route, {"destinations": bad})
            raise AssertionError(f"{bad!r} should have raised")
        except ValueError:
            pass


def test_search_combination_budget_enforced() -> None:
    # A sweep far above MAX_PAIRS_PER_SCAN is rejected with 422 before any job.
    resp = client.post("/api/search", json={
        "destinations": ["AAA", "BBB", "CCC"],
        "window_days": 120,
        "weekdays": "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
        "min_trip_days": 1,
        "max_trip_days": 21,
    })
    assert resp.status_code == 422, resp.text
    assert "max" in resp.json()["detail"].lower()
    assert str(engine.MAX_PAIRS_PER_SCAN) in resp.json()["detail"]


def test_scans_endpoint_combination_budget_enforced() -> None:
    # The identical guard on the saved-route endpoint (egyptair is a window route).
    resp = client.post("/api/scans", json={
        "route_id": "egyptair",
        "window_days": 366,
        "weekdays": "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
        "min_trip_days": 1,
        "max_trip_days": 60,
    })
    assert resp.status_code == 422, resp.text
    assert "max" in resp.json()["detail"].lower()


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
