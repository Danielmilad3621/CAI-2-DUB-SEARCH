"""One-off closeout: row-ENVELOPE parity between engine.run_scan and the legacy
run_*_scan functions (commit 1848d25).

URL and parser-dict parity were locked in Phase 1; this verifies the wrapper
the scan loop builds AROUND the parser output — the success-row envelope
(dest/origin/dep/ret/dep_wd/ret_wd/trip_days/url via data.update) and the
error-row dict built when page.goto fails.

Method: extract the actual source fragments from both versions via AST
(no hand transcription), exec/eval them with identical bindings (golden parse
fixtures, fixed dates/url/exception), and compare the resulting dicts for
content equality (==) AND JSON key order (list(dict.keys())).

Run:  .venv/bin/python tests/closeout_envelope_diff.py
"""

from __future__ import annotations

import ast
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.route_defs import BUILTIN_ROUTES  # noqa: E402

LEGACY_COMMIT = "1848d25"
FIXTURES = ROOT / "tests" / "fixtures"
GOLDEN_PARSES = json.loads((FIXTURES / "golden_parses.json").read_text())

# Identical bindings for both versions.
DEP, RET = dt.date(2026, 8, 7), dt.date(2026, 8, 10)
URL = "https://example.invalid/search?tfs=TEST"
EXC = RuntimeError("Timeout 45000ms exceeded.")

CASES = {
    "turkey": {"dest": "IST", "legacy_fn": "run_turkey_scan"},
    "egyptair": {"dest": "CAI", "legacy_fn": "run_egyptair_scan"},
    "ams": {"dest": "AMS", "legacy_fn": "run_ams_scan"},
}


def _fn(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise LookupError(name)


def _error_and_update_segments(src: str, fn: ast.FunctionDef) -> tuple[str, list[str]]:
    """Return (error-dict/-building source, [statements building the success row]).

    For the legacy functions the error row is a dict literal inside
    job.results.append({...}); the success row is one data.update(...) call.
    For engine.run_scan the error row is built by consecutive statements
    (row = {...}; if multi-dest: row = {"dest": dest, **row}) before the
    append; the success row is the same single data.update(...) call.
    """
    error_stmts: list[str] = []
    update_call = None
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler):
            stmts = []
            for stmt in node.body:
                txt = ast.get_source_segment(src, stmt)
                if "job.results.append" in txt:
                    call = stmt.value  # type: ignore[attr-defined]
                    arg = ast.get_source_segment(src, call.args[0])
                    if arg.lstrip().startswith("{"):  # legacy: dict literal inline
                        stmts.append(f"row = {arg}")
                    break
                if txt.startswith(("row", "if ")):  # engine: row-building stmts
                    stmts.append(txt)
            error_stmts = stmts
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "update" and getattr(node.func.value, "id", "") == "data":
                update_call = ast.get_source_segment(src, node)
    assert error_stmts and update_call, "extraction failed"
    return update_call, error_stmts


def _run(update_src: str, error_stmts: list[str], env: dict) -> tuple[dict, dict]:
    env = dict(env)
    env["data"] = dict(env["data"])  # fresh copy per run
    exec(compile("\n".join(error_stmts), "<error-row>", "exec"), env)  # noqa: S102
    exec(compile(update_src, "<success-row>", "exec"), env)  # noqa: S102
    return env["data"], env["row"]


def main() -> int:
    old_src = subprocess.run(
        ["git", "show", f"{LEGACY_COMMIT}:app/scanner.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    new_src = (ROOT / "app" / "engine.py").read_text()
    old_tree, new_tree = ast.parse(old_src), ast.parse(new_src)
    new_update, new_error = _error_and_update_segments(new_src, _fn(new_tree, "run_scan"))

    clean = True
    for route_id, case in CASES.items():
        route = BUILTIN_ROUTES[route_id]
        env = {
            "dep": DEP, "ret": RET, "dest": case["dest"], "url": URL, "exc": EXC,
            "route": route, "str": str, "len": len,
            "data": GOLDEN_PARSES["routes"][route_id]["parsed"],
        }
        old_update, old_error = _error_and_update_segments(
            old_src, _fn(old_tree, case["legacy_fn"])
        )
        old_success, old_err_row = _run(old_update, old_error, env)
        new_success, new_err_row = _run(new_update, new_error, env)

        print(f"[{route_id}]")
        for label, old, new in (
            ("success row", old_success, new_success),
            ("error row  ", old_err_row, new_err_row),
        ):
            content = "EQUAL" if old == new else "DIFFERS"
            order = "SAME" if list(old) == list(new) else "DIFFERS"
            print(f"  {label}: content {content:7s} | key order {order}")
            if old != new:
                clean = False
                only_old = {k: v for k, v in old.items() if new.get(k) != v}
                only_new = {k: v for k, v in new.items() if old.get(k) != v}
                print(f"    old-only/changed: {only_old}\n    new-only/changed: {only_new}")
            elif list(old) != list(new):
                print(f"    old order: {list(old)}\n    new order: {list(new)}")
    print("\nVERDICT:", "content-clean" if clean else "CONTENT DIVERGENCE — STOP")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
