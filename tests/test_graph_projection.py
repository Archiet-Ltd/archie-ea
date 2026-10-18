"""Per-organisation graph projection.

The projection (app/modules/intelligence/services/graph_projection.py) is a
rebuildable in-memory copy of one organisation's elements, relationships and
relationship history. These tests hold it to: the organisation boundary (a
foreign row pointing at the caller's element ids never appears), honest empty
states, agreement with the canonical relational walk and with the history
service's as-of answer, "rebuilt equals incrementally maintained", the stored
snapshot, the scheduled job and its registration.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app import db
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.entity_history import EntityHistory
from app.models.graph_projection_snapshot import GraphProjectionSnapshot
from app.models.organization import Organization
from app.modules.intelligence.services import graph_projection as gp
from app.services.event_log_service import relay_outbox_batch


@pytest.fixture(autouse=True)
def _guards_and_cache(app, _schema):
    """The updated outbox trigger must be present, and no projection may carry over between tests."""
    from app.models.transformation_db_guards import ensure_transformation_db_guards

    with app.app_context(), db.engine.begin() as connection:
        ensure_transformation_db_guards(connection)
    gp.clear_cache()
    yield
    gp.clear_cache()


def _org(db_session, label):
    suffix = uuid.uuid4().hex[:10]
    org = Organization(name=f"Projection {label} {suffix}", slug=f"gp-{label.lower()}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _element(db_session, org_id, name, *, type_="ApplicationComponent", layer="Application", parent_id=None):
    row = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id, parent_id=parent_id)
    db_session.add(row)
    db_session.flush()
    return row


def _rel(db_session, org_id, source_id, target_id, type_="Serving", **extra):
    row = ArchiMateRelationship(type=type_, source_id=source_id, target_id=target_id, organization_id=org_id, **extra)
    db_session.add(row)
    db_session.flush()
    return row


def _publish(db_session):
    """Commit pending work and move the outbox rows into the event log."""
    db_session.commit()
    relay_outbox_batch()
    db_session.commit()


def _canonical_depths(org_id, root_id, depth, direction):
    """The relational walk query_service uses, run inside the organisation's own scope."""
    from app.modules.intelligence.services.query_service import _walk_explicit

    return {row["element_id"]: row["relation"]["depth"] for row in _walk_explicit(root_id, depth, direction)}


@pytest.fixture
def graph(db_session):
    """Organisation A with a branching, cyclic graph; organisation B with its own."""
    org_a, org_b = _org(db_session, "A"), _org(db_session, "B")
    a = {name: _element(db_session, org_a.id, name) for name in "abcdefg"}
    rels = [
        _rel(db_session, org_a.id, a["a"].id, a["b"].id),
        _rel(db_session, org_a.id, a["a"].id, a["c"].id),
        _rel(db_session, org_a.id, a["b"].id, a["d"].id),
        _rel(db_session, org_a.id, a["c"].id, a["d"].id),
        _rel(db_session, org_a.id, a["d"].id, a["e"].id),
        _rel(db_session, org_a.id, a["e"].id, a["a"].id),  # cycle back to the root
        _rel(db_session, org_a.id, a["e"].id, a["f"].id),
        _rel(db_session, org_a.id, a["f"].id, a["g"].id),
    ]
    b = {name: _element(db_session, org_b.id, f"B-{name}") for name in "xyz"}
    _rel(db_session, org_b.id, b["x"].id, b["y"].id)
    # Foreign rows that point at the CALLER's (A's) element ids.
    foreign_into_a = _rel(db_session, org_b.id, b["y"].id, a["a"].id, type_="Flow")
    foreign_out_of_a = _rel(db_session, org_b.id, a["g"].id, b["z"].id, type_="Flow")
    db_session.commit()
    return {"A": org_a, "B": org_b, "a": a, "b": b, "rels": rels,
            "foreign_into_a": foreign_into_a, "foreign_out_of_a": foreign_out_of_a}


# --------------------------------------------------------------------------- #
# Organisation boundary
# --------------------------------------------------------------------------- #


def test_projection_of_one_organisation_holds_none_of_anothers_nodes_or_edges(db_session, graph):
    org_a, org_b = graph["A"], graph["B"]
    # The foreign rows are really there, and they name A's element ids.
    assert db_session.scalar(
        select(func.count()).select_from(ArchiMateRelationship).where(
            ArchiMateRelationship.organization_id == org_b.id,
            ArchiMateRelationship.target_id == graph["a"]["a"].id)) == 1

    projection = gp.build_projection(org_a.id)

    own_ids = {e.id for e in graph["a"].values()}
    foreign_ids = {e.id for e in graph["b"].values()}
    assert set(projection._nodes) == own_ids
    assert not (set(projection._nodes) & foreign_ids)
    assert set(projection._edges) == {r.id for r in graph["rels"]}
    assert graph["foreign_into_a"].id not in projection._edges
    assert graph["foreign_out_of_a"].id not in projection._edges

    # A's walk never reaches B's elements, even from the element a foreign row points out of.
    result = projection.traverse(graph["a"]["g"].id, 3, "both")
    assert foreign_ids.isdisjoint(row.element_id for row in result.rows)
    whole = projection.traverse(graph["a"]["a"].id, 10, "both")
    assert foreign_ids.isdisjoint(row.element_id for row in whole.rows)
    assert {row.element_id for row in whole.rows} == own_ids - {graph["a"]["a"].id}


