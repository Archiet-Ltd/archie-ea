"""Tests for ``IntelligenceQueryService.risk_for_element`` (L6): risks
seeded directly on an element, each carrying its own blast-radius
traversal via the same ``cross_layer_impact`` code path L1 already uses --
no second traversal implementation, no fabricated risk score.

Fixtures (app, db_session, make_org) are discovered via
app/modules/conftest.py's import of tests.conftest,
same pattern as test_query_service.py. No import needed here.
"""

from __future__ import annotations


def _element(db_session, org_id, name, layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="ApplicationComponent", layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type=type_, organization_id=org_id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _risk(db_session, org_id, element, *, title="Vendor lock-in", likelihood=4, impact=5, owner=None,
          mitigation_plan=None):
    from app.models.risk import Risk

    row = Risk(
        organization_id=org_id,
        archimate_element_id=element.id,
        title=title,
        likelihood=likelihood,
        impact=impact,
        owner=owner,
        mitigation_plan=mitigation_plan,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_element_with_no_risks_returns_honest_empty_not_silent_zero(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("risk-lens-empty")
    a = _element(db_session, org.id, "A")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.risk_for_element(a.id)

    assert result["risks"] == []
    assert result["reasons"] == ["no_risk_recorded"]


def test_unknown_element_returns_element_not_found_reason(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("risk-lens-unknown")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.risk_for_element(999999999)

    assert result["risks"] == []
    assert result["reasons"] == ["element_not_found"]


def test_no_tenant_context_returns_honest_reason_not_cross_tenant_data(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("risk-lens-no-ctx")
    a = _element(db_session, org.id, "A")
    _risk(db_session, org.id, a)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None
        result = IntelligenceQueryService.risk_for_element(a.id)

    assert result["risks"] == []
    assert result["reasons"] == ["no_tenant_context"]


def test_direct_risk_returns_its_own_fields_and_display_score(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("risk-lens-direct")
    a = _element(db_session, org.id, "A")
    _risk(
        db_session, org.id, a,
        title="Single vendor dependency", likelihood=4, impact=5,
        owner="platform-team", mitigation_plan="Dual-source by Q3",
    )
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.risk_for_element(a.id)

    assert result["reasons"] == []
    assert len(result["risks"]) == 1
    row = result["risks"][0]
    assert row["title"] == "Single vendor dependency"
    assert row["likelihood"] == 4
    assert row["impact"] == 5
    # risk_score/risk_level are the model's own display properties
    # (likelihood x impact), never recomputed here -- one source of truth.
    assert row["risk_score"] == 20
    assert row["risk_level"] == "critical"
    assert row["owner"] == "platform-team"
    assert row["mitigation_plan"] == "Dual-source by Q3"


def test_risk_blast_radius_reuses_cross_layer_impact_not_a_second_traversal(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("risk-lens-blast")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    _risk(db_session, org.id, a, title="A is a single point of failure")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        risk_result = IntelligenceQueryService.risk_for_element(a.id, max_depth=3)
        direct_impact = IntelligenceQueryService.cross_layer_impact(a.id, max_depth=3, with_owner=True)

    assert len(risk_result["risks"]) == 1
    # The blast radius attached to the risk is byte-identical in shape to
    # calling the impact traversal directly on the same seed -- same rows,
    # same summary -- proving this is the SAME code path, not a parallel
    # reimplementation of the traversal.
    assert risk_result["risks"][0]["affected_rows"] == direct_impact["rows"]
    assert risk_result["risks"][0]["affected_summary"]["explicit_count"] == direct_impact["summary"]["explicit_count"]


def test_multiple_risks_on_one_element_each_get_their_own_row(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("risk-lens-multi")
    a = _element(db_session, org.id, "A")
    _risk(db_session, org.id, a, title="Risk one", likelihood=2, impact=2)
    _risk(db_session, org.id, a, title="Risk two", likelihood=5, impact=5)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.risk_for_element(a.id)

    titles = {r["title"] for r in result["risks"]}
    assert titles == {"Risk one", "Risk two"}


def test_cross_tenant_risk_is_invisible_not_leaked(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org_a = make_org("risk-lens-tenant-a")
    org_b = make_org("risk-lens-tenant-b")
    a = _element(db_session, org_a.id, "A")
    _risk(db_session, org_a.id, a, title="Tenant A's own risk")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        # Same element id, wrong tenant context -- the element itself is
        # already tenant-fenced (ArchiMateElement.query goes through the
        # ORM listener), so this must come back as element-not-found, not a
        # leaked risk from tenant A.
        g.current_org_id = org_b.id
        result = IntelligenceQueryService.risk_for_element(a.id)

    assert result["risks"] == []
    assert result["reasons"] == ["element_not_found"]
