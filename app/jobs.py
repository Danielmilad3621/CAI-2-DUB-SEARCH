from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

log = logging.getLogger("dub.jobs")

# Cap on retained jobs. The store is in-memory and was previously unbounded
# (list_jobs only capped the *view*); we now evict the oldest TERMINAL jobs once
# the cap is exceeded so a long-lived process does not leak memory. The active
# job is never evicted.
MAX_RETAINED_JOBS = 200


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Write-once terminal states: once a job reaches one of these, no transition may
# overwrite it (this is what closes the lost-cancellation race).
_TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})


@dataclass
class ScanJob:
    id: str
    scanner: str
    status: JobStatus
    total: int
    done: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    error_kind: str | None = None  # typed terminal reason, e.g. "wall_signin" (AC2)
    config: dict[str, Any] = field(default_factory=dict)
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        # Iterate a shallow copy so a concurrent worker append can't mutate the list
        # mid-sort. Under the GIL the copy may include one extra appended row, which
        # is harmless — the goal is a stable list to rank, not a precise cutoff.
        results = list(self.results)
        priced = [r for r in results if r.get("min_price") is not None]
        priced.sort(key=lambda r: r["min_price"])
        done = self.done
        return {
            "id": self.id,
            "route_id": self.scanner,
            "status": self.status.value,
            "total": self.total,
            "done": done,
            "progress_pct": round(100 * done / self.total, 1) if self.total else 0,
            "results_count": len(results),
            "error": self.error,
            "error_kind": self.error_kind,
            "message": self.message,
            "config": self.config,
            "winner": priced[0] if priced else None,
            "top_results": priced[:20],
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, ScanJob] = {}
        self._lock = threading.Lock()
        self._active_id: str | None = None
        self._cancel_flags: dict[str, threading.Event] = {}

    def get(self, job_id: str) -> ScanJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 20) -> list[ScanJob]:
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.id, reverse=True)
        return jobs[:limit]

    def _evict_locked(self) -> None:
        """Evict oldest TERMINAL jobs beyond MAX_RETAINED_JOBS. Caller holds the lock.

        `dict` preserves insertion order, so iterating yields oldest-first; the
        active job and any non-terminal job are never evicted.
        """
        while len(self._jobs) > MAX_RETAINED_JOBS:
            victim = next(
                (
                    jid
                    for jid, j in self._jobs.items()
                    if jid != self._active_id and j.status in _TERMINAL
                ),
                None,
            )
            if victim is None:
                break  # nothing currently evictable
            del self._jobs[victim]
            self._cancel_flags.pop(victim, None)

    def create(self, scanner: str, total: int, config: dict[str, Any]) -> ScanJob:
        with self._lock:
            if self._active_id:
                active = self._jobs.get(self._active_id)
                if active and active.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                    raise RuntimeError("A scan is already running. Wait for it to finish.")
            job_id = str(uuid.uuid4())
            job = ScanJob(
                id=job_id,
                scanner=scanner,
                status=JobStatus.QUEUED,
                total=total,
                config=config,
            )
            self._jobs[job_id] = job
            self._active_id = job_id
            self._cancel_flags[job_id] = threading.Event()
            self._evict_locked()
            return job

    def cancel(self, job_id: str) -> bool:
        """Request cancellation. Returns True iff the job exists (so the API keeps
        404-on-missing semantics). The status flip happens UNDER the lock and only
        for a non-terminal job, so it can never overwrite or be overwritten by the
        worker's terminal transition (closes the lost-cancellation race)."""
        with self._lock:
            ev = self._cancel_flags.get(job_id)
            job = self._jobs.get(job_id)
            if not ev or not job:
                return False
            ev.set()
            if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                job.status = JobStatus.CANCELLED
                job.message = "Cancelled by user"
            return True

    def is_cancelled(self, job_id: str) -> bool:
        ev = self._cancel_flags.get(job_id)
        return bool(ev and ev.is_set())

    def _finalize(self, job_id: str, *, cancelled: bool) -> None:
        """Move a RUNNING job to its terminal success/cancel state, write-once."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status in _TERMINAL:
                return  # never overwrite a terminal status (e.g. a user CANCELLED)
            if cancelled:
                job.status = JobStatus.CANCELLED
                job.message = "Cancelled"
            else:
                job.status = JobStatus.COMPLETED
                job.message = "Scan complete"

    def _fail(self, job_id: str, exc: BaseException) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status in _TERMINAL:
                return  # a cancellation that surfaced as an exception stays CANCELLED
            job.status = JobStatus.FAILED
            job.error = str(exc)
            job.message = "Scan failed"

    def start_worker(
        self, job_id: str, runner: Callable[[ScanJob, Callable[[], bool]], None]
    ) -> None:
        def _run() -> None:
            job = self.get(job_id)
            if not job:
                return
            cancel_check = lambda: self.is_cancelled(job_id)  # noqa: E731
            try:
                if cancel_check():
                    self._finalize(job_id, cancelled=True)
                    return
                with self._lock:
                    if job.status == JobStatus.QUEUED:
                        job.status = JobStatus.RUNNING
                        job.message = "Starting browser…"
                runner(job, cancel_check)
                self._finalize(job_id, cancelled=cancel_check())
            except BaseException as exc:  # noqa: BLE001
                # Catch BaseException (not just Exception): a SystemExit raised deep
                # in the engine must NOT escape the worker thread and leave the job
                # stuck RUNNING forever. The job always reaches a terminal state.
                log.exception(
                    "scan job failed", extra={"job_id": job_id, "route_id": job.scanner}
                )
                self._fail(job_id, exc)
            finally:
                with self._lock:
                    if self._active_id == job_id:
                        self._active_id = None

        threading.Thread(target=_run, daemon=True).start()


store = JobStore()