def test_other_organisations_own_walk_ignores_edges_whose_far_end_is_not_its_element(db_session, graph):
    org_b = graph["B"]
    projection = gp.build_projection(org_b.id)
    # B owns the row that points at A's element, so the edge is B's... but A's element is not B's node.
    assert graph["foreign_into_a"].id in projection._edges
    result = projection.traverse(graph["b"]["y"].id, 3, "downstream")
    assert graph["a"]["a"].id not in {row.element_id for row in result.rows}
    assert result.rows == []


def test_asking_for_another_organisations_element_is_not_found(db_session, graph):
    result = gp.query(graph["B"].id, graph["a"]["a"].id, 3)
    assert result.rows == []
    assert result.reason == "element_not_found"


def test_unknown_organisation_reads_nothing_and_writes_nothing(app, db_session, graph, tenant_ctx):
    from app.middleware.tenant_context import current_org_id

    before = db_session.scalar(select(func.count()).select_from(GraphProjectionSnapshot))
    with tenant_ctx(None):
        assert current_org_id() is None
        result = gp.query(current_org_id(), graph["a"]["a"].id, 3)
        assert gp.get_projection(current_org_id()) is None
        assert gp.load_snapshot(current_org_id()) is None
        assert gp.refresh_organisation(current_org_id()) == {"skipped": "no_tenant_context"}
        with pytest.raises(ValueError):
            gp.build_projection(None)
    assert result.rows == []
    assert result.reason == "no_tenant_context"
    assert db_session.scalar(select(func.count()).select_from(GraphProjectionSnapshot)) == before


def test_an_organisation_with_no_model_gets_an_honest_empty_answer(db_session):
    empty = _org(db_session, "Empty")
    db_session.commit()
    result = gp.query(empty.id, 1, 3)
    assert result.rows == []
    assert result.reason == "no_data_recorded"
    assert result.by_layer == {} and result.by_type == {}
    assert gp.refresh_organisation(empty.id)["outcome"] == "empty"
    # An empty organisation is stored too, so its next refresh is the cheap early path.
    assert db_session.scalar(
        select(func.count()).select_from(GraphProjectionSnapshot).where(
            GraphProjectionSnapshot.organization_id == empty.id)) == 1
    assert gp.refresh_organisation(empty.id)["unchanged"] is True


def test_relationship_and_element_ids_that_collide_do_not_confuse_the_walk(db_session):
    org = _org(db_session, "Decoy")
    x, y, z = (_element(db_session, org.id, n) for n in "xyz")
    # A relationship whose id equals an element's id, joining two other elements.
    decoy = _rel(db_session, org.id, x.id, z.id)
    db_session.flush()
    clash = ArchiMateRelationship(id=y.id + 100000, type="Serving", source_id=y.id, target_id=z.id,
                                  organization_id=org.id)
    db_session.add(clash)
    child = _element(db_session, org.id, "child", parent_id=x.id)
    db_session.commit()

    projection = gp.build_projection(org.id)
    result = projection.traverse(x.id, 1, "downstream")
    by_id = {row.element_id: row for row in result.rows}
    assert set(by_id) == {z.id, child.id}
    assert by_id[z.id].via_edge == decoy.id and by_id[z.id].via_kind == "explicit"
    assert by_id[child.id].via_edge == -child.id and by_id[child.id].via_kind == "column"
    assert by_id[child.id].via_type == "parent"
    assert y.id not in by_id  # y is only reachable through the other relationship


# --------------------------------------------------------------------------- #
# Agreement with the canonical relational walk
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("direction", ["downstream", "upstream", "both"])
@pytest.mark.parametrize("depth", [1, 2, 3])
def test_traversal_equals_the_canonical_relational_walk(app, db_session, graph, tenant_ctx, direction, depth):
    org = graph["A"]
    projection = gp.build_projection(org.id)
    compared = 0
    for element in graph["a"].values():
        with tenant_ctx(org.id):
            expected = _canonical_depths(org.id, element.id, depth, direction)
        got = projection.traverse(element.id, depth, direction, kinds=("explicit",)).depth_by_element()
        assert got == expected, (element.name, direction, depth)
        compared += len(expected)
    assert compared > 0, "the fixture graph must give the comparison something to compare"


