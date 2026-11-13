"""The layer rule cannot be read without naming an organisation.

Two real organisations hold elements in the same layers. The one count of every layer and the one reader of a
layer's elements each name the organisation they are read for, and a call that names none is refused: a count
or a list that silently answers from every organisation's rows is the leak this closes. The model map reads
every layer's kinds in one grouped query, so its per-layer figures cost the same whatever the layer count.
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
    mine, theirs = make_org("namedmine"), make_org("namedtheirs")
    with tenant_ctx(mine.id):
        _add(mine.id, "Goal", "motivation", 3, "My goal")
        _add(mine.id, "BusinessProcess", "business", 2, "My process")
        db_session.flush()
    with tenant_ctx(theirs.id):
        _add(theirs.id, "Goal", "motivation", 4, "Their goal")
        _add(theirs.id, "BusinessProcess", "business", 5, "Their process")
        db_session.flush()
    return mine, theirs


def test_the_reader_of_a_layers_elements_refuses_a_call_that_names_no_organisation(db_session):
    from app.services.archimate_viewpoint_service import layer_model_query

    with pytest.raises(TypeError):
        layer_model_query("business")
    with pytest.raises(ValueError):
        layer_model_query("business", None)


def test_the_count_of_every_layer_refuses_a_call_that_names_no_organisation(db_session):
    from app.services.archimate_viewpoint_service import count_layers

    with pytest.raises(TypeError):
        count_layers()
    with pytest.raises(ValueError):
        count_layers(None)


def test_the_rule_places_one_element_by_its_own_stored_layer_and_type():
    """element_layer reads no organisation: it places one element, so it cannot answer for one
    organisation's rows and not another's. Both of its inputs are required."""
    from app.services.archimate_viewpoint_service import COMPOSITE_LAYER, element_layer

    with pytest.raises(TypeError):
        element_layer()
    assert element_layer("motivation", "Goal") == "motivation"
    assert element_layer("business", "Goal") == "business"  # a goal filed under Business is Business's
    assert element_layer(None, "Goal") == "motivation"  # no layer stored: placed by its type
    assert element_layer(None, "NoLayerHoldsThisType") == COMPOSITE_LAYER


def test_each_organisation_is_read_by_its_own_id_and_no_call_reads_both(db_session, two_orgs, tenant_ctx):
    from app.services.archimate_viewpoint_service import count_layers, layer_model_query

    for org, (goals, processes) in zip(two_orgs, ((3, 2), (4, 5))):
        with tenant_ctx(org.id):
            assert layer_model_query("motivation", org.id).count() == goals
            assert layer_model_query("business", org.id).count() == processes
            counted = {row["layer"]: row["model"] for row in count_layers(org.id)}
            assert (counted["motivation"], counted["business"]) == (goals, processes)
            # The leak this closes: naming no organisation is refused, not answered from every
            # organisation's rows at once.
            with pytest.raises(ValueError):
                layer_model_query("motivation", None)
            with pytest.raises(ValueError):
                count_layers(None)


def test_the_other_layer_reads_the_composite_row_not_itself(db_session, make_org, tenant_ctx):
    """The model map's "other" layer is count_layers' "composite" row: elements stored in no layer
    and of a type no layer holds. Looking the map's "other" key up under itself finds nothing, so it
    must be read under "composite" (_counting_layer). With a plain lookup under "other" the layer
    reads zero although it holds elements."""
    from app.services.archimate_backbone import create_backbone_element
    from app.services.model_map import layer_counts

    mine = make_org("namedother")
    with tenant_ctx(mine.id):
        create_backbone_element(element_type="Location", layer=None, name="A place", organization_id=mine.id)
        create_backbone_element(element_type="Grouping", layer=None, name="A group", organization_id=mine.id)
        create_backbone_element(element_type="Goal", layer="motivation", name="A goal", organization_id=mine.id)
        db_session.flush()

    counts = {c["layer"]: c for c in layer_counts()}
    assert counts["other"]["count"] == 2
    assert counts["motivation"]["count"] == 1


def test_the_model_map_reads_every_layers_kinds_in_one_grouped_query(db_session, two_orgs, tenant_ctx):
    """One grouped read carries every layer's kinds, so the map's per-layer figures cost the same
    whatever the number of layers, instead of one read per layer."""
    from app import db
    from app.services.archimate_viewpoint_service import COMPOSITE_LAYER, count_layers, layer_model_query
    from app.services.model_map import layer_counts

    statements = []
    engine = db_session.get_bind()

    def _record(conn, cursor, statement, parameters, context, executemany):
        if "archimate_elements" in statement:
            statements.append(statement)

    from sqlalchemy import event

    event.listen(engine, "before_cursor_execute", _record)
    try:
        for org in two_orgs:
            with tenant_ctx(org.id):
                statements.clear()
                counts = {c["layer"]: c for c in layer_counts()}
                reads = len(statements)
                # The one grouped count of every layer, and the one grouped read of every layer's kinds.
                assert reads <= 2, statements
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    # The grouped read answers what one read per layer answered: each layer's kinds and its count.
    mine, _ = two_orgs
    with tenant_ctx(mine.id):
        from app.models.archimate_core import ArchiMateElement

        counts = {c["layer"]: c for c in layer_counts()}
        counted = {row["layer"]: row["model"] for row in count_layers(mine.id)}
        type_column = db.func.coalesce(ArchiMateElement.type, "Unspecified")
        for layer, number in counted.items():
            key = "other" if layer == COMPOSITE_LAYER else layer
            assert counts[key]["count"] == number, layer
            per_layer = dict(layer_model_query(layer, mine.id).with_entities(
                type_column, db.func.count()).group_by(type_column).all())
            assert {k["type"]: k["count"] for k in counts[key]["kinds"]} == per_layer, layer
