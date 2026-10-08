"""ArchiMateImpactService delegates to the one canonical impact engine
(PR 297 defects 2-4: it used to run its own untenanted traversal)."""

from __future__ import annotations


def _element(db_session, org_id, name, etype="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=etype, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def test_analyze_impact_finds_direct_and_indirect_impacts(app, db_session, make_org):
    org = make_org("archimate-impact-basic")
    a = _element(db_session, org.id, "Root")
    b = _element(db_session, org.id, "Direct")
    c = _element(db_session, org.id, "Indirect")
    _relationship(db_session, org.id, a, b)
    _relationship(db_session, org.id, b, c)
    db_session.commit()

    from app.services.archimate_impact_service import ArchiMateImpactService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = ArchiMateImpactService().analyze_impact(a.id, max_hops=3)

    assert "error" not in result
    direct_ids = {i["id"] for i in result["direct_impacts"]}
    indirect_ids = {i["id"] for i in result["indirect_impacts"]}
    assert direct_ids == {b.id}
    assert indirect_ids == {c.id}
    assert result["total_impacted"] == 2
    # Fields the template (impact_analysis.html) reads on every row.
    for imp in result["direct_impacts"] + result["indirect_impacts"]:
        assert imp["name"] is not None
        assert imp["via_relationship"] == "Serving"
        assert imp["direction"] in ("outgoing", "incoming")


def test_analyze_impact_never_crosses_org_boundary(app, db_session, make_org):
    """The old implementation ran ArchiMateElement.query.get() / .query.filter()
    with no tenant predicate at all -- confirm the delegated version is fenced."""
    org_a = make_org("archimate-impact-org-a")
    org_b = make_org("archimate-impact-org-b")

    a1 = _element(db_session, org_a.id, "A1")
    a2 = _element(db_session, org_a.id, "A2")
    _relationship(db_session, org_a.id, a1, a2)

    b1 = _element(db_session, org_b.id, "B1")
    b2 = _element(db_session, org_b.id, "B2")
    _relationship(db_session, org_b.id, b1, b2)
    db_session.commit()

    from app.services.archimate_impact_service import ArchiMateImpactService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_a.id
        result = ArchiMateImpactService().analyze_impact(a1.id, max_hops=3)

    all_ids = {i["id"] for i in result["direct_impacts"] + result["indirect_impacts"]}
    assert all_ids == {a2.id}
    assert b1.id not in all_ids
    assert b2.id not in all_ids

    # And org B's own tenant context must never resolve org A's element at all.
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_b.id
        cross_org_result = ArchiMateImpactService().analyze_impact(a1.id, max_hops=3)

    assert cross_org_result.get("error") == "Element not found"


def test_get_capability_gaps_finds_strategy_elements(app, db_session, make_org):
    org = make_org("archimate-impact-gaps")
    a = _element(db_session, org.id, "Root")
    s = _element(db_session, org.id, "At-Risk Strategy", etype="Capability", layer="Strategy")
    _relationship(db_session, org.id, a, s)
    db_session.commit()

    from app.services.archimate_impact_service import ArchiMateImpactService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        gaps = ArchiMateImpactService().get_capability_gaps(a.id)

    assert {g_["id"] for g_ in gaps} == {s.id}


def test_get_impact_summary_matches_analyze_impact(app, db_session, make_org):
    org = make_org("archimate-impact-summary")
    a = _element(db_session, org.id, "Root")
    b = _element(db_session, org.id, "Direct")
    _relationship(db_session, org.id, a, b)
    db_session.commit()

    from app.services.archimate_impact_service import ArchiMateImpactService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        summary = ArchiMateImpactService().get_impact_summary(a.id, max_hops=2)

    assert summary["total_affected"] == 1
    assert summary["by_layer"].get("Application") == 1