def test_traversal_reports_a_summary_by_layer_and_type(db_session):
    org = _org(db_session, "Summary")
    root = _element(db_session, org.id, "root")
    biz = _element(db_session, org.id, "svc", type_="BusinessService", layer="Business")
    app1 = _element(db_session, org.id, "app1")
    _rel(db_session, org.id, root.id, biz.id)
    _rel(db_session, org.id, root.id, app1.id)
    db_session.commit()
    result = gp.build_projection(org.id).traverse(root.id, 1)
    assert result.by_layer == {"business": 1, "application": 1}
    assert result.by_type == {"BusinessService": 1, "ApplicationComponent": 1}


def test_budget_truncates_and_says_so(db_session):
    org = _org(db_session, "Budget")
    hub = _element(db_session, org.id, "hub")
    for i in range(12):
        leaf = _element(db_session, org.id, f"leaf{i}")
        _rel(db_session, org.id, hub.id, leaf.id)
    db_session.commit()
    projection = gp.build_projection(org.id)
    cut = projection.traverse(hub.id, 1, max_nodes=5)
    assert len(cut.rows) == 5 and cut.truncated is True
    exact = projection.traverse(hub.id, 1, max_nodes=12)
    assert len(exact.rows) == 12 and exact.truncated is False


def test_invalid_arguments_are_refused(db_session, graph):
    projection = gp.build_projection(graph["A"].id)
    root = graph["a"]["a"].id
    for bad in (0, 11):
        with pytest.raises(ValueError):
            projection.traverse(root, bad)
    with pytest.raises(ValueError):
        projection.traverse(root, 2, "sideways")
    with pytest.raises(ValueError):
        projection.traverse(root, 2, kinds=("nonsense",))
    with pytest.raises(ValueError):
        projection.traverse(root, 2, max_nodes=0)


# --------------------------------------------------------------------------- #
# Event log: incremental maintenance equals a rebuild
# --------------------------------------------------------------------------- #


def test_incrementally_maintained_projection_equals_a_rebuild(db_session):
    org = _org(db_session, "Incremental")
    a, b, c = (_element(db_session, org.id, n) for n in "abc")
    ab = _rel(db_session, org.id, a.id, b.id)
    bc = _rel(db_session, org.id, b.id, c.id)
    _publish(db_session)

    maintained = gp.build_projection(org.id)
    gp.catch_up(maintained)
    start_ordinal = maintained.last_ordinal
    assert start_ordinal > 0
    assert maintained.same_content_as(gp.build_projection(org.id))

    # Every kind of change the ORM can make.
    d = _element(db_session, org.id, "d")
    cd = _rel(db_session, org.id, c.id, d.id)
    ab.type = "Flow"
    b.layer = "Business"
    d.parent_id = a.id
    db_session.delete(bc)
    _publish(db_session)
    e = _element(db_session, org.id, "e", parent_id=d.id)
    _rel(db_session, org.id, a.id, e.id)
    db_session.delete(cd)
    db_session.flush()
    db_session.delete(c)
    _publish(db_session)

    assert gp.catch_up(maintained) > 0
    assert maintained.last_ordinal > start_ordinal
    rebuilt = gp.build_projection(org.id)
    gp.catch_up(rebuilt)

    # The changes really are in the maintained copy...
    assert not maintained.has_node(c.id)
    assert maintained.has_node(d.id) and maintained.has_node(e.id)
    assert bc.id not in maintained._edges
    assert maintained._edges[ab.id][2] == maintained._string_index["Flow"]
    assert maintained._edges[gp.column_edge_key(d.id)][:2] == (a.id, d.id)
    assert maintained._edges[gp.column_edge_key(e.id)][:2] == (d.id, e.id)
    # ...and it equals the rebuild.
    assert maintained.same_content_as(rebuilt)
    assert maintained.fingerprint() == rebuilt.fingerprint()
    assert maintained.last_ordinal == rebuilt.last_ordinal


def test_applying_the_same_events_twice_changes_nothing(db_session):
    org = _org(db_session, "Idempotent")
    a, b = _element(db_session, org.id, "a"), _element(db_session, org.id, "b")
    _rel(db_session, org.id, a.id, b.id)
    _publish(db_session)
    projection = gp.build_projection(org.id)
    from app.services.event_log_service import read_from_offset

    events = read_from_offset(org.id, 0, limit=100)
    assert len(events) >= 3
    gp.apply_events(projection, events)
    once = projection.fingerprint()
    gp.apply_events(projection, events)
    gp.apply_events(projection, list(reversed(events)))
    assert projection.fingerprint() == once
    assert projection.same_content_as(gp.build_projection(org.id))


def test_events_of_another_organisation_never_change_this_projection(db_session, graph):
    _publish(db_session)
    projection_a = gp.build_projection(graph["A"].id)
    before = projection_a.fingerprint()
    # B changes after A's projection was taken.
    extra = _element(db_session, graph["B"].id, "late B element")
    _rel(db_session, graph["B"].id, extra.id, graph["a"]["a"].id)
    _publish(db_session)
    gp.catch_up(projection_a)
    assert projection_a.fingerprint() == before
    assert not projection_a.has_node(extra.id)


