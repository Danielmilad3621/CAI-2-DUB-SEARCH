from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from app.browser_env import prepare_playwright_browsers
from app.engine import MAX_PAIRS_PER_SCAN, Route, estimate_pairs, run_scan
from app.jobs import JobStatus, store
from app.registry import (
    LOCKED_ORIGIN,
    MAX_DESTINATIONS,
    BuiltinProtected,
    RegistryError,
    RouteConflict,
    RouteEntry,
    registry,
)
from app.scanner import estimate_total, run_job

API_VERSION = "0.2.0"
PROVIDER = "google-flights (tfs= search URL, scraped via Playwright/Firefox)"

prepare_playwright_browsers()

ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(
    title="DUB Flight Search Engine",
    version=API_VERSION,
    description=(
        "Headless flight-search API over Google Flights. A search is a reverse-engineered "
        "`tfs=` round-trip URL scraped headless with Playwright/Firefox, so it drives a real "
        "browser across every (destination x date-pair) combination and is therefore "
        "**asynchronous**: `POST /api/search` enqueues a job and returns `202` immediately, then "
        "you poll `GET /api/scans/{id}` for progress and `GET /api/scans/{id}/results` for ranked "
        "fares.\n\n"
        "**Provider constraints** (properties of the captured Google Flights blob, surfaced as "
        "`4xx` or documented no-ops rather than silent behavior): the origin is pinned to Dublin "
        "(DUB); the blob encodes a single-adult round trip; nonstop search is currently "
        "Dublin->Cairo only."
    ),
    openapi_tags=[
        {"name": "search", "description": "Run an ad-hoc flight search and read its results."},
        {"name": "scans", "description": "Inspect, list and cancel search jobs."},
        {"name": "routes", "description": "Built-in and user-defined saved routes."},
        {"name": "system", "description": "Health check and cached sample data."},
    ],
)

# A headless JSON API is normally consumed server-to-server, so default to an
# open CORS policy; pin ALLOWED_ORIGINS (comma-separated) to lock it down.
_origins_raw = os.getenv("ALLOWED_ORIGINS", "*")
_origins = [o.strip() for o in _origins_raw.split(",") if o.strip()] or ["*"]
# Credentials cannot be combined with the "*" wildcard (CORS spec); this API
# uses no cookies, so credentials are only enabled for an explicit origin list.
_allow_credentials = _origins != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScanCreate(BaseModel):
    """Body for POST /api/scans — runs a *saved* route (by id) over a date window."""

    route_id: str = Field(description="Id of a route from GET /api/routes.", examples=["turkey"])
    destinations: str = Field(default="IST,SAW,AYT",
        description="Comma-separated overrides, honored only by routes with configurable_destinations.")
    currency: str = Field(default="EUR", pattern=r"^[A-Za-z]{3}$",
        description="ISO-4217 currency for displayed fares.")
    start: str | None = Field(default=None, description="Earliest departure date YYYY-MM-DD (default: today).")
    window_days: int = Field(default=60, ge=7, le=366)
    min_trip_days: int = Field(default=3, ge=1, le=30)
    max_trip_days: int = Field(default=14, ge=2, le=60)
    weekdays: str = Field(default="Sat,Sun,Tue,Thu",
        description="Comma-separated 3-letter operating days (ignored by routes that pin their own).")


