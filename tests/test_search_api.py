"""API surface tests for the ad-hoc search endpoint (POST /api/search).

The Playwright worker is monkeypatched so no browser launches: search() spawns
its scan via a closure over engine.run_scan looked up on app.main, so replacing
main.run_scan swaps in a fake that records a priced row synchronously.

Run standalone (no pytest needed):  .venv/bin/python tests/test_search_api.py
Or via pytest:                      pytest tests/test_search_api.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402
from app.jobs import store  # noqa: E402

client = TestClient(main.app)


def _fake_run_scan(route, job, cancel_check, config, **_kw):
    job.total = job.total or 1
    job.results.append(
        {
            "dest": route.destinations[0],
            "origin": route.origin,
            "dep": "2026-07-04",
            "ret": "2026-07-07",
            "min_price": 321,
            "entries": [],
            "url": "https://example.test/u",
        }
    )
    job.done = job.total


def _wait_done(job_id: str, timeout: float = 5.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = store.get(job_id).status.value
        if status in ("completed", "failed", "cancelled"):
            return status
        time.sleep(0.02)
    raise AssertionError("scan did not finish in time")


class _patched_worker:
    """Swap engine.run_scan (as seen by app.main) for the browser-free fake."""

    def __enter__(self):
        self._orig = main.run_scan
        main.run_scan = _fake_run_scan
        return self

    def __exit__(self, *exc):
        main.run_scan = self._orig


def test_search_happy_path() -> None:
    with _patched_worker():
        resp = client.post(
            "/api/search",
            json={"origin": "DUB", "destinations": ["IST", "SAW"], "window_days": 14},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] in ("queued", "running", "completed")
        assert body["total"] > 0
        assert body["route_id"] == "ist-saw"
        assert body["links"]["results"] == f"/api/scans/{body['id']}/results"
        assert any("origin pinned to DUB" in n for n in body["capability_notes"])

        assert _wait_done(body["id"]) == "completed"
        results = client.get(body["links"]["results"]).json()
        assert results["winner"]["min_price"] == 321
        assert results["ranked"][0]["dest"] == "IST"


def test_search_accepts_comma_string_destinations() -> None:
    with _patched_worker():
        resp = client.post(
            "/api/search",
            json={"destinations": "ist, saw ,ayt", "window_days": 10},
        )
        assert resp.status_code == 202, resp.text
        assert resp.json()["route_id"] == "ist-saw-ayt"
        _wait_done(resp.json()["id"])


def test_search_origin_locked() -> None:
    resp = client.post("/api/search", json={"origin": "LHR", "destinations": "CDG"})
    assert resp.status_code == 400
    assert "locked to DUB" in resp.json()["detail"]


def test_search_nonstop_only_cairo() -> None:
    resp = client.post("/api/search", json={"destinations": "IST", "nonstop": True})
    assert resp.status_code == 400
    assert "Cairo" in resp.json()["detail"] or "CAI" in resp.json()["detail"]


def test_search_bad_destination_iata() -> None:
    # 4-letter code is rejected by Route validation -> 400 capability error.
    resp = client.post("/api/search", json={"destinations": "CDGX"})
    assert resp.status_code == 400


def test_search_rejects_non_alpha_destination() -> None:
    # Non-alpha 3-char codes must not slip through into the provider URL.
    for bad in ("CA;", "C I", "C1G"):
        resp = client.post("/api/search", json={"destinations": [bad]})
        assert resp.status_code == 400, f"{bad!r} -> {resp.status_code}"


def test_search_rejects_injected_currency() -> None:
    for bad in ("A&B", "EUR&hl=xx", "EU"):
        resp = client.post("/api/search", json={"destinations": "CDG", "currency": bad})
        assert resp.status_code == 422, f"{bad!r} -> {resp.status_code}"


def test_search_caps_destinations() -> None:
    resp = client.post(
        "/api/search",
        json={"destinations": ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]},
    )
    assert resp.status_code == 422
    assert "at most" in resp.json()["detail"]


def test_search_trip_day_bounds_are_422() -> None:
    resp = client.post(
        "/api/search",
        json={"destinations": "CDG", "min_trip_days": 10, "max_trip_days": 5},
    )
    assert resp.status_code == 422


def test_search_bad_start_date_is_422() -> None:
    resp = client.post("/api/search", json={"destinations": "CDG", "start": "not-a-date"})
    assert resp.status_code == 422


def test_search_adults_note_surfaced() -> None:
    with _patched_worker():
        resp = client.post("/api/search", json={"destinations": "CDG", "adults": 3})
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["config"]["adults"] == 3
        assert any("adults>1" in n for n in body["capability_notes"])
        _wait_done(body["id"])


def test_health_reports_version_and_provider() -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["version"] == main.API_VERSION
    assert "google-flights" in body["provider"]


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