def test_an_event_for_an_id_in_another_organisation_is_treated_as_absent(db_session, graph):
    projection = gp.build_projection(graph["A"].id)
    forged = [{
        "ordinal": projection.last_ordinal + 1, "entity_type": "archimate_element",
        "entity_id": graph["b"]["x"].id, "event_type": "archimate_element.created",
    }, {
        "ordinal": projection.last_ordinal + 2, "entity_type": "archimate_relationship",
        "entity_id": graph["foreign_into_a"].id, "event_type": "archimate_relationship.created",
    }]
    gp.apply_events(projection, forged)
    assert not projection.has_node(graph["b"]["x"].id)
    assert graph["foreign_into_a"].id not in projection._edges


# --------------------------------------------------------------------------- #
# Stored snapshot
# --------------------------------------------------------------------------- #


def test_snapshot_round_trips_and_restores_the_same_projection(db_session, graph):
    org = graph["A"]
    projection = gp.build_projection(org.id)
    gp.save_snapshot(projection)
    db_session.commit()
    loaded = gp.load_snapshot(org.id)
    assert loaded is not None
    assert loaded.same_content_as(projection) and loaded.last_ordinal == projection.last_ordinal
    assert loaded.traverse(graph["a"]["a"].id, 3).depth_by_element() == \
        projection.traverse(graph["a"]["a"].id, 3).depth_by_element()
    row = db_session.scalars(select(GraphProjectionSnapshot).where(
        GraphProjectionSnapshot.organization_id == org.id)).one()
    assert row.node_count == projection.node_count and row.edge_count == projection.edge_count
    assert len(row.payload) > 0
    # Saving again replaces the one row.
    gp.save_snapshot(projection)
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(GraphProjectionSnapshot).where(
        GraphProjectionSnapshot.organization_id == org.id)) == 1


def test_a_damaged_or_foreign_snapshot_is_ignored_not_trusted(db_session, graph):
    org_a, org_b = graph["A"], graph["B"]
    projection_a = gp.build_projection(org_a.id)
    gp.save_snapshot(projection_a)
    db_session.commit()
    row_a = db_session.scalars(select(GraphProjectionSnapshot).where(
        GraphProjectionSnapshot.organization_id == org_a.id)).one()

    # A's payload stored under B's row: wrong organisation inside, so it is refused.
    foreign = GraphProjectionSnapshot(
        organization_id=org_b.id, format_version=1, last_ordinal=row_a.last_ordinal,
        node_count=row_a.node_count, edge_count=row_a.edge_count,
        fingerprint=row_a.fingerprint, payload=row_a.payload, built_at=datetime.utcnow())
    db_session.add(foreign)
    db_session.commit()
    assert gp.load_snapshot(org_b.id) is None

    # A checksum that no longer matches the stored bytes.
    row_a.fingerprint = "0" * 64
    db_session.commit()
    assert gp.load_snapshot(org_a.id) is None
    # Garbage bytes with a matching checksum: the decode fails, the snapshot is ignored.
    garbage = b"not a snapshot"
    row_a.payload = garbage
    row_a.fingerprint = hashlib.sha256(garbage).hexdigest()
    db_session.commit()
    assert gp.load_snapshot(org_a.id) is None
    # An unknown format version.
    good = projection_a.to_payload()
    row_a.payload = good
    row_a.fingerprint = hashlib.sha256(good).hexdigest()
    row_a.format_version = 99
    db_session.commit()
    assert gp.load_snapshot(org_a.id) is None
    # A stored offset that disagrees with the payload's own.
    row_a.format_version = 1
    row_a.last_ordinal = projection_a.last_ordinal + 5
    db_session.commit()
    assert gp.load_snapshot(org_a.id) is None
    # In every case the caller still gets a correct projection by rebuilding.
    assert gp.load_or_build(org_a.id).same_content_as(projection_a)


def test_a_fresh_process_loads_the_snapshot_and_replays_only_the_log_after_it(db_session):
    org = _org(db_session, "Replay")
    a, b = _element(db_session, org.id, "a"), _element(db_session, org.id, "b")
    _rel(db_session, org.id, a.id, b.id)
    _publish(db_session)
    first = gp.refresh_organisation(org.id)
    assert first["saved"] is True and first["from_snapshot"] is False and first["nodes"] == 2
    c = _element(db_session, org.id, "c")
    _rel(db_session, org.id, b.id, c.id)
    _publish(db_session)

    fresh = gp.get_projection(org.id)  # cache was cleared by the refresh, as in a new process
    assert fresh.has_node(c.id)
    assert fresh.traverse(a.id, 3).depth_by_element() == {b.id: 1, c.id: 2}
    assert fresh.same_content_as(gp.build_projection(org.id))


