"""Tests for ``IntelligenceQueryService.strategy_for_element`` (L2):
initiatives seeded directly on an element, each carrying its own
blast-radius traversal via the same ``cross_layer_impact`` code path
L1/L5/L6 already use -- no second traversal implementation, no fabricated
budget figures.

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


def _initiative(db_session, element, *, name="Cloud migration programme", status="Active",
                 priority="High", health_status="Green", completion_percentage=40,
                 total_budget=None, spent_to_date=None, start_date=None, target_end_date=None,
                 executive_sponsor=None, program_manager=None):
    from app.models.enterprise_intelligence import PortfolioInitiative

    initiative = PortfolioInitiative(
        name=name,
        archimate_element_id=element.id,
        status=status,
        priority=priority,
        health_status=health_status,
        completion_percentage=completion_percentage,
        total_budget=total_budget,
        spent_to_date=spent_to_date,
        start_date=start_date,
        target_end_date=target_end_date,
        executive_sponsor=executive_sponsor,
        program_manager=program_manager,
    )
    db_session.add(initiative)
    db_session.flush()
    return initiative


def _metric(db_session, initiative, *, metric_name="Adoption rate", metric_type="KPI",
            target_value="90%", actual_value="60%", status="At Risk"):
    from app.models.enterprise_intelligence import InitiativeSuccessMetric

    metric = InitiativeSuccessMetric(
        initiative_id=initiative.id,
        metric_name=metric_name,
        metric_type=metric_type,
        target_value=target_value,
        actual_value=actual_value,
        status=status,
    )
    db_session.add(metric)
    db_session.flush()
    return metric


def test_element_with_no_initiatives_returns_honest_empty(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("strategy-lens-empty")
    a = _element(db_session, org.id, "A")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.strategy_for_element(a.id)

    assert result["initiatives"] == []
    assert result["reasons"] == ["no_initiative_linked"]


def test_unknown_element_returns_element_not_found_reason(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("strategy-lens-unknown")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.strategy_for_element(999999999)

    assert result["initiatives"] == []
    assert result["reasons"] == ["element_not_found"]


def test_no_tenant_context_returns_honest_reason(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("strategy-lens-no-ctx")
    a = _element(db_session, org.id, "A")
    _initiative(db_session, a)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None
        result = IntelligenceQueryService.strategy_for_element(a.id)

    assert result["initiatives"] == []
    assert result["reasons"] == ["no_tenant_context"]


def test_initiative_with_real_budget_data_returns_variance(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("strategy-lens-costed")
    a = _element(db_session, org.id, "A")
    _initiative(
        db_session, a, name="ERP consolidation",
        total_budget=100000.0, spent_to_date=120000.0,
    )
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.strategy_for_element(a.id)

    assert result["reasons"] == []
    assert len(result["initiatives"]) == 1
    row = result["initiatives"][0]
    assert row["name"] == "ERP consolidation"
    assert row["budget_reason"] is None
    assert row["budget_variance_pct"] == 20.0


def test_initiative_with_no_budget_is_honestly_not_costed(app, db_session, make_org):
    """Pins the same fabrication risk L5's not_costed test pins: an
    initiative with no total_budget must never report a variance of 0 --
    that reads as "on budget", a claim nobody measured."""
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("strategy-lens-uncosted")
    a = _element(db_session, org.id, "A")
    _initiative(db_session, a, name="Discovery workshop", total_budget=None, spent_to_date=None)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.strategy_for_element(a.id)

    row = result["initiatives"][0]
    assert row["budget_variance_pct"] is None
    assert row["budget_reason"] == "no_budget_recorded"


def test_sponsor_and_manager_fields_render_honestly(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("strategy-lens-sponsor")
    a = _element(db_session, org.id, "A")
    _initiative(
        db_session, a, name="Sponsored initiative",
        executive_sponsor="Jordan Sponsor", program_manager="Alex Manager",
    )
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.strategy_for_element(a.id)

    row = result["initiatives"][0]
    assert row["executive_sponsor"] == "Jordan Sponsor"
    assert row["program_manager"] == "Alex Manager"


def test_sponsor_absent_reads_as_honest_none_not_a_placeholder(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("strategy-lens-no-sponsor")
    a = _element(db_session, org.id, "A")
    _initiative(db_session, a, name="Unsponsored initiative", executive_sponsor=None)
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.strategy_for_element(a.id)

    assert result["initiatives"][0]["executive_sponsor"] is None


def test_success_metrics_are_nested_under_their_initiative(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("strategy-lens-metrics")
    a = _element(db_session, org.id, "A")
    initiative = _initiative(db_session, a, name="Measured initiative")
    _metric(db_session, initiative, metric_name="Adoption rate", target_value="90%", actual_value="60%")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.strategy_for_element(a.id)

    metrics = result["initiatives"][0]["success_metrics"]
    assert len(metrics) == 1
    assert metrics[0]["metric_name"] == "Adoption rate"
    assert metrics[0]["target_value"] == "90%"
    assert metrics[0]["actual_value"] == "60%"


def test_initiative_with_no_metrics_returns_empty_metrics_list(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("strategy-lens-no-metrics")
    a = _element(db_session, org.id, "A")
    _initiative(db_session, a, name="Metric-less initiative")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.strategy_for_element(a.id)

    assert result["initiatives"][0]["success_metrics"] == []


def test_initiative_blast_radius_reuses_cross_layer_impact(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("strategy-lens-blast")
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    _relationship(db_session, org.id, a, b)
    _initiative(db_session, a, name="A is being transformed")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        strategy_result = IntelligenceQueryService.strategy_for_element(a.id, max_depth=3)
        direct_impact = IntelligenceQueryService.cross_layer_impact(a.id, max_depth=3, with_owner=True)

    assert len(strategy_result["initiatives"]) == 1
    assert strategy_result["initiatives"][0]["affected_rows"] == direct_impact["rows"]


def test_multiple_initiatives_on_one_element_each_get_their_own_row(app, db_session, make_org):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org = make_org("strategy-lens-multi")
    a = _element(db_session, org.id, "A")
    _initiative(db_session, a, name="Phase 1 rollout")
    _initiative(db_session, a, name="Phase 2 rollout")
    db_session.commit()

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        result = IntelligenceQueryService.strategy_for_element(a.id)

    names = {i["name"] for i in result["initiatives"]}
    assert names == {"Phase 1 rollout", "Phase 2 rollout"}