class SearchRequest(BaseModel):
    """Body for POST /api/search — an ad-hoc round-trip search built on the fly.

    Unlike POST /api/scans (which references a stored route id), this constructs
    an ephemeral route directly from the request and runs it as a background job.
    """

    origin: str = Field(default=LOCKED_ORIGIN, examples=["DUB"],
        description="Origin airport IATA. Engine is pinned to DUB; any other value returns 400.")
    destinations: list[str] | str = Field(examples=[["IST", "SAW", "AYT"]],
        description="1-5 destination IATA codes, as a JSON array or comma-separated string.")
    currency: str = Field(default="EUR", pattern=r"^[A-Za-z]{3}$", examples=["EUR"],
        description="ISO-4217 currency for displayed fares.")
    airline: str | None = Field(default=None, pattern=r"^[A-Z0-9]{2}$", examples=["MS"],
        description="Optional 2-letter airline IATA. Stored on the search; note that result "
                    "filtering is done by airline_name (the provider's airline filter is stale).")
    airline_name: str | None = Field(default=None, max_length=40, examples=["EgyptAir"],
        description="Airline display name. When set, results are restricted to rows naming this carrier.")
    nonstop: bool = Field(default=False,
        description="Direct flights only. Currently supported for DUB->CAI only (else 400).")
    start: str | None = Field(default=None, examples=["2026-07-01"],
        description="Earliest departure date YYYY-MM-DD. Defaults to the server's current date.")
    window_days: int = Field(default=60, ge=7, le=366,
        description="Days after `start` to include in the search window.")
    min_trip_days: int = Field(default=3, ge=1, le=30, description="Minimum trip length in days.")
    max_trip_days: int = Field(default=14, ge=2, le=60, description="Maximum trip length in days.")
    weekdays: str = Field(default="Sat,Sun,Tue,Thu",
        description="Comma-separated 3-letter operating days for both departure and return.")
    adults: int = Field(default=1, ge=1, le=9,
        description="Passenger count. The captured provider URL encodes a single-adult round trip; "
                    "values > 1 are accepted and echoed back but do not yet change provider pricing.")

    @model_validator(mode="after")
    def _validate(self) -> SearchRequest:
        if self.max_trip_days < self.min_trip_days:
            raise ValueError("max_trip_days must be >= min_trip_days")
        if self.start is not None:
            try:
                dt.date.fromisoformat(self.start)
            except ValueError as exc:
                raise ValueError(f"start must be an ISO date (YYYY-MM-DD): {exc}") from exc
        return self


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


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    """Liveness probe. Returns 200 while the API process is up."""
    return {"status": "ok", "version": API_VERSION, "provider": PROVIDER}


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
        "nonstop": route.nonstop,
        "combinations": combinations,
        "builtin": entry.builtin,
        # User routes live in the container filesystem only: they survive a
        # restart but are lost on the next image rebuild (until Phase 5).
        "persistent": entry.persistent,
        # URL generation byte-patches a captured Dublin blob (Phase 4 lifts this).
        "origin_locked": True,
    }


@app.get("/api/routes", tags=["routes"])
def list_routes() -> list[dict[str, Any]]:
    """List built-in and user-defined routes with their estimated combination counts."""
    return [_route_entry_dict(e) for e in registry.list_entries()]


@app.post("/api/routes", status_code=201, tags=["routes"])
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


@app.delete("/api/routes/{route_id}", tags=["routes"])
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