def test_refresh_stores_once_then_only_when_enough_has_changed(db_session, monkeypatch):
    org = _org(db_session, "Refresh")
    a, b = _element(db_session, org.id, "a"), _element(db_session, org.id, "b")
    _rel(db_session, org.id, a.id, b.id)
    _publish(db_session)
    first = gp.refresh_organisation(org.id)
    again = gp.refresh_organisation(org.id)
    assert first["saved"] is True and again["saved"] is False
    assert again["from_snapshot"] is True and again["reloaded"] == 0
    monkeypatch.setattr(gp, "SNAPSHOT_EVERY_EVENTS", 2)
    c = _element(db_session, org.id, "c")
    _rel(db_session, org.id, b.id, c.id)
    _publish(db_session)
    third = gp.refresh_organisation(org.id)
    assert third["saved"] is True and third["nodes"] == 3 and third["edges"] == 2
    assert gp.load_snapshot(org.id).has_node(c.id)


def test_verification_reports_a_write_that_bypassed_the_event_log(db_session):
    org = _org(db_session, "Drift")
    a, b = _element(db_session, org.id, "a"), _element(db_session, org.id, "b")
    _rel(db_session, org.id, a.id, b.id)
    _publish(db_session)
    gp.refresh_organisation(org.id)

    clean = gp.refresh_organisation(org.id, verify=True)
    assert clean["verified"] is True and clean["drift"] is False

    c = _element(db_session, org.id, "c")
    db_session.commit()
    relay_outbox_batch()
    # Raw SQL: no ORM flush, so no event, so the maintained projection cannot know.
    db_session.execute(
        text("INSERT INTO archimate_relationships (type, source_id, target_id, organization_id) "
             "VALUES ('Serving', :s, :t, :o)"),
        {"s": b.id, "t": c.id, "o": org.id},
    )
    db_session.commit()
    stale = gp.get_projection(org.id)
    assert stale.traverse(a.id, 3).depth_by_element() == {b.id: 1}  # still missing c's edge

    drifted = gp.refresh_organisation(org.id, verify=True)
    assert drifted["drift"] is True and drifted["saved"] is True
    gp.clear_cache(org.id)
    assert gp.get_projection(org.id).traverse(a.id, 3).depth_by_element() == {b.id: 1, c.id: 2}


def test_unverified_old_snapshot_is_verified_automatically(db_session):
    org = _org(db_session, "AutoVerify")
    a, b = _element(db_session, org.id, "a"), _element(db_session, org.id, "b")
    _rel(db_session, org.id, a.id, b.id)
    _publish(db_session)
    gp.refresh_organisation(org.id)
    assert gp.refresh_organisation(org.id)["verified"] is False
    row = db_session.scalars(select(GraphProjectionSnapshot).where(
        GraphProjectionSnapshot.organization_id == org.id)).one()
    row.verified_at = datetime.utcnow() - timedelta(hours=gp.VERIFY_EVERY_HOURS + 1)
    db_session.commit()
    assert gp.refresh_organisation(org.id)["verified"] is True


# --------------------------------------------------------------------------- #
# Interval edges: the model as of a date
# --------------------------------------------------------------------------- #


def _history(db_session, org_id, table, record_id, snapshot, valid_from, valid_to=None):
    row = EntityHistory(organization_id=org_id, table_name=table, record_id=record_id, snapshot=snapshot,
                        valid_from=valid_from, valid_to=valid_to, recorded_at=valid_from, source="trigger")
    db_session.add(row)
    db_session.flush()
    return row


def _relational_as_of_depths(db_session, org_id, root_id, depth, direction, when):
    """The history service's answer, walked breadth-first in plain Python."""
    from app.modules.intelligence.services.history_service import HistoryService

    model = HistoryService(db_session, org_id).as_of(when)
    seen, frontier, out = {root_id}, [root_id], {}
    for hop in range(1, depth + 1):
        nxt = []
        for node in frontier:
            for rel in model.relationships:
                ends = []
                if direction in ("downstream", "both") and rel.source_id == node:
                    ends.append(rel.target_id)
                if direction in ("upstream", "both") and rel.target_id == node:
                    ends.append(rel.source_id)
                for end in ends:
                    if end not in seen:
                        seen.add(end)
                        out[end] = hop
                        nxt.append(end)
        frontier = nxt
    return out


