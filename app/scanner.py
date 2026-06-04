from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

from playwright.sync_api import sync_playwright

import cheapest_dub_ams as ams
import cheapest_dub_cai_egyptair as egyptair
import cheapest_dub_turkey as turkey

AMS_DEFAULT_WEEKDAYS = "Mon,Tue,Wed,Thu,Fri,Sat,Sun"

from app.browser_env import prepare_playwright_browsers, resolve_firefox_executable
from app.jobs import ScanJob


def _launch_firefox(p, prefs: dict) -> object:
    resolved = resolve_firefox_executable()
    launch_kwargs: dict = {"headless": True, "firefox_user_prefs": prefs}
    if resolved:
        launch_kwargs["executable_path"] = resolved
    return p.firefox.launch(**launch_kwargs)


def _count_egyptair_pairs(config: dict[str, Any]) -> int:
    start_raw = config.get("start") or dt.date.today().isoformat()
    start = dt.date.fromisoformat(start_raw)
    end = start + dt.timedelta(days=int(config.get("window_days", 60)))
    weekdays = egyptair._parse_weekdays(config.get("weekdays", "Sat,Sun,Tue,Thu"))
    min_days = int(config.get("min_trip_days", 3))
    max_days = int(config.get("max_trip_days", 14))
    deps = list(egyptair._valid_days(start + dt.timedelta(days=1), end, weekdays))
    pairs = [
        (dep, ret)
        for dep in deps
        for ret in egyptair._valid_days(
            dep + dt.timedelta(days=min_days),
            dep + dt.timedelta(days=max_days),
            weekdays,
        )
    ]
    return len(pairs)


def _count_ams_pairs(config: dict[str, Any]) -> int:
    start_raw = config.get("start") or dt.date.today().isoformat()
    start = dt.date.fromisoformat(start_raw)
    end = start + dt.timedelta(days=int(config.get("window_days", 60)))
    weekdays = ams._parse_weekdays(AMS_DEFAULT_WEEKDAYS)
    min_days = int(config.get("min_trip_days", 3))
    max_days = int(config.get("max_trip_days", 14))
    deps = list(ams._valid_days(start + dt.timedelta(days=1), end, weekdays))
    pairs = [
        (dep, ret)
        for dep in deps
        for ret in ams._valid_days(
            dep + dt.timedelta(days=min_days),
            dep + dt.timedelta(days=max_days),
            weekdays,
        )
    ]
    return len(pairs)


def estimate_total(scanner: str, config: dict[str, Any]) -> int:
    if scanner == "turkey":
        dests = [d.strip().upper() for d in config.get("destinations", "IST,SAW,AYT").split(",") if d.strip()]
        return len(turkey.august_2026_pairs()) * len(dests)
    if scanner == "egyptair":
        return _count_egyptair_pairs(config)
    if scanner == "ams":
        return _count_ams_pairs(config)
    raise ValueError(f"Unknown scanner: {scanner}")


