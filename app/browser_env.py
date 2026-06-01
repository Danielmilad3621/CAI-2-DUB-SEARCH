from __future__ import annotations

import os
from pathlib import Path

from app.debug_log import debug_log

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

    # #region agent log
    debug_log(
        "A",
        "browser_env.py:prepare",
        "env before",
        {
            "PLAYWRIGHT_BROWSERS_PATH": before,
            "user_cache": str(user_cache) if user_cache else None,
            "sandbox_in_path": any(m in (before or "") for m in _SANDBOX_MARKERS),
            "resolved_exe": resolve_firefox_executable(),
        },
        run_id="post-fix",
    )
    # #endregion

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

    after = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")

    # #region agent log
    debug_log(
        "B",
        "browser_env.py:prepare",
        "env after",
        {"status": status, "PLAYWRIGHT_BROWSERS_PATH": after},
        run_id="post-fix",
    )
    # #endregion

    return status