@pytest.fixture
def timeline(db_session):
    """a -> b valid from t0; b -> c from t1 to t3; a -> d from t2 (b -> c is replaced by b -> e at t3).
    Element 'gone' was deleted after t3 but its history remains."""
    org, other = _org(db_session, "Timeline"), _org(db_session, "Timeline other")
    names = {n: _element(db_session, org.id, n) for n in ("a", "b", "c", "d", "e")}
    gone = _element(db_session, org.id, "gone")
    t0 = datetime(2026, 1, 1)
    t1, t2, t3, t4 = (t0 + timedelta(days=30 * i) for i in (1, 2, 3, 4))
    ids = {k: v.id for k, v in names.items()}
    ab = _rel(db_session, org.id, ids["a"], ids["b"])
    bc = _rel(db_session, org.id, ids["b"], ids["e"])  # current state of the b relationship
    ad = _rel(db_session, org.id, ids["a"], ids["d"])
    snap = lambda s, t, ty="Serving": {"source_id": s, "target_id": t, "type": ty}  # noqa: E731
    _history(db_session, org.id, "archimate_relationships", ab.id, snap(ids["a"], ids["b"]), t0)
    _history(db_session, org.id, "archimate_relationships", bc.id, snap(ids["b"], ids["c"]), t1, t3)
    _history(db_session, org.id, "archimate_relationships", bc.id, snap(ids["b"], ids["e"]), t3)
    _history(db_session, org.id, "archimate_relationships", ad.id, snap(ids["a"], ids["d"]), t2)
    # A relationship that was removed from the model at t3 but whose far end is gone too.
    rm = _rel(db_session, org.id, ids["c"], gone.id)
    _history(db_session, org.id, "archimate_relationships", rm.id, snap(ids["c"], gone.id), t1)
    for e in list(names.values()) + [gone]:
        _history(db_session, org.id, "archimate_elements", e.id, {"name": e.name, "type": e.type, "layer": e.layer}, t0)
    # Another organisation's history for the same record ids must never leak in.
    _history(db_session, other.id, "archimate_relationships", ab.id, snap(ids["a"], ids["e"]), t0)
    db_session.commit()
    db_session.delete(rm)
    db_session.flush()
    db_session.delete(gone)
    db_session.commit()
    return {"org": org, "other": other, "ids": ids, "gone": gone.id, "times": (t0, t1, t2, t3, t4), "rels": (ab, bc, ad, rm)}


@pytest.mark.parametrize("direction", ["downstream", "upstream", "both"])
def test_as_of_traversal_equals_the_history_services_relational_answer(db_session, timeline, direction):
    org, ids, times = timeline["org"], timeline["ids"], timeline["times"]
    t0, t1, t2, t3, t4 = times
    projection = gp.build_projection(org.id)
    instants = [t0 - timedelta(days=1), t0, t1 + timedelta(days=1), t2 + timedelta(days=1), t3, t4]
    compared = 0
    for when in instants:
        for root in ids.values():
            expected = _relational_as_of_depths(db_session, org.id, root, 4, direction, when)
            got = projection.traverse(root, 4, direction, as_of=when).depth_by_element()
            assert got == expected, (when, root, direction)
            compared += len(expected)
    assert compared > 0

    # Spot-check real content so agreement is not agreement on nothing.
    mid = projection.traverse(ids["a"], 3, "downstream", as_of=t2 + timedelta(days=1)).depth_by_element()
    assert mid == {ids["b"]: 1, ids["d"]: 1, ids["c"]: 2, timeline["gone"]: 3}
    late = projection.traverse(ids["a"], 3, "downstream", as_of=t4).depth_by_element()
    # b -> c ended at t3 (replaced by b -> e), so c and the deleted element are not reachable at t4.
    assert late == {ids["b"]: 1, ids["d"]: 1, ids["e"]: 2}
    before = projection.traverse(ids["a"], 3, "downstream", as_of=t0 - timedelta(days=1))
    assert before.rows == [] and before.reason is None


def test_as_of_does_not_read_another_organisations_history(db_session, timeline):
    org, other, ids, (t0, *_rest) = timeline["org"], timeline["other"], timeline["ids"], timeline["times"]
    projection = gp.build_projection(org.id)
    got = projection.traverse(ids["a"], 1, "downstream", as_of=t0 + timedelta(days=1)).depth_by_element()
    assert got == {ids["b"]: 1}  # the other organisation's a -> e row is not here
    assert gp.build_projection(other.id).version_count == 1


def test_current_walk_does_not_cross_into_a_deleted_element_but_as_of_can(db_session, timeline):
    org, ids, times = timeline["org"], timeline["ids"], timeline["times"]
    projection = gp.build_projection(org.id)
    current = projection.traverse(ids["c"], 2, "downstream").depth_by_element()
    assert timeline["gone"] not in current
    assert timeline["gone"] in projection._retired
    earlier = projection.traverse(ids["c"], 2, "downstream", as_of=times[1] + timedelta(days=1)).depth_by_element()
    assert earlier == {timeline["gone"]: 1}


def test_history_written_after_the_build_is_picked_up_from_the_event(db_session):
    org = _org(db_session, "LateHistory")
    a, b, c = (_element(db_session, org.id, n) for n in "abc")
    rel = _rel(db_session, org.id, a.id, b.id)
    t0 = datetime(2026, 3, 1)
    _history(db_session, org.id, "archimate_relationships", rel.id,
             {"source_id": a.id, "target_id": b.id, "type": "Serving"}, t0, datetime(2026, 4, 1))
    _publish(db_session)
    maintained = gp.build_projection(org.id)
    gp.catch_up(maintained)

    rel.target_id = c.id
    _history(db_session, org.id, "archimate_relationships", rel.id,
             {"source_id": a.id, "target_id": c.id, "type": "Serving"}, datetime(2026, 4, 1))
    _publish(db_session)
    gp.catch_up(maintained)

    assert maintained.traverse(a.id, 1, as_of=datetime(2026, 3, 15)).depth_by_element() == {b.id: 1}
    assert maintained.traverse(a.id, 1, as_of=datetime(2026, 5, 1)).depth_by_element() == {c.id: 1}
    assert maintained.same_content_as(gp.build_projection(org.id))


