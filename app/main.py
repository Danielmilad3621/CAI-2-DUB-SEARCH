from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.browser_env import prepare_playwright_browsers
from app.jobs import JobStatus, store
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
    scanner: Literal["turkey", "egyptair", "ams"]
    destinations: str = "IST,SAW,AYT"
    currency: str = "EUR"
    start: str | None = None
    window_days: int = Field(default=60, ge=7, le=366)
    min_trip_days: int = Field(default=3, ge=1, le=30)
    max_trip_days: int = Field(default=14, ge=2, le=60)
    weekdays: str = "Sat,Sun,Tue,Thu"


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


@app.post("/api/scans")
def create_scan(body: ScanCreate) -> dict[str, Any]:
    config = body.model_dump()
    try:
        total = estimate_total(body.scanner, config)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        job = store.create(body.scanner, total, config)
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
