from __future__ import annotations

import os
from pathlib import Path

_SANDBOX_MARKERS = ("cursor-sandbox-cache", "/T/cursor-sandbox-cache/")


def _user_playwright_cache() -> Path | None:
    home = Path.home()
    for candidate in (
        home / "Library" / "Caches" / "ms-playwright",
        home / ".cache" / "ms-playwright",
    ):
        if (candidate / "firefox-1522").is_dir() or list(candidate.glob("firefox-*")):
            return candidate
    return None


def resolve_firefox_executable() -> str | None:
    """Return a working Firefox binary path, preferring the user cache over sandbox copies."""
    user_cache = _user_playwright_cache()
    if user_cache:
        for firefox_dir in sorted(user_cache.glob("firefox-*"), reverse=True):
            exe = firefox_dir / "firefox" / "Nightly.app" / "Contents" / "MacOS" / "firefox"
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
