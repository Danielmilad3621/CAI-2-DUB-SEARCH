"""run_scan resilience behaviors, exercised fully offline with a fake browser.

Drives the real engine.run_scan with a programmable FakePage (a sequence of
(url, snapshot) steps consumed one-per-goto) so every AC1/AC2/AC3 path is
reproducible without a network or a real Firefox:

  * a still-loading page is retried, not recorded as empty (AC1);
  * a drifted price-label page is DRIFT_SUSPECTED, not empty (AC1);
  * a persistent provider-error page after all retries is PROVIDER_ERROR, not
    empty (AC2);
  * a parser/navigation exception on one pair records a single typed error row
    and the scan CONTINUES (the parse used to abort the whole job) (AC3);
  * a sign-in wall aborts the scan as a typed FAILED state and the browser is
    always torn down (AC2 + leak fix);
  * every pair emits one structured log record with diagnostic fields (AC3).

Requires pytest (uses monkeypatch + caplog).
"""

from __future__ import annotations

import datetime as dt
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import engine  # noqa: E402
from app.engine import Route, ScanBlocked, run_scan  # noqa: E402
from app.jobs import JobStatus, ScanJob  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
FLIGHTS_URL = "https://www.google.com/travel/flights/search?tfs=X&hl=en&curr=EUR"
HOME = (FLIGHTS_URL, '- main:\n  - heading "Search results" [level=2]\n')
PRICED = (
    FLIGHTS_URL,
    '- list:\n  - listitem:\n    - link "From 242 euros round trip total. '
    'Nonstop flight with KLM. Leaves Dublin Airport at 1:25 PM. Select flight"\n',
)
DEP, RET = dt.date(2026, 8, 7), dt.date(2026, 8, 10)
DEP2, RET2 = dt.date(2026, 8, 14), dt.date(2026, 8, 17)


def _fix(stem: str) -> str:
    return (FIX / f"{stem}_snapshot.txt").read_text()


class FakePage:
    """Serves one (url, snapshot) step per goto(); a step snapshot that is an
    Exception is raised from aria_snapshot() to simulate a torn-down page."""

    def __init__(self, steps: list[tuple[str, object]]) -> None:
        self.steps = steps
        self.i = -1
        self.goto_urls: list[str] = []
        self.waits: list[int] = []

    def goto(self, url: str, **_kw) -> None:
        self.goto_urls.append(url)
        self.i = min(self.i + 1, len(self.steps) - 1)

    @property
    def url(self) -> str:
        return self.steps[max(self.i, 0)][0]

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)

    def wait_for_load_state(self, *_a, **_k) -> None:
        pass

    def locator(self, _sel: str) -> FakePage:
        return self

    def aria_snapshot(self) -> str:
        snap = self.steps[max(self.i, 0)][1]
        if isinstance(snap, BaseException):
            raise snap
        return snap  # type: ignore[return-value]

    def get_by_role(self, *_a, **_k) -> FakePage:
        return self

    def click(self, *_a, **_k) -> None:
        pass


class FakeContext:
    def __init__(self, page: FakePage, *, fail_new_page: bool = False) -> None:
        self._page = page
        self._fail = fail_new_page

    def new_page(self) -> FakePage:
        if self._fail:
            raise RuntimeError("new_page boom")
        return self._page


class FakeBrowser:
    def __init__(self, page: FakePage, *, fail_new_page: bool = False) -> None:
        self._page = page
        self._fail = fail_new_page
        self.closed = False

    def new_context(self, **_kw) -> FakeContext:
        return FakeContext(self._page, fail_new_page=self._fail)

    def close(self) -> None:
        self.closed = True


class _PW:
    def __enter__(self):
        return None

    def __exit__(self, *_a):
        return False


def _route(**kw) -> Route:
    base = dict(
        id="ams", name="DUB -> AMS", subtitle="t", origin="DUB",
        destinations=("AMS",), date_strategy="window",
    )
    base.update(kw)
    return Route(**base)


def _job() -> ScanJob:
    return ScanJob(id="job-test", scanner="ams", status=JobStatus.RUNNING, total=0)


def _patch(monkeypatch, browser: FakeBrowser, pairs, dests=("AMS",)) -> None:
    monkeypatch.setattr(engine, "prepare_playwright_browsers", lambda: "test")
    monkeypatch.setattr(engine, "sync_playwright", lambda: _PW())
    monkeypatch.setattr(engine, "_launch_firefox", lambda p, headless=True: browser)
    monkeypatch.setattr(engine, "destinations_for", lambda r, c: dests)
    monkeypatch.setattr(engine, "enumerate_pairs", lambda r, c: list(pairs))


def _run(monkeypatch, steps, pairs, route=None, dests=("AMS",)):
    page = FakePage(list(steps))
    browser = FakeBrowser(page)
    _patch(monkeypatch, browser, pairs, dests)
    job = _job()
    run_scan(route or _route(), job, lambda: False, {}, headless=True)
    return job, page, browser


def test_partial_page_is_retried_then_priced(monkeypatch) -> None:
    job, page, _ = _run(
        monkeypatch,
        [HOME, (FLIGHTS_URL, _fix("loading_partial")), PRICED],
        [(DEP, RET)],
    )
    assert len(job.results) == 1
    row = job.results[0]
    assert row["outcome"] == "priced" and row["min_price"] == 242
    # warm-up goto + 2 attempts (loading -> retry -> priced).
    assert len(page.goto_urls) == 3


def test_drift_recorded_as_drift_not_empty(monkeypatch) -> None:
    job, _, _ = _run(monkeypatch, [HOME, (FLIGHTS_URL, _fix("drift_round_trip"))], [(DEP, RET)])
    row = job.results[0]
    assert row["outcome"] == "drift_suspected"
    assert row["min_price"] is None


