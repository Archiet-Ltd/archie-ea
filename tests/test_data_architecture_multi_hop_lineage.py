"""Multi-hop data lineage walk (R1-B80, TB-0319): held from source through
every transformation to a report, with the owner at each hop.

Extends the existing single-hop pattern (IntelligenceQueryService's Ask
Data lens) to walk multiple hops, bounded and breadth-first, rather than
building a second graph engine.
"""

from __future__ import annotations

import uuid

import pytest

from app.modules.architecture.services.data_architecture_service import DataArchitectureService


def _element(db_session, org_id, name="Element"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _edge(db_session, org_id, source, target, **fields):
    from app.models.all_missing_models import DataLineage

    edge = DataLineage(
        name=f"edge-{uuid.uuid4().hex[:6]}", archimate_element_id=source.id,
        target_archimate_element_id=target.id, organization_id=org_id, **fields,
    )
    db_session.add(edge)
    db_session.flush()
    return edge


def _component(db_session, org_id, element):
    from app.models.application_portfolio import ApplicationComponent

    comp = ApplicationComponent(
        name=f"Component {uuid.uuid4().hex[:6]}", organization_id=org_id,
        archimate_element_id=element.id,
    )
    db_session.add(comp)
    db_session.flush()
    return comp


def _owner(db_session, org_id, component, user, ownership_type="primary"):
    from app.models.application_owner import ApplicationOwner

    row = ApplicationOwner(
        application_id=component.id, user_id=user.id, organization_id=org_id,
        ownership_type=ownership_type,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"owner-{uuid.uuid4().hex[:8]}@example.test", first_name="Data", last_name="Owner",
        organization_id=org_id, confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def test_walks_multiple_hops_in_breadth_first_order(app, db_session, make_org):
    org = make_org("lineage-multihop")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    c = _element(db_session, org.id, "C")
    _edge(db_session, org.id, a, b)
    _edge(db_session, org.id, b, c)
    db_session.commit()

    with app.app_context():
        result = DataArchitectureService.multi_hop_lineage(a.id, org.id, max_hops=5)

    by_name = {hop["element_name"]: hop["depth"] for hop in result["hops"]}
    assert by_name == {"A": 0, "B": 1, "C": 2}
    assert result["truncated"] is False


def test_a_cycle_is_not_walked_forever(app, db_session, make_org):
    org = make_org("lineage-cycle")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _edge(db_session, org.id, a, b)
    _edge(db_session, org.id, b, a)
    db_session.commit()

    with app.app_context():
        result = DataArchitectureService.multi_hop_lineage(a.id, org.id, max_hops=10)

    names = [hop["element_name"] for hop in result["hops"]]
    assert sorted(names) == ["A", "B"]
    assert result["truncated"] is False


def test_the_walk_stops_at_max_hops_and_reports_truncation(app, db_session, make_org):
    org = make_org("lineage-long-chain")
    elements = [_element(db_session, org.id, f"E{i}") for i in range(6)]
    for i in range(5):
        _edge(db_session, org.id, elements[i], elements[i + 1])
    db_session.commit()

    with app.app_context():
        result = DataArchitectureService.multi_hop_lineage(elements[0].id, org.id, max_hops=2)

    depths = sorted(hop["depth"] for hop in result["hops"])
    assert depths == [0, 1, 2]  # E0 at depth 0, then E1 at depth 1, E2 at depth 2; E3-E5 unreached
    assert result["truncated"] is True


def test_max_hops_is_capped_at_the_safety_ceiling(app, db_session, make_org):
    org = make_org("lineage-cap")
    el = _element(db_session, org.id)
    db_session.commit()

    with app.app_context():
        # Should not raise or hang even if asked for an absurd depth.
        result = DataArchitectureService.multi_hop_lineage(el.id, org.id, max_hops=10_000)

    assert result["hops"][0]["element_id"] == el.id


def test_an_unknown_element_returns_no_hops(app, db_session, make_org):
    org = make_org("lineage-unknown")
    db_session.commit()

    with app.app_context():
        result = DataArchitectureService.multi_hop_lineage(999999999, org.id)

    assert result == {"hops": [], "truncated": False}


def test_a_foreign_organisations_element_is_dropped_not_named(app, db_session, make_org):
    org_a = make_org("lineage-fence-a")
    org_b = make_org("lineage-fence-b")
    a = _element(db_session, org_a.id, "Mine")
    foreign = _element(db_session, org_b.id, "Not mine")
    _edge(db_session, org_a.id, a, foreign)
    db_session.commit()

    with app.app_context():
        result = DataArchitectureService.multi_hop_lineage(a.id, org_a.id, max_hops=5)

    names = [hop["element_name"] for hop in result["hops"]]
    assert names == ["Mine"]


def test_each_hop_carries_its_elements_owner_via_the_canonical_reader(app, db_session, make_org):
    org = make_org("lineage-owner")
    a = _element(db_session, org.id, "Source")
    b = _element(db_session, org.id, "Target")
    comp_b = _component(db_session, org.id, b)
    owner = _user(db_session, org.id)
    _owner(db_session, org.id, comp_b, owner)
    _edge(db_session, org.id, a, b)
    db_session.commit()

    with app.app_context():
        result = DataArchitectureService.multi_hop_lineage(a.id, org.id, max_hops=5)

    by_name = {hop["element_name"]: hop["owner"] for hop in result["hops"]}
    assert by_name["Source"] is None
    assert by_name["Target"]["user_name"] == "Data Owner"


def test_no_owner_recorded_is_none_not_a_fabricated_name(app, db_session, make_org):
    org = make_org("lineage-no-owner")
    a = _element(db_session, org.id, "Alone")
    db_session.commit()

    with app.app_context():
        result = DataArchitectureService.multi_hop_lineage(a.id, org.id)

    assert result["hops"][0]["owner"] is None


def test_downstream_impact_only_follows_outgoing_edges(app, db_session, make_org):
    """PB-0116: the impact of a change to *a* is every consumer *a* feeds
    -- an edge pointing INTO *a* is upstream, not a consumer, and must
    not appear."""
    org = make_org("impact-direction")
    upstream = _element(db_session, org.id, "Upstream")
    a = _element(db_session, org.id, "Source")
    downstream = _element(db_session, org.id, "Consumer")
    _edge(db_session, org.id, upstream, a)
    _edge(db_session, org.id, a, downstream)
    db_session.commit()

    with app.app_context():
        result = DataArchitectureService.downstream_impact(a.id, org.id)

    names = {c["element_name"] for c in result["consumers"]}
    assert "Consumer" in names
    assert "Upstream" not in names


def test_downstream_impact_ranks_by_criticality_critical_first(app, db_session, make_org):
    org = make_org("impact-ranking")
    a = _element(db_session, org.id, "Source")
    low = _element(db_session, org.id, "Low consumer")
    critical = _element(db_session, org.id, "Critical consumer")
    _edge(db_session, org.id, a, low)
    _edge(db_session, org.id, a, critical)
    db_session.flush()

    from app.models.application_portfolio import ApplicationComponent

    db_session.add(ApplicationComponent(
        name="Low comp", organization_id=org.id, archimate_element_id=low.id,
        business_criticality="Low",
    ))
    db_session.add(ApplicationComponent(
        name="Critical comp", organization_id=org.id, archimate_element_id=critical.id,
        business_criticality="Critical",
    ))
    db_session.commit()

    with app.app_context():
        result = DataArchitectureService.downstream_impact(a.id, org.id)

    ordered_names = [c["element_name"] for c in result["consumers"]]
    assert ordered_names.index("Critical consumer") < ordered_names.index("Low consumer")


def test_downstream_impact_with_no_recorded_criticality_shows_dash_not_fabricated(app, db_session, make_org):
    org = make_org("impact-no-criticality")
    a = _element(db_session, org.id, "Source")
    consumer = _element(db_session, org.id, "Unrated consumer")
    _edge(db_session, org.id, a, consumer)
    db_session.commit()

    with app.app_context():
        result = DataArchitectureService.downstream_impact(a.id, org.id)

    assert result["consumers"][0]["criticality"] == "—"


def test_downstream_impact_two_organisations_never_cross(app, db_session, make_org):
    org_a = make_org("impact-fence-a")
    org_b = make_org("impact-fence-b")
    a_source = _element(db_session, org_a.id, "A-source")
    a_consumer = _element(db_session, org_a.id, "A-consumer")
    b_source = _element(db_session, org_b.id, "B-source")
    b_consumer = _element(db_session, org_b.id, "B-consumer")
    _edge(db_session, org_a.id, a_source, a_consumer)
    _edge(db_session, org_b.id, b_source, b_consumer)
    db_session.commit()

    with app.app_context():
        result = DataArchitectureService.downstream_impact(a_source.id, org_a.id)

    names = {c["element_name"] for c in result["consumers"]}
    assert "A-consumer" in names
    assert "B-consumer" not in names
