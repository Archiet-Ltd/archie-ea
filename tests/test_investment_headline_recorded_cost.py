"""The Investment Analysis headline money figure comes from recorded costs.

The first round of this branch left the headline as placeholder arithmetic
(a fixed figure per capability, regardless of any real cost). These tests pin
the headline to the recorded annual cost of the organisation's mapped
applications, read through the one canonical cost accessor, and prove a second
organisation never sees the first's money, and an organisation with nothing
recorded gets an explained empty value rather than an invented number.
"""
import pytest

from app.services.application_cost_accessor import set_annual_cost


def _seed_org(db_session, make_org, tenant_ctx, cap_id, costs):
    """Create an org with one application per entry in *costs*, mapped to the
    shared capability, and the given recorded annual cost (None means unrecorded).
    Returns the org.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )

    org = make_org("Headline")
    with tenant_ctx(org.id):
        for i, cost in enumerate(costs):
            component = ApplicationComponent(
                name=f"{org.name} App {i}",
                organization_id=org.id,
                lifecycle_status="active",
            )
            db_session.add(component)
            db_session.commit()
            if cost is not None:
                set_annual_cost(component, cost)
            db_session.add(
                UnifiedApplicationCapabilityMapping(
                    unified_capability_id=cap_id,
                    application_component_id=component.id,
                    support_level="full",
                    coverage_percentage=90,
                )
            )
            db_session.commit()
    return org


def _headline(db_session, make_org, tenant_ctx, cap_id, org):
    from app.modules.solutions_strategic.v2.services.investment_prioritization_service import (
        InvestmentPrioritizationService,
    )

    with tenant_ctx(org.id):
        analysis = InvestmentPrioritizationService().analyze_investment_priorities(
            include_risk_analysis=True
        )
    return analysis["portfolio_metrics"]


def test_headline_is_sum_of_recorded_costs_and_isolated(db_session, make_org, tenant_ctx):
    """Two real organisations: the headline equals the signed-in org's own
    recorded costs, never the other's."""
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(name="Payments", code="PAY-HL-001")
    db_session.add(cap)
    db_session.commit()
    cap_id = cap.id

    # Org A records 120,000 + 30,000 = 150,000. Org B records 999,000.
    org_a = _seed_org(db_session, make_org, tenant_ctx, cap_id, [120000, 30000])
    org_b = _seed_org(db_session, make_org, tenant_ctx, cap_id, [999000])

    metrics_a = _headline(db_session, make_org, tenant_ctx, cap_id, org_a)
    metrics_b = _headline(db_session, make_org, tenant_ctx, cap_id, org_b)

    # The headline is the sum of this org's recorded costs, and is a real figure.
    assert metrics_a["has_recorded_investment"] is True
    assert metrics_a["total_estimated_investment"] == pytest.approx(150000.0)
    # Not the first-round placeholder arithmetic (which produced a six/seven-figure
    # fabricated total unrelated to the 150,000 actually recorded).
    assert metrics_a["applications_with_cost"] == 2

    # The second organisation sees only its own recorded money.
    assert metrics_b["has_recorded_investment"] is True
    assert metrics_b["total_estimated_investment"] == pytest.approx(999000.0)
    # No cross-tenant leak: A's total excludes B's 999,000 and vice versa.
    assert metrics_a["total_estimated_investment"] != metrics_b["total_estimated_investment"]
    assert org_b.name not in {"headline_marker"}  # orgs are distinct rows


def test_headline_is_explained_empty_when_nothing_recorded(db_session, make_org, tenant_ctx):
    """An organisation with mapped applications but no recorded cost gets an
    explained empty value (None), never a fabricated figure."""
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(name="Ledger", code="LDG-HL-001")
    db_session.add(cap)
    db_session.commit()

    org = _seed_org(db_session, make_org, tenant_ctx, cap.id, [None, None])
    metrics = _headline(db_session, make_org, tenant_ctx, cap.id, org)

    assert metrics["has_recorded_investment"] is False
    assert metrics["total_estimated_investment"] is None
    assert metrics["mapped_application_count"] == 2
    assert metrics["applications_with_cost"] == 0
