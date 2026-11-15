"""A layer counts every element stored in it, whatever its type, and every screen reads that one count.

Two real organisations each hold an element filed under a layer its type does not belong to (a goal filed under
Business, a node filed under Motivation). The tabs and the Composer read count_layers, the model map reads it
too and draws the rows layer_model_query returns; for each organisation the three give one figure per layer.
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
    mine, theirs = make_org("storedmine"), make_org("storedtheirs")
    with tenant_ctx(mine.id):
        _add(mine.id, "Goal", "motivation", 3, "Goal")
        _add(mine.id, "Node", "motivation", 1, "Stray node")  # a node filed under Motivation
        _add(mine.id, "Goal", "business", 2, "Stray goal")  # a goal filed under Business
        _add(mine.id, "BusinessProcess", "business", 4, "Process")
        _add(mine.id, "Principle", None, 1, "Unfiled principle")  # no layer stored: placed by its type
        db_session.flush()
    with tenant_ctx(theirs.id):
        _add(theirs.id, "Node", "motivation", 5, "Other node")
        _add(theirs.id, "BusinessProcess", "business", 7, "Other process")
        db_session.flush()
    return mine, theirs


def test_each_layer_counts_what_is_stored_in_it_for_each_organisation(db_session, two_orgs, tenant_ctx):
    from app.services.archimate_viewpoint_service import count_layers

    for org, expected in zip(two_orgs, ({"motivation": 5, "business": 6}, {"motivation": 5, "business": 7})):
        with tenant_ctx(org.id):
            model = {row["layer"]: row["model"] for row in count_layers(org.id)}
        assert {k: model[k] for k in expected} == expected
        assert model["technology"] == 0  # the node filed under Motivation is not also Technology's


def test_the_tab_the_card_the_list_and_the_map_give_one_figure_per_layer(db_session, two_orgs, tenant_ctx):
    from app.services.archimate_viewpoint_service import COMPOSITE_LAYER, count_layers, layer_model_query
    from app.services.model_map import build_model_map, layer_counts

    for org in two_orgs:
        with tenant_ctx(org.id):
            counted = {row["layer"]: row["model"] for row in count_layers(org.id)}
            mapped = {c["layer"]: c["count"] for c in layer_counts()}
            for layer, number in counted.items():
                key = "other" if layer == COMPOSITE_LAYER else layer
                assert mapped[key] == number, layer
                assert layer_model_query(layer, org.id).count() == number, layer
                drawn = build_model_map(key)
                assert len(drawn["nodes"]) == number and {n["layer"] for n in drawn["nodes"]} <= {key}, layer
