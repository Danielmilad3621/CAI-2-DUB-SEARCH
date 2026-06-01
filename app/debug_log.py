from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_LOG = Path(__file__).resolve().parent.parent / ".cursor" / "debug-a4bf5a.log"
_SESSION = "a4bf5a"


def debug_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    *,
    run_id: str = "pre-fix",
) -> None:
    # #region agent log
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "sessionId": _SESSION,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    with _LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    # #endregion