def _build_search_route(body: SearchRequest) -> Route:
    """Construct an ephemeral (unregistered) Route from a search request.

    Reuses Route's own validation (IATA codes, nonstop=DUB->CAI only, etc.);
    any ValueError it raises becomes an HTTP 400 capability response.
    """
    raw = body.destinations
    codes = [c.strip().upper() for c in (raw.split(",") if isinstance(raw, str) else raw) if str(c).strip()]
    if not codes:
        raise HTTPException(422, "destinations must contain at least one IATA code")
    if len(codes) > MAX_DESTINATIONS:
        raise HTTPException(422, f"at most {MAX_DESTINATIONS} destinations per search")
    route_id = "-".join(c.lower() for c in codes)
    try:
        return Route(
            id=route_id,
            name=f"{LOCKED_ORIGIN} -> {', '.join(codes)}",
            subtitle="ad-hoc search",
            origin=LOCKED_ORIGIN,
            destinations=tuple(codes),
            airline=body.airline,
            airline_name=body.airline_name,
            date_strategy="window",
            weekdays=body.weekdays,
            configurable_destinations=False,
            eta_minutes=30,
            nonstop=body.nonstop,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/search", status_code=202, tags=["search"])
def search(body: SearchRequest) -> dict[str, Any]:
    """Enqueue an ad-hoc flight search and return a job handle (HTTP 202).

    The scan runs in the background (one at a time — a single browser is shared),
    so poll `links.status` for progress and `links.results` for ranked fares.
    """
    if body.origin.strip().upper() != LOCKED_ORIGIN:
        raise HTTPException(
            400,
            f"origin is locked to {LOCKED_ORIGIN}: the captured Google Flights blob encodes "
            "Dublin as a knowledge-graph id, so other origins are not yet supported",
        )

    route = _build_search_route(body)
    config = {
        "currency": body.currency,
        "start": body.start,
        "window_days": body.window_days,
        "min_trip_days": body.min_trip_days,
        "max_trip_days": body.max_trip_days,
        "weekdays": body.weekdays,
        "adults": body.adults,
        "destinations": ",".join(route.destinations),
    }

    try:
        total = estimate_pairs(route, config)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    if total == 0:
        raise HTTPException(422, "search window yields zero date pairs; widen window_days or trip days")
    if total > MAX_PAIRS_PER_SCAN:
        raise HTTPException(
            422,
            f"search would scan {total} (destination x date-pair) combinations; the max is "
            f"{MAX_PAIRS_PER_SCAN}. Narrow window_days, trip days, or destinations.",
        )

    try:
        job = store.create(route.id, total, config)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc

    store.start_worker(job.id, lambda j, cancel_check: run_scan(route, j, cancel_check, config))

    payload = store.get(job.id).to_dict()  # type: ignore[union-attr]
    payload["links"] = {
        "status": f"/api/scans/{job.id}",
        "results": f"/api/scans/{job.id}/results",
        "cancel": f"/api/scans/{job.id}/cancel",
    }
    notes = [f"origin pinned to {LOCKED_ORIGIN}"]
    if body.adults > 1:
        notes.append("adults>1 accepted but the provider currently prices a single adult")
    if body.airline and not body.airline_name:
        notes.append("airline code stored but does not filter results; pass airline_name to filter")
    if body.nonstop:
        notes.append("nonstop search uses the fresh DUB->Cairo blob (EgyptAir is the only nonstop carrier)")
    payload["capability_notes"] = notes
    return payload


@app.post("/api/scans", tags=["scans"])
def create_scan(body: ScanCreate) -> dict[str, Any]:
    route_id = body.route_id
    if registry.get(route_id) is None:
        raise HTTPException(404, f"Unknown route: {route_id}")

    config = body.model_dump()
    try:
        total = estimate_total(route_id, config)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    if total == 0:
        raise HTTPException(422, "scan window yields zero date pairs; widen window_days or trip days")
    if total > MAX_PAIRS_PER_SCAN:
        raise HTTPException(
            422,
            f"scan would run {total} (destination x date-pair) combinations; the max is "
            f"{MAX_PAIRS_PER_SCAN}. Narrow window_days, trip days, or destinations.",
        )

    try:
        job = store.create(route_id, total, config)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc

    store.start_worker(job.id, run_job)
    return store.get(job.id).to_dict()  # type: ignore[union-attr]


@app.get("/api/scans", tags=["scans"])
def list_scans() -> list[dict[str, Any]]:
    """List recent search jobs (most recent first, capped at 20)."""
    return [j.to_dict() for j in store.list_jobs()]


@app.get("/api/scans/{job_id}", tags=["scans"])
def get_scan(job_id: str) -> dict[str, Any]:
    """Get a single job's status, progress and current best fare."""
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


@app.get("/api/scans/{job_id}/results", tags=["scans"])
def get_scan_results(job_id: str) -> dict[str, Any]:
    """Get a job's full result rows plus a price-ranked view and the winner."""
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    results = list(job.results)  # snapshot once for an internally-consistent response
    priced = [r for r in results if r.get("min_price") is not None]
    priced.sort(key=lambda r: r["min_price"])
    return {
        "id": job.id,
        "status": job.status.value,
        "results": results,
        "ranked": priced,
        "winner": priced[0] if priced else None,
    }


@app.post("/api/scans/{job_id}/cancel", tags=["scans"])
def cancel_scan(job_id: str) -> dict[str, str]:
    """Request cancellation of a queued or running job."""
    if not store.cancel(job_id):
        raise HTTPException(404, "Job not found")
    return {"status": "cancelled"}


@app.get("/api/saved-trips", tags=["system"])
def saved_trips() -> list[dict[str, Any]]:
    """Best fares from the bundled cached result files (offline sample data, no scan)."""
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
