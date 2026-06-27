from __future__ import annotations

import json
import os
from pathlib import Path

_SANDBOX_MARKERS = ("cursor-sandbox-cache", "/T/cursor-sandbox-cache/")

_FIREFOX_EXE_SUFFIX = ("firefox", "Nightly.app", "Contents", "MacOS", "firefox")


def _user_playwright_cache() -> Path | None:
    home = Path.home()
    for candidate in (
        home / "Library" / "Caches" / "ms-playwright",
        home / ".cache" / "ms-playwright",
    ):
        if list(candidate.glob("firefox-*")):
            return candidate
    return None


def expected_firefox_revision() -> str | None:
    """Read the Firefox build revision the *currently installed* Playwright is pinned to.

    Launching a Firefox build that does not match the running Playwright's bundled
    juggler protocol crashes the browser process with SIGABRT on macOS, so we must
    select the matching `firefox-<revision>` build rather than the newest on disk.
    """
    try:
        import playwright  # imported lazily so this module stays import-cheap
    except Exception:
        return None
    browsers_json = (
        Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
    )
    try:
        data = json.loads(browsers_json.read_text())
    except Exception:
        return None
    for entry in data.get("browsers", []):
        if entry.get("name") == "firefox" and entry.get("revision"):
            return str(entry["revision"])
    return None


def resolve_firefox_executable() -> str | None:
    """Return a Firefox binary that matches the installed Playwright build.

    Preference order:
      1. The `firefox-<revision>` build that the running Playwright expects.
      2. The newest build present (best-effort fallback when the exact one is absent).

    Selecting a mismatched build (e.g. the newest on disk) is what produces the
    "Failed to launch the browser process ... signal=SIGABRT" crash on macOS.
    """
    user_cache = _user_playwright_cache()
    if not user_cache:
        return None

    expected = expected_firefox_revision()
    if expected:
        exe = user_cache.joinpath(f"firefox-{expected}", *_FIREFOX_EXE_SUFFIX)
        if exe.is_file():
            return str(exe)

    # Fallback: newest build on disk, sorted numerically so firefox-1509 < firefox-1522.
    def _revision_key(p: Path) -> int:
        suffix = p.name.rsplit("-", 1)[-1]
        return int(suffix) if suffix.isdigit() else -1

    for firefox_dir in sorted(user_cache.glob("firefox-*"), key=_revision_key, reverse=True):
        exe = firefox_dir.joinpath(*_FIREFOX_EXE_SUFFIX)
        if exe.is_file():
            return str(exe)
    return None


def prepare_playwright_browsers() -> str:
    """Ensure Playwright does not use Cursor sandbox browser copies (they SIGABRT on macOS).

    Returns a short status string for logging.
    """
    before = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    user_cache = _user_playwright_cache()

    if before and any(m in before for m in _SANDBOX_MARKERS):
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        if user_cache:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(user_cache)
            status = "redirected_to_user_cache"
        else:
            status = "cleared_sandbox_path_no_user_cache"
    elif before and not Path(before).exists():
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        status = "cleared_missing_path"
    else:
        status = "unchanged"

    return status