def test_persistent_provider_error_is_not_empty(monkeypatch) -> None:
    oops = (FLIGHTS_URL, _fix("oops_error"))
    job, page, _ = _run(monkeypatch, [HOME, oops, oops, oops, oops], [(DEP, RET)])
    row = job.results[0]
    assert row["outcome"] == "provider_error"  # NOT "empty"
    assert row["min_price"] is None
    assert len(page.goto_urls) == 1 + engine._GOTO_ATTEMPTS  # warm-up + all retries


def test_parse_exception_records_one_row_and_continues(monkeypatch) -> None:
    # pair 1's snapshot raises; pair 2 must still be scanned (parse was outside try).
    job, _, browser = _run(
        monkeypatch,
        [HOME, (FLIGHTS_URL, ValueError("aria drift")), PRICED],
        [(DEP, RET), (DEP2, RET2)],
    )
    assert len(job.results) == 2
    # A parser exception (ValueError from aria_snapshot) maps to the typed PARSE_ERROR.
    assert job.results[0]["outcome"] == "parse_error"
    assert job.results[0]["min_price"] is None and "error" in job.results[0]
    assert job.results[1]["outcome"] == "priced" and job.results[1]["min_price"] == 242
    assert job.done == 2
    assert browser.closed is True


def test_signin_wall_aborts_scan_and_closes_browser(monkeypatch) -> None:
    signin = ("https://accounts.google.com/ServiceLogin?x=1", _fix("signin_redirect"))
    page = FakePage([HOME, signin])
    browser = FakeBrowser(page)
    _patch(monkeypatch, browser, [(DEP, RET), (DEP2, RET2)])
    job = _job()
    with pytest.raises(ScanBlocked):
        run_scan(_route(), job, lambda: False, {}, headless=True)
    assert job.error_kind == "wall_signin"
    assert job.results[-1]["outcome"] == "wall_signin"
    assert browser.closed is True  # teardown ran even though run_scan raised


def test_browser_closed_on_setup_exception(monkeypatch) -> None:
    page = FakePage([HOME])
    browser = FakeBrowser(page, fail_new_page=True)
    _patch(monkeypatch, browser, [(DEP, RET)])
    with pytest.raises(RuntimeError, match="new_page boom"):
        run_scan(_route(), _job(), lambda: False, {}, headless=True)
    assert browser.closed is True


def test_each_pair_emits_structured_log(monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO, logger="dub.engine")
    _run(monkeypatch, [HOME, PRICED], [(DEP, RET)])
    pair_logs = [r for r in caplog.records if r.getMessage() == "scan pair"]
    assert len(pair_logs) == 1
    rec = pair_logs[0]
    for field in ("outcome", "match_count", "snapshot_len", "url", "elapsed_ms", "dest", "dep", "ret"):
        assert hasattr(rec, field), f"log record missing {field}"
    assert rec.outcome == "priced"
    assert rec.match_count == 1
    assert rec.snapshot_len > 0


def test_deadline_stops_scan_early(monkeypatch) -> None:
    # A negative deadline is guaranteed already-exceeded (no reliance on timer
    # resolution): the per-pair guard trips before any pair runs and the scan
    # returns cleanly rather than running for hours.
    page = FakePage([HOME, PRICED, PRICED])
    browser = FakeBrowser(page)
    _patch(monkeypatch, browser, [(DEP, RET), (DEP2, RET2)])
    job = _job()
    run_scan(_route(), job, lambda: False, {"max_scan_seconds": -1}, headless=True)
    assert job.results == []  # deadline tripped before the first pair
    assert browser.closed is True


def test_warmup_wall_aborts_before_any_pair(monkeypatch) -> None:
    # A blocking wall reached at warm-up (step 0) aborts before any pair runs.
    signin = ("https://accounts.google.com/ServiceLogin?x=1", _fix("signin_redirect"))
    page = FakePage([signin])  # warm-up lands directly on the wall
    browser = FakeBrowser(page)
    _patch(monkeypatch, browser, [(DEP, RET)])
    job = _job()
    with pytest.raises(ScanBlocked):
        run_scan(_route(), job, lambda: False, {}, headless=True)
    assert job.error_kind == "wall_signin"
    assert job.results == []  # no pair ran
    assert browser.closed is True


def test_captcha_wall_aborts_scan(monkeypatch) -> None:
    # WALL_CAPTCHA hit on a per-pair navigation aborts the scan (session burned),
    # driving the blocking branch end-to-end (not just classify).
    captcha = ("https://www.google.com/sorry/index?q=x", _fix("unusual_traffic"))
    page = FakePage([HOME, captcha])
    browser = FakeBrowser(page)
    _patch(monkeypatch, browser, [(DEP, RET), (DEP2, RET2)])
    job = _job()
    with pytest.raises(ScanBlocked):
        run_scan(_route(), job, lambda: False, {}, headless=True)
    assert job.error_kind == "wall_captcha"
    assert job.results[-1]["outcome"] == "wall_captcha"
    assert browser.closed is True


def test_midscan_consent_is_dismissed_then_priced(monkeypatch) -> None:
    # A consent wall mid-scan is auto-dismissed once and the pair retried to a price.
    consent = ("https://consent.google.com/m?continue=x", _fix("consent_wall"))
    job, page, _ = _run(monkeypatch, [HOME, consent, PRICED], [(DEP, RET)])
    assert job.results[0]["outcome"] == "priced"
    # warm-up goto + consent attempt + retry-after-dismiss attempt.
    assert len(page.goto_urls) == 3
