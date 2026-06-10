"""API surface tests for the Phase-2 route registry endpoints.

Covers: GET /api/routes metadata, the byte-stable legacy GET /api/scanners,
POST/DELETE /api/routes validation + protection, and POST /api/scans
accepting both route_id and the deprecated scanner alias (worker function
monkeypatched — no browser is launched).

Run standalone (no pytest needed):  .venv/bin/python tests/test_api_routes.py
Or via pytest:                      pytest tests/test_api_routes.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402
from app.jobs import store  # noqa: E402
from app.registry import registry  # noqa: E402

client = TestClient(main.app)

# GET /api/scanners must stay byte-stable until Phase 3 removes it.
LEGACY_SCANNERS = [
    {"id": "turkey", "name": "Dublin → Turkey", "subtitle": "Aug 2026 weekends · IST, SAW, AYT",
     "origin": "DUB", "default_dest": "IST", "eta_minutes": 5, "combinations": 36},
    {"id": "egyptair", "name": "Dublin → Cairo", "subtitle": "EgyptAir only · Sat/Sun/Tue/Thu",
     "origin": "DUB", "default_dest": "CAI", "eta_minutes": 30, "combinations": 245},
    {"id": "ams", "name": "Dublin → Amsterdam", "subtitle": "All airlines · daily",
     "origin": "DUB", "default_dest": "AMS", "eta_minutes": 90, "combinations": 720},
]


class _isolated_user_routes:
    """Point the registry singleton at a temp user file for the duration."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_path = registry._user_path
        self._old_user = dict(registry._user)
        registry._user_path = Path(self._tmp.name) / "routes.user.json"
        registry._user = {}
        return self

    def __exit__(self, *exc):
        registry._user_path = self._old_path
        registry._user = self._old_user
        self._tmp.cleanup()


def _fake_run_job(job, cancel_check):
    job.done = job.total
    job.results.append({"dep": "2026-08-07", "ret": "2026-08-10", "min_price": 123,
                        "entries": [], "dest": "TST", "origin": "DUB", "url": "u"})


def _wait_done(job_id: str, timeout: float = 5.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = store.get(job_id).status.value
        if status in ("completed", "failed", "cancelled"):
            return status
        time.sleep(0.02)
    raise AssertionError("scan did not finish in time")


def test_legacy_scanners_endpoint_unchanged() -> None:
    assert client.get("/api/scanners").json() == LEGACY_SCANNERS


def test_list_routes_metadata() -> None:
    routes = {r["id"]: r for r in client.get("/api/routes").json()}
    assert set(routes) >= {"turkey", "egyptair", "ams"}
    turkey = routes["turkey"]
    assert turkey["builtin"] and turkey["persistent"] and turkey["origin_locked"]
    assert turkey["combinations"] == 36
    assert turkey["destinations"] == ["IST", "SAW", "AYT"]
    assert turkey["default_dest"] == "IST"
    assert routes["egyptair"]["airline"] == "MS"
    assert routes["ams"]["date_strategy"] == "window"


def test_route_crud_via_api() -> None:
    with _isolated_user_routes():
        created = client.post("/api/routes", json={"destinations": "CDG,ORY", "airline": "AF",
                                                   "airline_name": "Air France"})
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["id"] == "cdg-ory"
        assert body["builtin"] is False and body["persistent"] is False
        assert body["origin_locked"] is True
        assert body["combinations"] > 0  # window estimate from today

        ids = [r["id"] for r in client.get("/api/routes").json()]
        assert "cdg-ory" in ids

        assert client.post("/api/routes", json={"destinations": "CDG,ORY"}).status_code == 409
        assert client.delete("/api/routes/cdg-ory").status_code == 200
        assert "cdg-ory" not in [r["id"] for r in client.get("/api/routes").json()]
        assert client.delete("/api/routes/cdg-ory").status_code == 404


def test_route_create_rejections() -> None:
    with _isolated_user_routes():
        # non-DUB origin → 400 with the capability message
        resp = client.post("/api/routes", json={"destinations": "CDG", "origin": "CAI"})
        assert resp.status_code == 400 and "locked to DUB" in resp.json()["detail"]
        # pydantic-level: bad airline pattern, bad id slug
        assert client.post("/api/routes", json={"destinations": "CDG", "airline": "AIR"}).status_code == 422
        assert client.post("/api/routes", json={"destinations": "CDG", "id": "Bad Id"}).status_code == 422
        # registry-level: bad IATA, bad weekdays
        assert client.post("/api/routes", json={"destinations": "CDGX"}).status_code == 400
        assert client.post("/api/routes", json={"destinations": "CDG", "weekdays": "Funday"}).status_code == 400
        # fixed_pairs not creatable via API (field rejected by pydantic? no — ignored).
        resp = client.post("/api/routes", json={"destinations": "NCE", "date_strategy": "fixed_pairs",
                                                "fixed_pairs": [["2026-08-01", "2026-08-03"]]})
        assert resp.status_code == 201
        assert resp.json()["date_strategy"] == "window"  # unknown fields ignored, stays window


def test_delete_builtin_forbidden() -> None:
    assert client.delete("/api/routes/egyptair").status_code == 403


def test_scan_accepts_route_id_and_scanner_alias() -> None:
    with _isolated_user_routes():
        original = main.run_job
        main.run_job = _fake_run_job
        try:
            # deprecated alias
            resp = client.post("/api/scans", json={"scanner": "turkey"})
            assert resp.status_code == 200, resp.text
            job = resp.json()
            assert job["scanner"] == "turkey" and job["route_id"] == "turkey"
            assert job["total"] == 36
            assert _wait_done(job["id"]) == "completed"

            # new field, user-created route
            client.post("/api/routes", json={"destinations": "CDG"})
            resp = client.post("/api/scans", json={"route_id": "cdg", "window_days": 14})
            assert resp.status_code == 200, resp.text
            job = resp.json()
            assert job["route_id"] == "cdg"
            assert _wait_done(job["id"]) == "completed"
            assert store.get(job["id"]).config["scanner"] == "cdg"  # alias mirrored in config

            # neither field → 422; unknown → 404
            assert client.post("/api/scans", json={}).status_code == 422
            assert client.post("/api/scans", json={"route_id": "nope"}).status_code == 404
        finally:
            main.run_job = original


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
