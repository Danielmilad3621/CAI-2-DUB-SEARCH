"""Job state-machine hardening: atomic terminal transitions, BaseException
safety, and bounded retention. Uses fresh JobStore instances (not the global
singleton) so tests don't contend over the single-active gate.

Requires pytest (uses monkeypatch).
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import jobs  # noqa: E402
from app.jobs import JobStatus, JobStore  # noqa: E402


def _wait_status(st: JobStore, jid: str, wanted, timeout: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = st.get(jid)
        if j and j.status in wanted:
            return j.status
        time.sleep(0.005)
    got = st.get(jid).status if st.get(jid) else None
    raise AssertionError(f"status {[s.value for s in wanted]} not reached; got {got}")


def test_cancel_during_run_is_not_overwritten_by_completed() -> None:
    # The lost-cancellation race: a user cancel that lands while the worker is
    # finishing must not be flipped back to COMPLETED. Run the interleaving many
    # times — with the fix it is deterministic, without it this would flake.
    for _ in range(30):
        st = JobStore()
        gate = threading.Event()
        started = threading.Event()

        def runner(job, cancel_check, _g=gate, _s=started):
            _s.set()
            _g.wait(2.0)

        job = st.create("r", 1, {})
        st.start_worker(job.id, runner)
        assert started.wait(2.0)
        _wait_status(st, job.id, {JobStatus.RUNNING})
        assert st.cancel(job.id) is True
        assert st.get(job.id).status == JobStatus.CANCELLED
        gate.set()
        time.sleep(0.03)  # let the worker finalize
        assert st.get(job.id).status == JobStatus.CANCELLED, "cancellation was lost"


def test_cancel_on_completed_job_does_not_flip_status() -> None:
    st = JobStore()

    def runner(job, cancel_check):
        job.results.append({"min_price": 100})

    job = st.create("r", 1, {})
    st.start_worker(job.id, runner)
    _wait_status(st, job.id, {JobStatus.COMPLETED})
    assert st.cancel(job.id) is True  # job exists, so not a 404
    assert st.get(job.id).status == JobStatus.COMPLETED  # but stays COMPLETED


def test_systemexit_in_runner_marks_failed_not_stuck() -> None:
    st = JobStore()

    def runner(job, cancel_check):
        raise SystemExit("boom")  # a BaseException that 'except Exception' would miss

    job = st.create("r", 1, {})
    st.start_worker(job.id, runner)
    assert _wait_status(st, job.id, {JobStatus.FAILED}) == JobStatus.FAILED
    assert st.get(job.id).error is not None
    # _active_id must be cleared so the store isn't wedged at 409 forever.
    assert st.create("r2", 1, {}).id


def test_scanblocked_runner_marks_failed_with_error_kind() -> None:
    # A ScanBlocked (RuntimeError subclass, raised by run_scan on a sign-in/CAPTCHA
    # wall) must terminate the job as FAILED while preserving error_kind.
    from app.engine import ScanBlocked, ScanOutcome

    st = JobStore()

    def runner(job, cancel_check):
        job.error_kind = "wall_signin"  # run_scan sets this before raising
        raise ScanBlocked(ScanOutcome.WALL_SIGNIN)

    job = st.create("r", 1, {})
    st.start_worker(job.id, runner)
    assert _wait_status(st, job.id, {JobStatus.FAILED}) == JobStatus.FAILED
    assert st.get(job.id).error_kind == "wall_signin"  # not clobbered by _fail
    assert st.get(job.id).error is not None


def test_retention_evicts_oldest_terminal_jobs(monkeypatch) -> None:
    monkeypatch.setattr(jobs, "MAX_RETAINED_JOBS", 3)
    st = JobStore()
    ids = []
    for i in range(6):
        job = st.create(f"r{i}", 1, {})
        ids.append(job.id)
        job.status = JobStatus.COMPLETED  # terminal: evictable + releases the gate
        st._active_id = None
    assert len(st._jobs) <= 3
    assert ids[0] not in st._jobs
    assert ids[0] not in st._cancel_flags  # cancel-flag evicted too (no leak)
    assert ids[-1] in st._jobs
