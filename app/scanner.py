"""API-side scan orchestration.

Thin adapter between the job store (app/jobs.py, called from app/main.py)
and the generic engine (app/engine.py). The per-route run_* functions and
pair-count duplicates that used to live here are gone — routes are looked up
in app/route_defs.py and dispatched to engine.run_scan.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.engine import Route, estimate_pairs, run_scan
from app.jobs import ScanJob
from app.route_defs import BUILTIN_ROUTES


def _route(scanner: str) -> Route:
    route = BUILTIN_ROUTES.get(scanner)
    if route is None:
        raise ValueError(f"Unknown scanner: {scanner}")
    return route


def estimate_total(scanner: str, config: dict[str, Any]) -> int:
    return estimate_pairs(_route(scanner), config)


def run_job(job: ScanJob, cancel_check: Callable[[], bool]) -> None:
    run_scan(_route(job.scanner), job, cancel_check, job.config)
