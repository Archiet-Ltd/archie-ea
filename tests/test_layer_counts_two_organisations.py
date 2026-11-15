"""Each organisation's layer counts include only its own elements and its own register records.

Two real organisations hold different numbers of Implementation elements and gaps. The count, the rows a layer
lists and the records held outside the model are each read for one organisation by its own id, whichever
organisation the request happens to be signed in to.
"""
import uuid

import pytest

from tests.test_implementation_layer_one_count import seed_implementation_layer


@pytest.fixture
def two_orgs(db_session, make_org, tenant_ctx):
    from app.services.archimate_backbone import create_backbone_element
    from app.services.gap_register_service import create_gap

    mine, theirs = make_org("countmine"), make_org("counttheirs")
    with tenant_ctx(mine.id):
        seed_implementation_layer(mine.id, "mine")  # 20 elements and 1 gap
        db_session.flush()
    with tenant_ctx(theirs.id):
        seed_implementation_layer(theirs.id, "theirs")
        for n in range(5):
            create_backbone_element(element_type="WorkPackage", layer="implementation",
                                    name="Extra work %d %s" % (n, uuid.uuid4().hex[:6]), organization_id=theirs.id)
        create_gap(theirs.id, "Second gap")
        create_gap(theirs.id, "Third gap")
        db_session.flush()
    return mine, theirs


def _implementation(rows):
    return next(row for row in rows if row["layer"] == "implementation")


def test_count_layers_counts_only_the_organisation_asked_for(db_session, two_orgs, tenant_ctx):
    from app.services.archimate_viewpoint_service import count_layers

    mine, theirs = two_orgs
    with tenant_ctx(mine.id):
        row = _implementation(count_layers(mine.id))
    assert (row["model"], row["register_only"], row["count"]) == (20, 1, 21)
    with tenant_ctx(theirs.id):
        row = _implementation(count_layers(theirs.id))
    assert (row["model"], row["register_only"], row["count"]) == (25, 3, 28)


def test_the_rows_a_layer_lists_belong_to_the_organisation_named(db_session, two_orgs, tenant_ctx):
    from app.services.archimate_viewpoint_service import layer_model_query

    mine, theirs = two_orgs
    with tenant_ctx(mine.id):
        own = layer_model_query("implementation", mine.id).all()
    with tenant_ctx(theirs.id):
        other = layer_model_query("implementation", theirs.id).all()
    assert len(own) == 20 and {e.organization_id for e in own} == {mine.id}
    assert len(other) == 25 and {e.organization_id for e in other} == {theirs.id}
    assert not {e.id for e in own} & {e.id for e in other}


def test_the_records_held_outside_the_model_are_counted_for_each_organisation(db_session, two_orgs, tenant_ctx):
    from app.services.archimate_viewpoint_service import _register_only_count

    mine, theirs = two_orgs
    with tenant_ctx(mine.id):
        assert _register_only_count("implementation", mine.id) == 1
    with tenant_ctx(theirs.id):
        assert _register_only_count("implementation", theirs.id) == 3


def test_the_model_map_counts_each_organisation_by_count_layers(db_session, two_orgs, tenant_ctx):
    from app.services.archimate_viewpoint_service import count_layers
    from app.services.model_map import layer_counts

    for org, expected in zip(two_orgs, (20, 25)):
        with tenant_ctx(org.id):
            mapped = {c["layer"]: c["count"] for c in layer_counts()}
            counted = {r["layer"]: r["model"] for r in count_layers(org.id)}
        assert mapped["implementation"] == counted["implementation"] == expected
        assert mapped["other"] == counted["composite"]
