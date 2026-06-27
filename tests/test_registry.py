"""Route registry: JSON-loaded built-ins must equal the Phase-1 hardcoded
routes, and user-route CRUD must validate, persist and protect built-ins.

Run standalone (no pytest needed):  .venv/bin/python tests/test_registry.py
Or via pytest:                      pytest tests/test_registry.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.engine import Route  # noqa: E402
from app.registry import (  # noqa: E402
    BUILTIN_PATH,
    BuiltinProtected,
    RegistryError,
    RouteConflict,
    RouteRegistry,
)
from app.route_defs import BUILTIN_ROUTES, TURKEY_AUGUST_2026_PAIRS  # noqa: E402

# The three routes exactly as Phase 1 hardcoded them in app/route_defs.py
# (commit 1590c42). routes.json must reproduce these, or the "scan identically
# when driven from routes.json" guarantee is broken.
PHASE1_PAIRS = (
    ("2026-07-31", "2026-08-03"),
    ("2026-07-31", "2026-08-04"),
    ("2026-08-01", "2026-08-03"),
    ("2026-08-01", "2026-08-04"),
    ("2026-08-07", "2026-08-09"),
    ("2026-08-07", "2026-08-10"),
    ("2026-08-14", "2026-08-16"),
    ("2026-08-14", "2026-08-17"),
    ("2026-08-21", "2026-08-23"),
    ("2026-08-21", "2026-08-24"),
    ("2026-08-28", "2026-08-30"),
    ("2026-08-28", "2026-08-31"),
)
PHASE1_ROUTES = {
    "turkey": Route(
        id="turkey",
        name="Dublin → Turkey",
        subtitle="Aug 2026 weekends · IST, SAW, AYT",
        origin="DUB",
        destinations=("IST", "SAW", "AYT"),
        airline=None,
        date_strategy="fixed_pairs",
        fixed_pairs=PHASE1_PAIRS,
        configurable_destinations=True,
        eta_minutes=5,
    ),
    "egyptair": Route(
        id="egyptair",
        name="Dublin → Cairo",
        subtitle="EgyptAir nonstop · Sat/Sun/Tue/Thu",
        origin="DUB",
        destinations=("CAI",),
        airline="MS",
        airline_name="EgyptAir",
        date_strategy="window",
        weekdays=None,
        eta_minutes=30,
        nonstop=True,
    ),
    "ams": Route(
        id="ams",
        name="Dublin → Amsterdam",
        subtitle="All airlines · daily",
        origin="DUB",
        destinations=("AMS",),
        airline=None,
        date_strategy="window",
        weekdays="Mon,Tue,Wed,Thu,Fri,Sat,Sun",
        eta_minutes=90,
    ),
}


def _fresh(tmp: Path) -> RouteRegistry:
    return RouteRegistry(builtin_path=BUILTIN_PATH, user_path=tmp / "routes.user.json")


def test_builtins_match_phase1_definitions() -> None:
    assert BUILTIN_ROUTES == PHASE1_ROUTES, "routes.json diverges from Phase-1 route_defs"
    assert TURKEY_AUGUST_2026_PAIRS == PHASE1_PAIRS
    with tempfile.TemporaryDirectory() as tmp:
        reg = _fresh(Path(tmp))
        entries = {e.route.id: e for e in reg.list_entries()}
        assert {rid: e.route for rid, e in entries.items()} == PHASE1_ROUTES
        assert all(e.builtin and e.persistent for e in entries.values())


def test_create_minimal_and_defaults() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reg = _fresh(Path(tmp))
        entry = reg.create(destinations="cdg")
        r = entry.route
        assert (r.id, r.origin, r.destinations) == ("cdg", "DUB", ("CDG",))
        assert r.date_strategy == "window" and r.fixed_pairs == ()
        assert r.name == "Dublin → CDG"
        assert r.subtitle == "All airlines · flexible days"
        assert not entry.builtin and not entry.persistent
        assert reg.get("cdg") == r


def test_create_validation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reg = _fresh(Path(tmp))
        for bad_dests in ("", "CDGX", "C1G", "A,B,C,D,E,F"):
            try:
                reg.create(destinations=bad_dests)
                raise AssertionError(f"destinations {bad_dests!r} should fail")
            except RegistryError:
                pass
        try:
            reg.create(destinations="CDG", weekdays="Funday")
            raise AssertionError("bad weekday should fail")
        except RegistryError:
            pass
        try:
            reg.create(destinations="CDG", airline="ABC")
            raise AssertionError("3-letter airline should fail")
        except RegistryError:
            pass
        # Normalization: list input, lowercase, dupes, whitespace.
        entry = reg.create(destinations=[" lis", "OPO", "lis"], weekdays="fri,SAT , sun")
        assert entry.route.destinations == ("LIS", "OPO")
        assert entry.route.weekdays == "Fri,Sat,Sun"
        assert entry.route.id == "lis-opo"


def test_conflicts_and_delete() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reg = _fresh(Path(tmp))
        for taken in ("ams", "egyptair", "turkey"):
            try:
                reg.create(destinations="CDG", id=taken)
                raise AssertionError("builtin id collision should fail")
            except RouteConflict:
                pass
        reg.create(destinations="CDG")
        try:
            reg.create(destinations="CDG")
            raise AssertionError("duplicate derived id should fail")
        except RouteConflict:
            pass
        try:
            reg.delete("ams")
            raise AssertionError("builtin delete should fail")
        except BuiltinProtected:
            pass
        try:
            reg.delete("nope")
            raise AssertionError("missing delete should fail")
        except KeyError:
            pass
        reg.delete("cdg")
        assert reg.get("cdg") is None


def test_user_routes_persist_across_instances() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        reg = _fresh(path)
        reg.create(destinations="CDG,ORY", airline="AF", airline_name="Air France")
        reborn = _fresh(path)
        route = reborn.get("cdg-ory")
        assert route is not None
        assert route.destinations == ("CDG", "ORY")
        assert route.airline == "AF" and route.airline_name == "Air France"
        assert not reborn.get_entry("cdg-ory").builtin
        # builtins untouched by the user file
        assert reborn.get_entry("ams").builtin


def test_corrupt_and_shadowing_user_file_ignored() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        user = path / "routes.user.json"
        user.write_text("{not json")
        reg = _fresh(path)
        assert len(reg.list_entries()) == 3  # builtins only

        user.write_text(json.dumps({
            "version": 1,
            "routes": [
                {"id": "ams", "name": "evil", "subtitle": "shadow", "origin": "DUB",
                 "destinations": ["XXX"]},
                {"id": "ok", "name": "OK", "subtitle": "fine", "origin": "DUB",
                 "destinations": ["CDG"]},
                {"id": "broken", "name": "bad", "subtitle": "bad", "origin": "DUB",
                 "destinations": ["TOOLONG"]},
            ],
        }))
        reg = _fresh(path)
        ids = {e.route.id: e for e in reg.list_entries()}
        assert ids["ams"].builtin, "builtin must not be shadowed by user file"
        assert "ok" in ids and not ids["ok"].builtin
        assert "broken" not in ids


def test_user_routes_cannot_use_fixed_pairs() -> None:
    import inspect

    sig = inspect.signature(RouteRegistry.create)
    assert "fixed_pairs" not in sig.parameters
    assert "date_strategy" not in sig.parameters
    with tempfile.TemporaryDirectory() as tmp:
        reg = _fresh(Path(tmp))
        assert reg.create(destinations="CDG").route.date_strategy == "window"


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
