from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.browser_env import prepare_playwright_browsers
from app.jobs import JobStatus, store
from app.registry import (
    LOCKED_ORIGIN,
    BuiltinProtected,
    RegistryError,
    RouteConflict,
    RouteEntry,
    registry,
)
from app.scanner import estimate_total, run_job

prepare_playwright_browsers()

ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(title="DUB Flight Finder", version="0.1.0")

_origins_raw = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)
_origins = [o.strip() for o in _origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScanCreate(BaseModel):
    route_id: str | None = None
    scanner: str | None = None  # deprecated alias of route_id (removed in Phase 3)
    destinations: str = "IST,SAW,AYT"
    currency: str = "EUR"
    start: str | None = None
    window_days: int = Field(default=60, ge=7, le=366)
    min_trip_days: int = Field(default=3, ge=1, le=30)
    max_trip_days: int = Field(default=14, ge=2, le=60)
    weekdays: str = "Sat,Sun,Tue,Thu"


class RouteCreate(BaseModel):
    """User-created routes are window-only and Dublin-origin-only (v1):
    fixed_pairs date strategies stay built-in, and the origin is locked until
    the tfs= builder replaces byte-patching (Phase 4)."""

    destinations: list[str] | str
    id: str | None = Field(default=None, max_length=32, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str | None = Field(default=None, max_length=80)
    subtitle: str | None = Field(default=None, max_length=120)
    origin: str = LOCKED_ORIGIN
    airline: str | None = Field(default=None, pattern=r"^[A-Z0-9]{2}$")
    airline_name: str | None = Field(default=None, max_length=40)
    weekdays: str | None = Field(default=None, max_length=40)
    eta_minutes: int = Field(default=30, ge=1, le=600)
    configurable_destinations: bool = False


# Deprecated: legacy display list served by GET /api/scanners so a mid-rollout
# frontend keeps working. GET /api/routes (registry-backed) replaces it; both
# this list and the endpoint are removed in Phase 3's frontend commit.
SCANNERS = [
    {
        "id": "turkey",
        "name": "Dublin → Turkey",
        "subtitle": "Aug 2026 weekends · IST, SAW, AYT",
        "origin": "DUB",
        "default_dest": "IST",
        "eta_minutes": 5,
        "combinations": 36,
    },
    {
        "id": "egyptair",
        "name": "Dublin → Cairo",
        "subtitle": "EgyptAir only · Sat/Sun/Tue/Thu",
        "origin": "DUB",
        "default_dest": "CAI",
        "eta_minutes": 30,
        "combinations": 245,
    },
    {
        "id": "ams",
        "name": "Dublin → Amsterdam",
        "subtitle": "All airlines · daily",
        "origin": "DUB",
        "default_dest": "AMS",
        "eta_minutes": 90,
        "combinations": 720,
    },
]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/scanners")
def list_scanners() -> list[dict[str, Any]]:
    return SCANNERS


def _route_entry_dict(entry: RouteEntry) -> dict[str, Any]:
    route = entry.route
    try:
        combinations = estimate_total(route.id, {"destinations": ",".join(route.destinations)})
    except Exception:
        combinations = None
    return {
        "id": route.id,
        "name": route.name,
        "subtitle": route.subtitle,
        "origin": route.origin,
        "destinations": list(route.destinations),
        "default_dest": route.destinations[0],
        "airline": route.airline,
        "airline_name": route.airline_name,
        "date_strategy": route.date_strategy,
        "weekdays": route.weekdays,
        "configurable_destinations": route.configurable_destinations,
        "eta_minutes": route.eta_minutes,
        "combinations": combinations,
        "builtin": entry.builtin,
        # User routes live in the container filesystem only: they survive a
        # restart but are lost on the next image rebuild (until Phase 5).
        "persistent": entry.persistent,
        # URL generation byte-patches a captured Dublin blob (Phase 4 lifts this).
        "origin_locked": True,
    }


@app.get("/api/routes")
def list_routes() -> list[dict[str, Any]]:
    return [_route_entry_dict(e) for e in registry.list_entries()]


@app.post("/api/routes", status_code=201)
def create_route(body: RouteCreate) -> dict[str, Any]:
    if body.origin.strip().upper() != LOCKED_ORIGIN:
        raise HTTPException(
            400,
            f"origin is locked to {LOCKED_ORIGIN} while URL generation byte-patches "
            "a captured Dublin blob (Phase 4 unlocks arbitrary origins)",
        )
    try:
        entry = registry.create(
            destinations=body.destinations,
            id=body.id,
            name=body.name,
            subtitle=body.subtitle,
            airline=body.airline,
            airline_name=body.airline_name,
            weekdays=body.weekdays,
            eta_minutes=body.eta_minutes,
            configurable_destinations=body.configurable_destinations,
        )
    except RouteConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except RegistryError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _route_entry_dict(entry)


@app.delete("/api/routes/{route_id}")
def delete_route(route_id: str) -> dict[str, str]:
    active = [
        j
        for j in store.list_jobs()
        if j.scanner == route_id and j.status in (JobStatus.QUEUED, JobStatus.RUNNING)
    ]
    if active:
        raise HTTPException(409, "route has a running scan; cancel it first")
    try:
        registry.delete(route_id)
    except BuiltinProtected as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError:
        raise HTTPException(404, "Route not found") from None
    return {"status": "deleted"}


@app.post("/api/scans")
def create_scan(body: ScanCreate) -> dict[str, Any]:
    route_id = body.route_id or body.scanner
    if not route_id:
        raise HTTPException(422, "route_id is required")
    if registry.get(route_id) is None:
        raise HTTPException(404, f"Unknown route: {route_id}")

    config = body.model_dump()
    config["route_id"] = route_id
    config["scanner"] = route_id  # deprecated alias kept for older clients
    try:
        total = estimate_total(route_id, config)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        job = store.create(route_id, total, config)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc

    store.start_worker(job.id, run_job)
    return store.get(job.id).to_dict()  # type: ignore[union-attr]


@app.get("/api/scans")
def list_scans() -> list[dict[str, Any]]:
    return [j.to_dict() for j in store.list_jobs()]


@app.get("/api/scans/{job_id}")
def get_scan(job_id: str) -> dict[str, Any]:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


@app.get("/api/scans/{job_id}/results")
def get_scan_results(job_id: str) -> dict[str, Any]:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    priced = [r for r in job.results if r.get("min_price") is not None]
    priced.sort(key=lambda r: r["min_price"])
    return {
        "id": job.id,
        "status": job.status.value,
        "results": job.results,
        "ranked": priced,
        "winner": priced[0] if priced else None,
    }


@app.post("/api/scans/{job_id}/cancel")
def cancel_scan(job_id: str) -> dict[str, str]:
    if not store.cancel(job_id):
        raise HTTPException(404, "Job not found")
    return {"status": "cancelled"}


@app.get("/api/saved-trips")
def saved_trips() -> list[dict[str, Any]]:
    """Load best fares from cached JSON runs (offline preview)."""
    candidates = [
        ROOT / "dub_turkey_all_results.json",
        ROOT / "dub_turkey_saw_ayt.json",
        ROOT / "results-2026-05-27.json",
    ]
    trips: list[dict[str, Any]] = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            rows = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        priced = [r for r in rows if r.get("min_price") is not None]
        if not priced:
            continue
        priced.sort(key=lambda r: r["min_price"])
        best = priced[0]
        dest = best.get("dest", "CAI")
        trips.append(
            {
                "id": path.stem,
                "label": f"DUB → {dest}",
                "price": best["min_price"],
                "currency": "EUR",
                "dep": best.get("dep"),
                "ret": best.get("ret"),
                "airline": (best.get("entries") or [{}])[0].get("airline", "EgyptAir"),
                "source_file": path.name,
            }
        )
    trips.sort(key=lambda t: t["price"])
    return trips[:6]


_dist = ROOT / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
