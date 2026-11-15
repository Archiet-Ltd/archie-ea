"""An element reached by a recorded link and by worked-out reasoning keeps both
sources through the public impact projection and the set impact walk."""

from __future__ import annotations

from app.api.v1.impact import _derived_elements_for_element
from app.modules.intelligence.services.query_service import (
    IntelligenceQueryService,
    without_relations,
)

from .test_set_impact import _as, _element

RECORDED = {"kind": "explicit", "type": "Serving", "depth": 1}
WORKED_OUT = {"kind": "derived", "type": "Serving", "depth": 1, "derived_id": 9}


def _merged_walk(element_id):
    return {
        "rows": [
            {
                "element_id": element_id,
                "relation": RECORDED,
                "relations": [RECORDED, WORKED_OUT],
                "owner": None,
                "owners": [],
                "reason": None,
                "health": None,
            }
        ],
        "summary": {"derivation_state": "current"},
        "elements": {},
        "reasons": [],
    }


def test_without_relations_drops_only_the_relations_key():
    rows = without_relations([{"element_id": 1, "relation": RECORDED, "relations": [RECORDED]}])
    assert rows == [{"element_id": 1, "relation": RECORDED}]


def test_derived_elements_lists_an_element_found_by_both_a_recorded_and_a_worked_out_link(app, monkeypatch):
    monkeypatch.setattr(
        IntelligenceQueryService, "cross_layer_impact", staticmethod(lambda *a, **k: _merged_walk(7))
    )
    with app.test_request_context("/"):
        derived, state = _derived_elements_for_element(1, include_derived=True, max_depth=3)
    assert [row["element_id"] for row in derived] == [7]
    assert all("relations" not in row for row in derived)
    assert state == "current"


def test_set_impact_names_the_worked_out_source_of_a_merged_element(app, db_session, make_org, monkeypatch):
    org = make_org()
    seed = _element(db_session, org.id, "Seed")
    db_session.commit()
    monkeypatch.setattr(
        IntelligenceQueryService, "cross_layer_impact", staticmethod(lambda *a, **k: _merged_walk(7))
    )
    answer = _as(app, org.id, lambda: IntelligenceQueryService.set_impact([seed.id]))
    row = next(r for r in answer["rows"] if r["element_id"] == 7)
    assert [rel["kind"] for rel in row["relations"]] == ["explicit", "derived"]
    assert answer["summary"]["derived_count"] == 1
    assert answer["summary"]["explicit_count"] == 1
