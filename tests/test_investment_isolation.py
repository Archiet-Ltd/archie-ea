"""
Test investment analysis tenant isolation.

Two-organisation test proving Investment Analysis reads only the
signed-in organisation's data.
"""
import pytest


def test_investment_analysis_api_isolation(db_session, make_org, tenant_ctx):
    """Investment analysis API returns only the active organisation's mappings.

    Proves:
    - Organisation A sees only its own application-capability mappings
    - Organisation B sees only its own mappings
    - No cross-tenant leakage in the investment priorities endpoint
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.modules.architecture.routes.architecture_routes import (
        _assemble_investment_priorities_context,
    )

    # Create two organisations
    org_a = make_org("Investment Org A")
    org_b = make_org("Investment Org B")

    # Create capabilities (shared across orgs, no organization_id)
    cap_finance = UnifiedCapability(name="Financial Management", code="FIN-001")
    cap_hr = UnifiedCapability(name="Human Resources", code="HR-001")
    db_session.add_all([cap_finance, cap_hr])
    db_session.commit()
    cap_finance_id = cap_finance.id
    cap_hr_id = cap_hr.id

    # Create applications and mappings for Org A
    with tenant_ctx(org_a.id):
        app_a1 = ApplicationComponent(
            name="Org A Finance App",
            organization_id=org_a.id,
            lifecycle_status="active",
        )
        app_a2 = ApplicationComponent(
            name="Org A HR App",
            organization_id=org_a.id,
            lifecycle_status="active",
        )
        db_session.add_all([app_a1, app_a2])
        db_session.commit()

        mapping_a1 = UnifiedApplicationCapabilityMapping(
            unified_capability_id=cap_finance_id,
            application_component_id=app_a1.id,
            support_level="full",
            coverage_percentage=90,
        )
        mapping_a2 = UnifiedApplicationCapabilityMapping(
            unified_capability_id=cap_hr_id,
            application_component_id=app_a2.id,
            support_level="full",
            coverage_percentage=85,
        )
        db_session.add_all([mapping_a1, mapping_a2])
        db_session.commit()

    # Create applications and mappings for Org B (3 mappings)
    with tenant_ctx(org_b.id):
        app_b1 = ApplicationComponent(
            name="Org B Finance App",
            organization_id=org_b.id,
            lifecycle_status="active",
        )
        app_b2 = ApplicationComponent(
            name="Org B HR App",
            organization_id=org_b.id,
            lifecycle_status="active",
        )
        app_b3 = ApplicationComponent(
            name="Org B Extra Finance App",
            organization_id=org_b.id,
            lifecycle_status="active",
        )
        db_session.add_all([app_b1, app_b2, app_b3])
        db_session.commit()

        mapping_b1 = UnifiedApplicationCapabilityMapping(
            unified_capability_id=cap_finance_id,
            application_component_id=app_b1.id,
            support_level="full",
            coverage_percentage=95,
        )
        mapping_b2 = UnifiedApplicationCapabilityMapping(
            unified_capability_id=cap_hr_id,
            application_component_id=app_b2.id,
            support_level="partial",
            coverage_percentage=60,
        )
        mapping_b3 = UnifiedApplicationCapabilityMapping(
            unified_capability_id=cap_finance_id,
            application_component_id=app_b3.id,
            support_level="partial",
            coverage_percentage=50,
        )
        db_session.add_all([mapping_b1, mapping_b2, mapping_b3])
        db_session.commit()

    # Check as Org A
    with tenant_ctx(org_a.id):
        analysis_a, count_a = _assemble_investment_priorities_context()
        assert count_a == 2, f"Org A should see 2 mappings, got {count_a}"

        if analysis_a is not None:
            # Financial Management should show 1 application for Org A
            finance_scores = [
                c for c in analysis_a["capability_scores"]
                if c["capability_name"] == "Financial Management"
            ]
            if finance_scores:
                assert finance_scores[0]["coverage_count"] == 1, (
                    f"Financial Management should have 1 app for Org A, "
                    f"got {finance_scores[0]['coverage_count']}"
                )

            # HR should show 1 application for Org A
            hr_scores = [
                c for c in analysis_a["capability_scores"]
                if c["capability_name"] == "Human Resources"
            ]
            if hr_scores:
                assert hr_scores[0]["coverage_count"] == 1, (
                    f"Human Resources should have 1 app for Org A, "
                    f"got {hr_scores[0]['coverage_count']}"
                )

    # Check as Org B
    with tenant_ctx(org_b.id):
        analysis_b, count_b = _assemble_investment_priorities_context()
        assert count_b == 3, f"Org B should see 3 mappings, got {count_b}"

        if analysis_b is not None:
            # Financial Management should show 2 applications for Org B
            finance_scores = [
                c for c in analysis_b["capability_scores"]
                if c["capability_name"] == "Financial Management"
            ]
            if finance_scores:
                assert finance_scores[0]["coverage_count"] == 2, (
                    f"Financial Management should have 2 apps for Org B, "
                    f"got {finance_scores[0]['coverage_count']}"
                )

            # HR should show 1 application for Org B
            hr_scores = [
                c for c in analysis_b["capability_scores"]
                if c["capability_name"] == "Human Resources"
            ]
            if hr_scores:
                assert hr_scores[0]["coverage_count"] == 1, (
                    f"Human Resources should have 1 app for Org B, "
                    f"got {hr_scores[0]['coverage_count']}"
                )