def test_aware_as_of_instants_are_read_as_utc(db_session, timeline):
    from datetime import timezone

    org, ids, times = timeline["org"], timeline["ids"], timeline["times"]
    projection = gp.build_projection(org.id)
    naive = times[1] + timedelta(days=1)
    aware = naive.replace(tzinfo=timezone.utc)
    assert projection.traverse(ids["a"], 3, as_of=aware).depth_by_element() == \
        projection.traverse(ids["a"], 3, as_of=naive).depth_by_element()


# --------------------------------------------------------------------------- #
# The scheduled job and its registration
# --------------------------------------------------------------------------- #


def test_job_is_declared_as_a_tenant_job():
    from app.jobs.graph_projection_job import JOB_NAME
    from app.jobs.tenant_safe_job import PLATFORM_JOBS, TENANT_JOBS

    assert "graph_projection_refresh" in TENANT_JOBS
    assert "graph_projection_refresh" not in PLATFORM_JOBS
    assert JOB_NAME == "graph_projection_refresh"


def test_job_refreshes_each_organisation_inside_its_own_scope(app, db_session, monkeypatch):
    from app.jobs import graph_projection_job
    from flask import g

    seen = []

    def fake_refresh(organization_id):
        seen.append((organization_id, g.current_org_id))
        return {"outcome": "ok"}

    monkeypatch.setattr(gp, "refresh_organisation", fake_refresh)
    run = graph_projection_job.run_graph_projection_refresh(app, organization_ids=[11, 12])
    assert [r.ok for r in run.results] == [True, True]
    assert seen == [(11, 11), (12, 12)]


def test_job_builds_and_stores_a_snapshot_for_the_organisation_it_is_given(app, db_session):
    from app.jobs import graph_projection_job

    org = _org(db_session, "Job")
    other = _org(db_session, "Job other")
    a, b = _element(db_session, org.id, "a"), _element(db_session, org.id, "b")
    _rel(db_session, org.id, a.id, b.id)
    _element(db_session, other.id, "z")
    db_session.commit()
    run = graph_projection_job.run_graph_projection_refresh(app, organization_ids=[org.id])
    assert [r.ok for r in run.results] == [True], [r.error for r in run.results]
    assert run.results[0].value["saved"] is True and run.results[0].value["nodes"] == 2
    owners = set(db_session.scalars(select(GraphProjectionSnapshot.organization_id).where(
        GraphProjectionSnapshot.organization_id.in_([org.id, other.id]))))
    assert owners == {org.id}


class _FakeScheduler:
    def __init__(self):
        self.jobs = []
        self.started = False

    def add_job(self, **kwargs):
        self.jobs.append(kwargs)

    def get_jobs(self):
        return [type("J", (), {"id": j["id"]})() for j in self.jobs]

    def remove_job(self, job_id):
        self.jobs = [j for j in self.jobs if j["id"] != job_id]

    def start(self):
        self.started = True

    def pause(self):
        return None

    def shutdown(self, wait=False):
        return None


def _scheduler_with(monkeypatch, **config):
    import apscheduler.schedulers.background
    from flask import Flask

    from app._bootstrap.extensions import init_scheduler

    scheduler = _FakeScheduler()
    monkeypatch.setattr(apscheduler.schedulers.background, "BackgroundScheduler", lambda: scheduler)
    flask_app = Flask("graph-projection-scheduler-test")
    flask_app.config.update(TESTING=False, **config)
    init_scheduler(flask_app)
    return scheduler


def test_scheduler_registers_the_refresh_when_an_interval_is_configured(monkeypatch):
    scheduler = _scheduler_with(monkeypatch, GRAPH_PROJECTION_INTERVAL_MINUTES="7")
    jobs = {job["id"]: job for job in scheduler.jobs}
    assert "graph_projection_refresh" in jobs
    assert str(jobs["graph_projection_refresh"]["trigger"]) == "interval[0:07:00]"
    assert jobs["graph_projection_refresh"]["max_instances"] == 1


def test_scheduler_skips_the_refresh_on_a_bad_interval_and_keeps_the_others(monkeypatch):
    scheduler = _scheduler_with(monkeypatch, GRAPH_PROJECTION_INTERVAL_MINUTES="0")
    ids = {job["id"] for job in scheduler.jobs}
    assert "graph_projection_refresh" not in ids
    assert "event_log_relay" in ids


