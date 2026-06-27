"""Capture live Google Flights aria-snapshots + PRE-REFACTOR parser outputs.

Run from the repo root, against the untouched legacy scripts, BEFORE any
engine refactor lands (network required):

    .venv/bin/python tests/capture_fixtures.py

For each of the three routes this script:
  1. builds one search URL with the legacy build_url,
  2. loads it in headless Firefox (same prefs/waits as the real scan loop),
  3. saves the page's raw aria-snapshot to tests/fixtures/<route>_snapshot.txt,
  4. runs the legacy parse_results on that saved snapshot text and records the
     output in tests/fixtures/golden_parses.json.

tests/test_parser_parity.py later replays the merged engine parser over the
same snapshot files and asserts deep equality with the recorded outputs, so
parser parity is verified offline and repeatably.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

import cheapest_dub_ams as ams  # noqa: E402
import cheapest_dub_cai_egyptair as egyptair  # noqa: E402
import cheapest_dub_turkey as turkey  # noqa: E402
from app.browser_env import prepare_playwright_browsers, resolve_firefox_executable  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"

# One capture per route: (fixture name, URL, parse callable on a page-like object).
CAPTURES = [
    {
        "route": "egyptair",
        "url": egyptair.build_url(dt.date(2026, 6, 13), dt.date(2026, 6, 20)),
        "parse": lambda page: egyptair.parse_results(page),
        "parser": "cheapest_dub_cai_egyptair.parse_results (airline_name='EgyptAir')",
    },
    {
        "route": "turkey",
        "url": turkey.build_url(dt.date(2026, 7, 31), dt.date(2026, 8, 3), "IST"),
        "parse": lambda page: turkey.parse_results(page),
        "parser": "cheapest_dub_turkey.parse_results (any airline)",
    },
    {
        "route": "ams",
        "url": ams.build_url(dt.date(2026, 6, 20), dt.date(2026, 6, 27)),
        "parse": lambda page: ams.parse_results(page),
        "parser": "cheapest_dub_ams.parse_results (any airline)",
    },
]


class SnapshotPage:
    """Minimal stand-in for a Playwright page: replays a saved aria-snapshot.

    The legacy parsers only ever call page.locator("body").aria_snapshot(),
    so this is sufficient to run them on captured text.
    """

    def __init__(self, text: str) -> None:
        self._text = text

    def locator(self, selector: str) -> SnapshotPage:
        assert selector == "body", f"unexpected locator: {selector}"
        return self

    def aria_snapshot(self) -> str:
        return self._text


def main() -> int:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    prepare_playwright_browsers()

    golden: dict = {
        "generated_from_commit": sha,
        "captured_at": dt.datetime.now().isoformat(timespec="seconds"),
        "note": "Legacy parser outputs on the saved snapshots. Do not regenerate after the engine refactor.",
        "routes": {},
    }

    with sync_playwright() as p:
        launch_kwargs: dict = {"headless": True, "firefox_user_prefs": egyptair.FIREFOX_PREFS}
        exe = resolve_firefox_executable()
        if exe:
            launch_kwargs["executable_path"] = exe
        browser = p.firefox.launch(**launch_kwargs)
        ctx = browser.new_context(viewport={"width": 1280, "height": 1800}, locale="en-IE")
        page = ctx.new_page()

        page.goto("https://www.google.com/travel/flights?hl=en&curr=EUR",
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        egyptair._reject_consent(page)

        for cap in CAPTURES:
            route = cap["route"]
            print(f"[{route}] {cap['url'][:100]}…")
            page.goto(cap["url"], wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            snap = page.locator("body").aria_snapshot()
            parsed = cap["parse"](SnapshotPage(snap))
            # Results sometimes lazy-load; mirror a patient retry before giving up.
            attempts = 1
            while parsed["min_price"] is None and attempts < 4:
                page.wait_for_timeout(2500)
                snap = page.locator("body").aria_snapshot()
                parsed = cap["parse"](SnapshotPage(snap))
                attempts += 1
            fixture_path = FIXTURES / f"{route}_snapshot.txt"
            fixture_path.write_text(snap)
            golden["routes"][route] = {
                "fixture": fixture_path.name,
                "url": cap["url"],
                "parser": cap["parser"],
                "attempts": attempts,
                "parsed": parsed,
            }
            print(f"  snapshot {len(snap) // 1024} KB, min_price={parsed['min_price']}, "
                  f"entries={len(parsed['entries'])}, attempts={attempts}")

        browser.close()

    out = FIXTURES / "golden_parses.json"
    out.write_text(json.dumps(golden, indent=1))
    failures = [r for r, d in golden["routes"].items() if d["parsed"]["min_price"] is None]
    if failures:
        print(f"FAIL: no priced results parsed for: {failures}")
        return 1
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
