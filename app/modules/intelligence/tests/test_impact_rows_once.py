"""An element reached by a recorded link and by the model's own working-out is one row."""

from __future__ import annotations

from app.modules.intelligence.services.query_service import _merge_rows_by_element


def _row(element_id, kind, **extra):
    return {"element_id": element_id, "relation": {"kind": kind, "depth": 1, **extra}}


def test_one_element_reached_two_ways_is_one_row_with_both_sources_recorded_first():
    rows = [_row(5, "derived", derived_id=9), _row(5, "explicit"), _row(6, "explicit")]
    merged = _merge_rows_by_element(rows)
    assert [r["element_id"] for r in merged] == [5, 6]
    assert [rel["kind"] for rel in merged[0]["relations"]] == ["explicit", "derived"]
    assert merged[0]["relation"]["kind"] == "explicit"
    assert [rel["kind"] for rel in merged[1]["relations"]] == ["explicit"]


def test_two_different_elements_stay_two_rows():
    merged = _merge_rows_by_element([_row(1, "explicit"), _row(2, "derived")])
    assert [r["element_id"] for r in merged] == [1, 2]
    assert all(len(r["relations"]) == 1 for r in merged)
