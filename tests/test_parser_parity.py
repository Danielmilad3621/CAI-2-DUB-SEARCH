"""Parser parity: merged engine parser vs the pre-refactor parsers.

Replays the live aria-snapshots captured in tests/fixtures/ (see
tests/capture_fixtures.py) through engine.parse_results and asserts deep
equality with the legacy parsers' recorded outputs in golden_parses.json —
covering both output shapes: airline-filtered (EgyptAir: cheapest_tab +
snippet, uncapped entries) and any-airline (Turkey/AMS: airline/stops/
snippet_head, entries capped at 5).

Run standalone (no pytest needed):  .venv/bin/python tests/test_parser_parity.py
Or via pytest:                      pytest tests/test_parser_parity.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import engine  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
GOLDEN = json.loads((FIXTURES / "golden_parses.json").read_text())

# Route id -> airline_name the legacy parser used (None = any-airline variant).
PARSER_MODES = {"egyptair": "EgyptAir", "turkey": None, "ams": None}


class SnapshotPage:
    """Replays a saved aria-snapshot; the parsers only call locator('body').aria_snapshot()."""

    def __init__(self, text: str) -> None:
        self._text = text

    def locator(self, selector: str) -> "SnapshotPage":
        assert selector == "body", f"unexpected locator: {selector}"
        return self

    def aria_snapshot(self) -> str:
        return self._text


def test_parser_parity_all_routes() -> None:
    assert set(GOLDEN["routes"]) == set(PARSER_MODES)
    for route_id, record in GOLDEN["routes"].items():
        snap = (FIXTURES / record["fixture"]).read_text()
        got = engine.parse_results(SnapshotPage(snap), airline_name=PARSER_MODES[route_id])
        assert got == record["parsed"], (
            f"{route_id}: engine parser output diverges from legacy output\n"
            f"  engine: {json.dumps(got)[:400]}\n"
            f"  legacy: {json.dumps(record['parsed'])[:400]}"
        )


def test_parser_shapes() -> None:
    """Field-shape spot checks so a future edit can't silently merge the two variants."""
    egy = GOLDEN["routes"]["egyptair"]["parsed"]
    assert "cheapest_tab" in egy
    assert egy["entries"] and set(egy["entries"][0]) == {"price", "class", "snippet"}

    for rid in ("turkey", "ams"):
        any_air = GOLDEN["routes"][rid]["parsed"]
        assert "cheapest_tab" not in any_air
        assert len(any_air["entries"]) <= 5
        assert any_air["entries"] and set(any_air["entries"][0]) == {
            "price", "airline", "stops", "class", "snippet_head",
        }


def test_shim_parse_results_signatures() -> None:
    import cheapest_dub_ams as ams
    import cheapest_dub_cai_egyptair as egyptair
    import cheapest_dub_turkey as turkey

    snap_egy = (FIXTURES / GOLDEN["routes"]["egyptair"]["fixture"]).read_text()
    assert egyptair.parse_results(SnapshotPage(snap_egy)) == GOLDEN["routes"]["egyptair"]["parsed"]
    snap_tur = (FIXTURES / GOLDEN["routes"]["turkey"]["fixture"]).read_text()
    assert turkey.parse_results(SnapshotPage(snap_tur)) == GOLDEN["routes"]["turkey"]["parsed"]
    snap_ams = (FIXTURES / GOLDEN["routes"]["ams"]["fixture"]).read_text()
    assert ams.parse_results(SnapshotPage(snap_ams)) == GOLDEN["routes"]["ams"]["parsed"]


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failures else 0)