def run_turkey_scan(
    job: ScanJob,
    cancel_check: Callable[[], bool],
    *,
    destinations: str = "IST,SAW,AYT",
    currency: str = "EUR",
) -> None:
    dests = [d.strip().upper() for d in destinations.split(",") if d.strip()]
    pairs = turkey.august_2026_pairs()
    total = len(pairs) * len(dests)
    job.total = total

    prepare_playwright_browsers()
    with sync_playwright() as p:
        browser = _launch_firefox(p, turkey.FIREFOX_PREFS)
        ctx = browser.new_context(viewport={"width": 1280, "height": 1800}, locale="en-IE")
        page = ctx.new_page()

        page.goto(
            f"https://www.google.com/travel/flights?hl=en&curr={currency}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(2500)
        turkey._reject_consent(page)

        n = 0
        for dest in dests:
            if cancel_check():
                break
            for dep, ret in pairs:
                if cancel_check():
                    break
                n += 1
                job.done = n - 1
                job.message = f"DUB → {dest} · {dep.isoformat()} → {ret.isoformat()}"
                url = turkey.build_url(dep, ret, dest, currency=currency)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as exc:
                    job.results.append(
                        {
                            "dest": dest,
                            "dep": dep.isoformat(),
                            "ret": ret.isoformat(),
                            "min_price": None,
                            "error": str(exc),
                        }
                    )
                    job.done = n
                    continue
                page.wait_for_timeout(2500)
                data = turkey.parse_results(page)
                data.update(
                    dest=dest,
                    dep=dep.isoformat(),
                    ret=ret.isoformat(),
                    dep_wd=dep.strftime("%a"),
                    ret_wd=ret.strftime("%a"),
                    trip_days=(ret - dep).days,
                    url=url,
                    origin="DUB",
                )
                job.results.append(data)
                job.done = n

        browser.close()


def run_egyptair_scan(
    job: ScanJob,
    cancel_check: Callable[[], bool],
    *,
    start: str | None = None,
    window_days: int = 60,
    min_trip_days: int = 3,
    max_trip_days: int = 14,
    weekdays: str = "Sat,Sun,Tue,Thu",
    currency: str = "EUR",
) -> None:
    start_date = dt.date.fromisoformat(start or dt.date.today().isoformat())
    end = start_date + dt.timedelta(days=window_days)
    wd = egyptair._parse_weekdays(weekdays)
    deps = list(egyptair._valid_days(start_date + dt.timedelta(days=1), end, wd))
    pairs = [
        (dep, ret)
        for dep in deps
        for ret in egyptair._valid_days(
            dep + dt.timedelta(days=min_trip_days),
            dep + dt.timedelta(days=max_trip_days),
            wd,
        )
    ]
    job.total = len(pairs)

    prepare_playwright_browsers()
    with sync_playwright() as p:
        browser = _launch_firefox(p, egyptair.FIREFOX_PREFS)
        ctx = browser.new_context(viewport={"width": 1280, "height": 1800}, locale="en-IE")
        page = ctx.new_page()

        page.goto(
            f"https://www.google.com/travel/flights?hl=en&curr={currency}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(2500)
        egyptair._reject_consent(page)

        for i, (dep, ret) in enumerate(pairs, start=1):
            if cancel_check():
                break
            job.done = i - 1
            job.message = f"DUB → CAI · {dep.isoformat()} → {ret.isoformat()}"
            url = egyptair.build_url(dep, ret, currency=currency)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as exc:
                job.results.append(
                    {
                        "dep": dep.isoformat(),
                        "ret": ret.isoformat(),
                        "min_price": None,
                        "error": str(exc),
                    }
                )
                job.done = i
                continue
            page.wait_for_timeout(2500)
            data = egyptair.parse_results(page)
            data.update(
                dest="CAI",
                origin="DUB",
                dep=dep.isoformat(),
                ret=ret.isoformat(),
                dep_wd=dep.strftime("%a"),
                ret_wd=ret.strftime("%a"),
                trip_days=(ret - dep).days,
                url=url,
            )
            job.results.append(data)
            job.done = i

        browser.close()


def run_ams_scan(
    job: ScanJob,
    cancel_check: Callable[[], bool],
    *,
    start: str | None = None,
    window_days: int = 60,
    min_trip_days: int = 3,
    max_trip_days: int = 14,
    currency: str = "EUR",
) -> None:
    start_date = dt.date.fromisoformat(start or dt.date.today().isoformat())
    end = start_date + dt.timedelta(days=window_days)
    wd = ams._parse_weekdays(AMS_DEFAULT_WEEKDAYS)
    deps = list(ams._valid_days(start_date + dt.timedelta(days=1), end, wd))
    pairs = [
        (dep, ret)
        for dep in deps
        for ret in ams._valid_days(
            dep + dt.timedelta(days=min_trip_days),
            dep + dt.timedelta(days=max_trip_days),
            wd,
        )
    ]
    job.total = len(pairs)

    prepare_playwright_browsers()
    with sync_playwright() as p:
        browser = _launch_firefox(p, ams.FIREFOX_PREFS)
        ctx = browser.new_context(viewport={"width": 1280, "height": 1800}, locale="en-IE")
        page = ctx.new_page()

        page.goto(
            f"https://www.google.com/travel/flights?hl=en&curr={currency}",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(2500)
        ams._reject_consent(page)

        for i, (dep, ret) in enumerate(pairs, start=1):
            if cancel_check():
                break
            job.done = i - 1
            job.message = f"DUB → AMS · {dep.isoformat()} → {ret.isoformat()}"
            url = ams.build_url(dep, ret, currency=currency)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as exc:
                job.results.append(
                    {
                        "dep": dep.isoformat(),
                        "ret": ret.isoformat(),
                        "min_price": None,
                        "error": str(exc),
                    }
                )
                job.done = i
                continue
            page.wait_for_timeout(2500)
            data = ams.parse_results(page)
            data.update(
                dest="AMS",
                origin="DUB",
                dep=dep.isoformat(),
                ret=ret.isoformat(),
                dep_wd=dep.strftime("%a"),
                ret_wd=ret.strftime("%a"),
                trip_days=(ret - dep).days,
                url=url,
            )
            job.results.append(data)
            job.done = i

        browser.close()


def run_job(job: ScanJob, cancel_check: Callable[[], bool]) -> None:
    cfg = job.config
    if job.scanner == "turkey":
        run_turkey_scan(
            job,
            cancel_check,
            destinations=cfg.get("destinations", "IST,SAW,AYT"),
            currency=cfg.get("currency", "EUR"),
        )
    elif job.scanner == "egyptair":
        run_egyptair_scan(
            job,
            cancel_check,
            start=cfg.get("start"),
            window_days=int(cfg.get("window_days", 60)),
            min_trip_days=int(cfg.get("min_trip_days", 3)),
            max_trip_days=int(cfg.get("max_trip_days", 14)),
            weekdays=cfg.get("weekdays", "Sat,Sun,Tue,Thu"),
            currency=cfg.get("currency", "EUR"),
        )
    elif job.scanner == "ams":
        run_ams_scan(
            job,
            cancel_check,
            start=cfg.get("start"),
            window_days=int(cfg.get("window_days", 60)),
            min_trip_days=int(cfg.get("min_trip_days", 3)),
            max_trip_days=int(cfg.get("max_trip_days", 14)),
            currency=cfg.get("currency", "EUR"),
        )
    else:
        raise ValueError(f"Unknown scanner: {job.scanner}")
