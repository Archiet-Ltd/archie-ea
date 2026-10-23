"""
Unit tests for investment prioritization service tenant isolation.
"""
import pytest


def test_investment_service_scopes_mappings_by_org(db_session, make_org, tenant_ctx):
    """InvestmentPrioritizationService.analyze_investment_priorities() reads only
    the active organisation's application-capability mappings.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.modules.solutions_strategic.v2.services.investment_prioritization_service import (
        InvestmentPrioritizationService,
    )

    # Create two organisations
    org_a = make_org("Service Org A")
    org_b = make_org("Service Org B")

    # Create a shared capability
    cap = UnifiedCapability(name="Test Capability", code="TEST-001")
    db_session.add(cap)
    db_session.commit()
    cap_id = cap.id

    # Create 3 mappings for Org A
    with tenant_ctx(org_a.id):
        apps_a = []
        for i in range(3):
            app = ApplicationComponent(
                name=f"Org A App {i+1}",
                organization_id=org_a.id,
                lifecycle_status="active",
            )
            apps_a.append(app)
        db_session.add_all(apps_a)
        db_session.commit()

        for app in apps_a:
            mapping = UnifiedApplicationCapabilityMapping(
                unified_capability_id=cap_id,
                application_component_id=app.id,
            )
            db_session.add(mapping)
        db_session.commit()

    # Create 7 mappings for Org B
    with tenant_ctx(org_b.id):
        apps_b = []
        for i in range(7):
            app = ApplicationComponent(
                name=f"Org B App {i+1}",
                organization_id=org_b.id,
                lifecycle_status="active",
            )
            apps_b.append(app)
        db_session.add_all(apps_b)
        db_session.commit()

        for app in apps_b:
            mapping = UnifiedApplicationCapabilityMapping(
                unified_capability_id=cap_id,
                application_component_id=app.id,
            )
            db_session.add(mapping)
        db_session.commit()

    # Analyze investment priorities as Org A
    with tenant_ctx(org_a.id):
        service = InvestmentPrioritizationService()
        analysis_a = service.analyze_investment_priorities(include_risk_analysis=True)

        # Find the test capability in the results
        test_cap_scores = [
            c for c in analysis_a["capability_scores"]
            if c["capability_name"] == "Test Capability"
        ]
        assert len(test_cap_scores) == 1, "Test Capability should appear once"

        # Org A should see exactly 3 applications (its own)
        coverage_a = test_cap_scores[0]["coverage_count"]
        assert coverage_a == 3, (
            f"Org A should see 3 application mappings, got {coverage_a}"
        )

    # Analyze investment priorities as Org B
    with tenant_ctx(org_b.id):
        service = InvestmentPrioritizationService()
        analysis_b = service.analyze_investment_priorities(include_risk_analysis=True)

        # Find the test capability in the results
        test_cap_scores = [
            c for c in analysis_b["capability_scores"]
            if c["capability_name"] == "Test Capability"
        ]
        assert len(test_cap_scores) == 1, "Test Capability should appear once"

        # Org B should see exactly 7 applications (its own)
        coverage_b = test_cap_scores[0]["coverage_count"]
        assert coverage_b == 7, (
            f"Org B should see 7 application mappings, got {coverage_b}"
        )


def test_investment_context_assembly_scopes_mappings(db_session, make_org, tenant_ctx):
    """_assemble_investment_priorities_context() counts only the active
    organisation's mappings.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

    # Create two organisations
    org_a = make_org("Context Org A")
    org_b = make_org("Context Org B")

    # Create capability
    cap = UnifiedCapability(name="Context Capability", code="CTX-001")
    db_session.add(cap)
    db_session.commit()
    cap_id = cap.id

    # Create 2 mappings for Org A
    with tenant_ctx(org_a.id):
        for i in range(2):
            app = ApplicationComponent(
                name=f"Context App A{i+1}",
                organization_id=org_a.id,
                lifecycle_status="active",
            )
            db_session.add(app)
            db_session.commit()

            mapping = UnifiedApplicationCapabilityMapping(
                unified_capability_id=cap_id,
                application_component_id=app.id,
            )
            db_session.add(mapping)
        db_session.commit()

    # Create 4 mappings for Org B
    with tenant_ctx(org_b.id):
        for i in range(4):
            app = ApplicationComponent(
                name=f"Context App B{i+1}",
                organization_id=org_b.id,
                lifecycle_status="active",
            )
            db_session.add(app)
            db_session.commit()

            mapping = UnifiedApplicationCapabilityMapping(
                unified_capability_id=cap_id,
                application_component_id=app.id,
            )
            db_session.add(mapping)
        db_session.commit()

    # Import the function under test
    from app.modules.architecture.routes.architecture_routes import (
        _assemble_investment_priorities_context,
    )

    # Check as Org A
    with tenant_ctx(org_a.id):
        analysis_a, count_a = _assemble_investment_priorities_context()
        assert count_a == 2, f"Org A should see 2 mappings, got {count_a}"

    # Check as Org B
    with tenant_ctx(org_b.id):
        analysis_b, count_b = _assemble_investment_priorities_context()
        assert count_b == 4, f"Org B should see 4 mappings, got {count_b}"
