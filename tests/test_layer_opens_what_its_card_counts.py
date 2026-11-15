"""The layer a card opens holds exactly the elements the card counts, and a count that fails is not shown as a total.

An organisation holds elements filed under a layer their type does not belong to (a goal filed under
Business, a node filed under Motivation) and a gap held only in the register. The card counts each layer by
its stored layer; opening the layer, from any viewpoint that takes every type, lists the same elements.
"""
import uuid

import pytest


def _add(org_id, element_type, layer, count, tag):
    from app.services.archimate_backbone import create_backbone_element

    for n in range(count):
        create_backbone_element(element_type=element_type, layer=layer,
                                name="%s %d %s" % (tag, n, uuid.uuid4().hex[:6]), organization_id=org_id)


@pytest.fixture
def two_orgs(db_session, make_org, tenant_ctx):
    from app.services.gap_register_service import create_gap

    mine, theirs = make_org("opensmine"), make_org("openstheirs")
    with tenant_ctx(mine.id):
        _add(mine.id, "Goal", "motivation", 3, "Goal")
        _add(mine.id, "Node", "motivation", 1, "Stray node")
        _add(mine.id, "Goal", "business", 2, "Stray goal")
        _add(mine.id, "BusinessProcess", "business", 4, "Process")
        _add(mine.id, "Principle", None, 1, "Unfiled principle")
        _add(mine.id, "ApplicationComponent", "application", 2, "App")
        create_gap(mine.id, "Register only gap")
        db_session.flush()
    with tenant_ctx(theirs.id):
        _add(theirs.id, "Node", "motivation", 5, "Other node")
        _add(theirs.id, "BusinessProcess", "business", 7, "Other process")
        db_session.flush()
    return mine, theirs


def test_each_layer_opens_the_elements_its_card_counts(db_session, two_orgs, tenant_ctx):
    from app.services.archimate_viewpoint_service import COMPOSITE_LAYER, count_layers, get_viewpoint_data

    for org in two_orgs:
        with tenant_ctx(org.id):
            for row in count_layers(org.id):
                if row["layer"] == COMPOSITE_LAYER:
                    continue
                opened = get_viewpoint_data("layered", layer=row["layer"])
                assert opened["total"] == row["model"], (org.id, row["layer"])


def test_a_viewpoint_that_narrows_types_does_not_count_register_records_outside_them(db_session, two_orgs, tenant_ctx):
    from app.services.archimate_viewpoint_service import count_layers

    mine, _ = two_orgs
    with tenant_ctx(mine.id):
        whole = next(r for r in count_layers(mine.id) if r["layer"] == "implementation")
        narrowed = next(r for r in count_layers(mine.id, ["Goal"]) if r["layer"] == "implementation")
    assert whole["register_only"] == 1
    assert narrowed["register_only"] == 0


def test_a_failed_register_count_leaves_the_layer_total_unknown(db_session, two_orgs, tenant_ctx, monkeypatch):
    from app.services import archimate_viewpoint_service as counting, model_map
    _register_only_count = counting._register_only_count

    mine, _ = two_orgs

    def failing(layer, org_id=None, allowed_types=None):
        if layer == "business":
            raise RuntimeError("table unreadable")
        return _register_only_count(layer, org_id, allowed_types)

    monkeypatch.setattr(counting, "_register_only_count", failing)
    with tenant_ctx(mine.id):
        rows = {c["layer"]: c for c in model_map.layer_counts()}
    assert rows["business"]["count"] == 6
    assert rows["business"]["total"] is None and rows["business"]["not_drawn"] is None
    assert rows["motivation"]["total"] == 5
