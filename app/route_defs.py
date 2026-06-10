"""Compat shim: built-in routes now live in app/routes.json.

Phase 1 defined the three routes here as Route literals; Phase 2 moved them
into the JSON registry (app/registry.py). The CLI shims and tests keep
importing BUILTIN_ROUTES / TURKEY_AUGUST_2026_PAIRS from this module, so the
names survive as registry-backed snapshots (built-ins never change at
runtime).
"""

from __future__ import annotations

from app.engine import Route
from app.registry import registry

BUILTIN_ROUTES: dict[str, Route] = {
    entry.route.id: entry.route for entry in registry.list_entries() if entry.builtin
}

TURKEY_AUGUST_2026_PAIRS: tuple[tuple[str, str], ...] = BUILTIN_ROUTES["turkey"].fixed_pairs
