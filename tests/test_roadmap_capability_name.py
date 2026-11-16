"""
Tests for appstable02: _get_primary_capability_for_application returns the
correct capability name from the application-capability mapping.

These tests verify that the raw SQL querying a non-existent column has been
replaced with an ORM query using ApplicationCapabilityMapping.
"""

import pytest
from datetime import datetime


def _org_with_user(db_session, make_org, suffix):
    """Create an organisation with a user."""
    import uuid
    from app.models.user import Role, User
    from app.models.organization import Organization

    org = Organization(name=f"Org {suffix} {uuid.uuid4().hex[:6]}", slug=f"org-{suffix}-{uuid.uuid4().hex[:6]}")
    db_session.add(org)
    db_session.flush()

    Role.insert_roles()
    user = User(
        email=f"test.{suffix}.{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name=suffix.capitalize(),
        organization_id=org.id,
        enterprise_role="solution_architect",
        confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "TestPass123!"
    db_session.add(user)
    db_session.commit()
    return org, user


def _capability(db_session, org, name):
    """Create a capability for an organisation."""
    from app.models.unified_capability import UnifiedCapability
    import uuid

    cap = UnifiedCapability(
        name=name,
        code=f"CAP{uuid.uuid4().hex[:6].upper()}",
        level=1,
        scope="tenant",
        organization_id=org.id,
        strategic_importance="high",
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def _application(db_session, org, name):
    """Create an application for an organisation."""
    from app.models.application_portfolio import ApplicationComponent
    import uuid

    app = ApplicationComponent(
        name=name,
        application_code=f"APP{uuid.uuid4().hex[:6].upper()}",
        organization_id=org.id,
        criticality="high",
        technology_stack="Python, PostgreSQL",
    )
    db_session.add(app)
    db_session.flush()
    return app


def _link_app_cap(db_session, org, app, cap, is_primary=True):
    """Link an application to a capability using ApplicationCapabilityMapping."""
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.business_capabilities import BusinessCapability

    # The test creates a UnifiedCapability, but ApplicationCapabilityMapping references BusinessCapability.
    # We need to create a BusinessCapability with the same name for the mapping.
    biz_cap = BusinessCapability(
        name=cap.name,
        code=f"BIZ{cap.code[3:]}" if cap.code.startswith("CAP") else f"BIZ{cap.code}",
        level=cap.level,
        category=cap.category or "core",
        business_domain=cap.domain_id and "test" or "test",
        organization_id=org.id,
        strategic_importance=cap.strategic_importance,
    )
    db_session.add(biz_cap)
    db_session.flush()

    mapping = ApplicationCapabilityMapping(
        organization_id=org.id,
        application_component_id=app.id,
        business_capability_id=biz_cap.id,
        is_primary_enabler=is_primary,
        support_level="full",
        coverage_percentage=100,
    )
    db_session.add(mapping)
    db_session.commit()
    return mapping


class TestGetPrimaryCapabilityForApplication:
    """Test _get_primary_capability_for_application in both roadmap_automation copies."""

    def test_mapped_application_returns_capability_name_in_both_copies(
        self, db_session, make_org, tenant_ctx
    ):
        """(a) An application mapped to capability 'Payments' in organisation A returns 'Payments' in both copies."""
        org_a, user_a = _org_with_user(db_session, make_org, "a")
        org_b, user_b = _org_with_user(db_session, make_org, "b")

        # Create capability "Payments" in org A
        cap_payments = _capability(db_session, org_a, "Payments")
        app_a = _application(db_session, org_a, "Payment App")
        _link_app_cap(db_session, org_a, app_a, cap_payments, is_primary=True)

        # Create different capability in org B
        cap_billing = _capability(db_session, org_b, "Billing")
        app_b = _application(db_session, org_b, "Billing App")
        _link_app_cap(db_session, org_b, app_b, cap_billing, is_primary=True)

        db_session.commit()

        # Test v1 copy (app.services.roadmap_automation)
        from app.services.roadmap_automation import RoadmapAutomationEngine as V1Engine
        with tenant_ctx(org_a.id):
            v1_result = V1Engine()._get_primary_capability_for_application(app_a.id)
        assert v1_result == "Payments", f"V1 expected 'Payments', got '{v1_result}'"

        # Test v2 copy (app.modules.solutions_strategic.v2.services.roadmap_automation)
        from app.modules.solutions_strategic.v2.services.roadmap_automation import RoadmapAutomationEngine as V2Engine
        with tenant_ctx(org_a.id):
            v2_result = V2Engine()._get_primary_capability_for_application(app_a.id)
        assert v2_result == "Payments", f"V2 expected 'Payments', got '{v2_result}'"

    def test_same_application_id_from_different_org_context_does_not_leak(
        self, db_session, make_org, tenant_ctx
    ):
        """(b) The same application ID asked from organisation B's context does not return A's capability."""
        org_a, user_a = _org_with_user(db_session, make_org, "a")
        org_b, user_b = _org_with_user(db_session, make_org, "b")

        # Create capability "Payments" in org A
        cap_payments = _capability(db_session, org_a, "Payments")
        app_a = _application(db_session, org_a, "Payment App")
        _link_app_cap(db_session, org_a, app_a, cap_payments, is_primary=True)

        # Create different capability in org B
        cap_billing = _capability(db_session, org_b, "Billing")
        app_b = _application(db_session, org_b, "Billing App")
        _link_app_cap(db_session, org_b, app_b, cap_billing, is_primary=True)

        db_session.commit()

        # Ask for app_a.id from org B's context - should NOT return "Payments"
        from app.services.roadmap_automation import RoadmapAutomationEngine as V1Engine
        from app.modules.solutions_strategic.v2.services.roadmap_automation import RoadmapAutomationEngine as V2Engine

        with tenant_ctx(org_b.id):
            v1_result = V1Engine()._get_primary_capability_for_application(app_a.id)
            v2_result = V2Engine()._get_primary_capability_for_application(app_a.id)

        # Both should return fallback since app_a is not mapped in org B
        assert v1_result == "unknown_capability", f"V1 from org B context expected fallback, got '{v1_result}'"
        assert v2_result == "unknown_capability", f"V2 from org B context expected fallback, got '{v2_result}'"

    def test_roadmap_sync_creates_deliverable_with_correct_business_capability(
        self, db_session, make_org, client, login_as, tenant_ctx
    ):
        """(c) Roadmap sync creates a deliverable whose business_capability is 'Payments'."""
        org_a, user_a = _org_with_user(db_session, make_org, "a")

        # Create capability "Payments" in org A
        cap_payments = _capability(db_session, org_a, "Payments")
        app_a = _application(db_session, org_a, "Payment App")
        _link_app_cap(db_session, org_a, app_a, cap_payments, is_primary=True)

        db_session.commit()

        login_as(client, user_a)

        # Trigger application sync which calls _get_primary_capability_for_application
        response = client.post("/api/roadmap/sync/applications", json={})
        assert response.status_code == 200, response.get_data(as_text=True)

        result = response.get_json()["sync_result"]
        assert result["errors"] == []
        assert result["created"] == 1

        # Verify the deliverable has the correct business_capability
        from app.models.roadmap_models import RoadmapDeliverable, RoadmapWorkPackage

        deliverables = RoadmapDeliverable.query.filter(
            RoadmapDeliverable.created_by == user_a.id
        ).all()
        assert len(deliverables) == 1
        deliverable = deliverables[0]
        assert deliverable.name == "Payment App Modernization"

        # Check the associated work package's business_capability
        wp = RoadmapWorkPackage.query.get(deliverable.work_package_id)
        assert wp is not None
        assert wp.business_capability == "Payments", f"Expected 'Payments', got '{wp.business_capability}'"

    def test_application_with_no_mapping_returns_fallback(
        self, db_session, make_org, tenant_ctx
    ):
        """(d) An application with no mapping returns today's fallback ('unknown_capability')."""
        org_a, user_a = _org_with_user(db_session, make_org, "a")

        # Create application with NO capability mapping
        app_a = _application(db_session, org_a, "Unmapped App")
        db_session.commit()

        from app.services.roadmap_automation import RoadmapAutomationEngine as V1Engine
        from app.modules.solutions_strategic.v2.services.roadmap_automation import RoadmapAutomationEngine as V2Engine

        with tenant_ctx(org_a.id):
            v1_result = V1Engine()._get_primary_capability_for_application(app_a.id)
            v2_result = V2Engine()._get_primary_capability_for_application(app_a.id)

        assert v1_result == "unknown_capability", f"V1 expected fallback, got '{v1_result}'"
        assert v2_result == "unknown_capability", f"V2 expected fallback, got '{v2_result}'"

def _mapping_whose_id_matches_another_unified_capability(db_session, org):
    """An application mapped to business capability 'Payments' whose id equals
    the id of an unrelated unified capability named 'Decoy'."""
    from sqlalchemy import func
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability
    import uuid

    shared_id = (
        max(
            db_session.query(func.coalesce(func.max(UnifiedCapability.id), 0)).scalar(),
            db_session.query(func.coalesce(func.max(BusinessCapability.id), 0)).scalar(),
        )
        + 1000
    )
    db_session.add(
        UnifiedCapability(
            id=shared_id, name="Decoy", code=f"DEC{uuid.uuid4().hex[:6].upper()}",
            level=1, scope="tenant", organization_id=org.id, strategic_importance="high",
        )
    )
    db_session.add(
        BusinessCapability(
            id=shared_id, name="Payments", code=f"BIZ{uuid.uuid4().hex[:6].upper()}",
            level=1, category="core", business_domain="test", organization_id=org.id,
            strategic_importance="high",
        )
    )
    db_session.flush()
    app_a = _application(db_session, org, "Payment App")
    db_session.add(
        ApplicationCapabilityMapping(
            organization_id=org.id, application_component_id=app_a.id,
            business_capability_id=shared_id, is_primary_enabler=True,
            support_level="full", coverage_percentage=100,
        )
    )
    db_session.commit()
    return app_a


class TestCapabilityJoinUsesBusinessCapability:
    """The mapping's business_capability_id points at business capabilities, so a
    unified capability that happens to share the number must not be returned."""

    @pytest.mark.parametrize(
        "module",
        [
            "app.services.roadmap_automation:RoadmapAutomationEngine",
            "app.modules.solutions_strategic.v2.services.roadmap_automation:RoadmapAutomationEngine",
            "app.services.roadmap_sync:RoadmapDataSync",
            "app.modules.solutions_strategic.v2.services.roadmap_sync:RoadmapDataSync",
        ],
    )
    def test_name_comes_from_business_capability_in_every_copy(
        self, db_session, make_org, tenant_ctx, module
    ):
        import importlib

        org_a, _ = _org_with_user(db_session, make_org, "a")
        org_b, _ = _org_with_user(db_session, make_org, "b")
        app_a = _mapping_whose_id_matches_another_unified_capability(db_session, org_a)

        path, cls = module.split(":")
        engine = getattr(importlib.import_module(path), cls)()
        with tenant_ctx(org_a.id):
            assert engine._get_primary_capability_for_application(app_a.id) == "Payments"
        with tenant_ctx(org_b.id):
            assert engine._get_primary_capability_for_application(app_a.id) == "unknown_capability"