def test_default_configuration_schedules_the_refresh(app):
    assert int(app.config["GRAPH_PROJECTION_INTERVAL_MINUTES"]) > 0


def test_refresh_of_an_unchanged_organisation_does_not_decode_the_snapshot(db_session, monkeypatch):
    org = _org(db_session, "Unchanged")
    a, b = _element(db_session, org.id, "a"), _element(db_session, org.id, "b")
    _rel(db_session, org.id, a.id, b.id)
    _publish(db_session)
    gp.refresh_organisation(org.id)

    def _must_not_decode(*args, **kwargs):
        raise AssertionError("the snapshot payload was decoded although nothing changed")

    monkeypatch.setattr(gp.GraphProjection, "from_payload", classmethod(_must_not_decode))
    again = gp.refresh_organisation(org.id)
    assert again["unchanged"] is True and again["saved"] is False and again["reloaded"] == 0
    assert again["nodes"] == 2 and again["edges"] == 1


def test_refresh_does_the_full_work_once_the_log_has_moved(db_session):
    org = _org(db_session, "Moved")
    a, b = _element(db_session, org.id, "a"), _element(db_session, org.id, "b")
    _rel(db_session, org.id, a.id, b.id)
    _publish(db_session)
    gp.refresh_organisation(org.id)
    c = _element(db_session, org.id, "c")
    _rel(db_session, org.id, b.id, c.id)
    _publish(db_session)
    moved = gp.refresh_organisation(org.id)
    assert "unchanged" not in moved and moved["reloaded"] > 0 and moved["nodes"] == 3


def test_history_beyond_the_cap_drops_the_oldest_closed_versions_and_says_so():
    projection = gp.GraphProjection(1)
    for node in (1, 2, 3):
        projection.set_node(node, "Node", "technology")
    t = datetime(2026, 1, 1)
    # relationship 10: three closed versions (oldest first) and one open one.
    projection.set_versions(10, [
        (t, t + timedelta(days=1), 1, 2, "Serving"),
        (t + timedelta(days=1), t + timedelta(days=2), 1, 3, "Serving"),
        (t + timedelta(days=2), t + timedelta(days=3), 1, 2, "Serving"),
        (t + timedelta(days=3), None, 1, 2, "Serving"),
    ])
    assert projection.version_count == 4
    assert projection.enforce_version_cap(2) == 2
    assert projection.version_count == 2 and projection.history_truncated is True
    assert projection.history_floor == t + timedelta(days=2)
    kept = projection._versions[10]
    assert kept[-1][1] is None, "the open version is always kept"
    assert min(v[0] for v in kept) == t + timedelta(days=2), "the oldest closed versions went first"
    # Older than what was dropped: flagged, and never as the node budget's flag.
    old = projection.traverse(1, 1, "downstream", as_of=t + timedelta(days=1, hours=1))
    assert old.history_truncated is True and old.truncated is False
    # Newer than every dropped version, and the current walk: complete.
    assert projection.traverse(1, 1, "downstream", as_of=t + timedelta(days=10)).history_truncated is False
    assert projection.traverse(1, 1, "downstream").history_truncated is False
    restored = gp.GraphProjection.from_payload(projection.to_payload(), 1)
    assert restored.history_truncated is True and restored.version_count == 2


def test_the_version_cap_does_nothing_at_the_cap_and_trims_below_it_when_over():
    projection = gp.GraphProjection(1)
    t = datetime(2026, 1, 1)
    for node in range(1, 4):
        projection.set_node(node, "Node", "technology")
    for rid in range(1, 41):
        projection.set_versions(rid, [(t, t + timedelta(days=rid), 1, 2, "Serving")])
    assert projection.enforce_version_cap(40) == 0 and projection.history_floor is None
    assert projection.enforce_version_cap(50) == 0
    dropped = projection.enforce_version_cap(30)  # over: trims to 90% of the cap (27)
    assert dropped == 13 and projection.version_count == 27
    assert projection.history_floor == t + timedelta(days=13)
    assert sorted(projection._versions) == list(range(14, 41)), "the oldest closed versions went"
    assert projection.enforce_version_cap(30) == 0, "no repeat work until over the cap again"


def test_a_lagging_snapshot_is_saved_so_the_next_refresh_is_the_early_path(db_session):
    org = _org(db_session, "Lagging")
    a, b = _element(db_session, org.id, "a"), _element(db_session, org.id, "b")
    _rel(db_session, org.id, a.id, b.id)
    _publish(db_session)
    gp.refresh_organisation(org.id)
    _element(db_session, org.id, "c")  # one more change: far fewer than a batch of events
    _publish(db_session)
    behind = gp.refresh_organisation(org.id)
    assert "unchanged" not in behind and behind["reloaded"] > 0 and behind["saved"] is True
    quiet = gp.refresh_organisation(org.id)
    assert quiet["unchanged"] is True and quiet["nodes"] == 3
