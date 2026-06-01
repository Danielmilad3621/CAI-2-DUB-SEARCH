from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ScanJob:
    id: str
    scanner: str
    status: JobStatus
    total: int
    done: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        priced = [r for r in self.results if r.get("min_price") is not None]
        priced.sort(key=lambda r: r["min_price"])
        return {
            "id": self.id,
            "scanner": self.scanner,
            "status": self.status.value,
            "total": self.total,
            "done": self.done,
            "progress_pct": round(100 * self.done / self.total, 1) if self.total else 0,
            "results_count": len(self.results),
            "error": self.error,
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
            return job

    def cancel(self, job_id: str) -> bool:
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

    def start_worker(self, job_id: str, runner: Callable[[ScanJob, Callable[[], bool]], None]) -> None:
        def _run() -> None:
            job = self.get(job_id)
            if not job:
                return
            cancel_check = lambda: self.is_cancelled(job_id)
            try:
                job.status = JobStatus.RUNNING
                job.message = "Starting browser…"
                runner(job, cancel_check)
                if cancel_check():
                    job.status = JobStatus.CANCELLED
                    job.message = "Cancelled"
                elif job.status != JobStatus.CANCELLED:
                    job.status = JobStatus.COMPLETED
                    job.message = "Scan complete"
            except Exception as exc:
                job.status = JobStatus.FAILED
                job.error = str(exc)
                job.message = "Scan failed"
                # #region agent log
                try:
                    from app.debug_log import debug_log

                    debug_log(
                        "D",
                        "jobs.py:_run",
                        "job failed",
                        {"job_id": job_id, "error": str(exc)[:500]},
                    )
                except Exception:
                    pass
                # #endregion
            finally:
                with self._lock:
                    if self._active_id == job_id:
                        self._active_id = None

        threading.Thread(target=_run, daemon=True).start()


store = JobStore()
