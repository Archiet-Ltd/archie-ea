"""
Tests for appstable01: no Entelim route answers 500 because raw SQL reads an
`applications` table the schema does not have.

These tests verify that the three raw SQL reads against the missing `applications`
table have been replaced with the canonical application reader.
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


def _link_app_cap(db_session, org, app, cap):
    """Link an application to a capability."""
    from app.services.capability_support import record_support_for_records

    record_support_for_records(org.id, app.id, cap.id, kind="realization")
    db_session.commit()


class TestGenerateWorkPackagesCapability:
    """Test POST /api/roadmap/automation/generate-work-packages with capability source."""

    def test_org_a_can_generate_work_packages_for_own_capability(
        self, db_session, make_org, client, login_as
    ):
        """Organisation A posts its own capability ID -> 2xx, generates work packages referencing org A's applications."""
        org_a, user_a = _org_with_user(db_session, make_org, "a")
        org_b, user_b = _org_with_user(db_session, make_org, "b")

        # Create capability and application for org A
        cap_a = _capability(db_session, org_a, "Capability A")
        app_a = _application(db_session, org_a, "Application A")
        _link_app_cap(db_session, org_a, app_a, cap_a)

        # Create capability for org B (should not be accessible)
        cap_b = _capability(db_session, org_b, "Capability B")
        app_b = _application(db_session, org_b, "Application B")
        _link_app_cap(db_session, org_b, app_b, cap_b)

        db_session.commit()

        # Login as org A user
        login_as(client, user_a)

        # POST to generate-work-packages with org A's capability
        response = client.post(
            "/api/roadmap/automation/generate-work-packages",
            json={"source_type": "capability", "source_id": cap_a.id},
        )

        # Should succeed with 2xx
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.get_data(as_text=True)}"

        data = response.get_json()
        assert "generated_packages" in data
        assert data["count"] >= 1

        # Verify generated packages reference org A's application
        generated = data["generated_packages"]
        app_names = [pkg.get("name", "") for pkg in generated]
        assert any("Application A" in name for name in app_names), f"Generated packages should reference org A's application: {app_names}"

        # Verify no reference to org B's application
        assert not any("Application B" in name for name in app_names), f"Generated packages should not reference org B's application: {app_names}"

    def test_org_b_cannot_use_org_a_capability(
        self, db_session, make_org, client, login_as
    ):
        """Organisation B posting org A's capability ID -> 404, generates nothing."""
        org_a, user_a = _org_with_user(db_session, make_org, "a")
        org_b, user_b = _org_with_user(db_session, make_org, "b")

        # Create capability and application for org A
        cap_a = _capability(db_session, org_a, "Capability A")
        app_a = _application(db_session, org_a, "Application A")
        _link_app_cap(db_session, org_a, app_a, cap_a)

        db_session.commit()

        # Login as org B user
        login_as(client, user_b)

        # POST to generate-work-packages with org A's capability ID
        response = client.post(
            "/api/roadmap/automation/generate-work-packages",
            json={"source_type": "capability", "source_id": cap_a.id},
        )

        # Should return 404 (capability not found in org B's scope)
        assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.get_data(as_text=True)}"


class TestRoadmapSyncApplications:
    """The application sync reads applications through the application model."""

    def test_sync_creates_deliverable_for_own_application_only(
        self, db_session, make_org, client, login_as
    ):
        org_a, user_a = _org_with_user(db_session, make_org, "a")
        org_b, user_b = _org_with_user(db_session, make_org, "b")
        _application(db_session, org_a, "Ledger Alpha")
        _application(db_session, org_b, "Ledger Beta")
        db_session.commit()

        login_as(client, user_a)
        response = client.post("/api/roadmap/sync/applications", json={})
        assert response.status_code == 200, response.get_data(as_text=True)

        result = response.get_json()["sync_result"]
        assert result["errors"] == []
        assert result["created"] == 1

        from app.models.roadmap_models import RoadmapDeliverable

        names = [
            d.name
            for d in RoadmapDeliverable.query.filter(
                RoadmapDeliverable.created_by == user_a.id
            ).all()
        ]
        assert "Ledger Alpha Modernization" in names
        assert not any("Ledger Beta" in n for n in names)


class TestPredictiveInsightsApplication:
    """The lifecycle prediction reads the application through the application model."""

    def test_prediction_reports_own_application_and_its_vendor_status(
        self, db_session, make_org, tenant_ctx
    ):
        org_a, _ = _org_with_user(db_session, make_org, "a")
        org_b, _ = _org_with_user(db_session, make_org, "b")
        app_a = _application(db_session, org_a, "Ledger Alpha")
        app_a.created_at = datetime(2020, 1, 1)
        app_a.vendor_risk = "high"
        app_b = _application(db_session, org_b, "Ledger Beta")
        app_b.created_at = datetime(2018, 1, 1)
        db_session.commit()

        from app.services.ai_chat_extensions.predictive_insights_service import (
            PredictiveInsightsService,
        )

        service = PredictiveInsightsService()
        with tenant_ctx(org_a.id):
            result = service.predict_application_lifecycle(app_id=app_a.id)
            other = service.predict_application_lifecycle(app_id=app_b.id)

        signals = result["signals_analyzed"]
        assert result["application_id"] == app_a.id
        assert signals["application_name"] == "Ledger Alpha"
        assert signals["vendor_support_status"] == "high"
        assert signals["age_years"] > 5
        # Another organisation's application is not read at all.
        assert "application_name" not in other["signals_analyzed"]
        assert other["signals_analyzed"]["vendor_support_status"] == "unknown"

    def test_application_with_no_vendor_fields_is_unknown(
        self, db_session, make_org, tenant_ctx
    ):
        org_a, _ = _org_with_user(db_session, make_org, "a")
        app_a = _application(db_session, org_a, "Ledger Alpha")
        app_a.created_at = datetime(2020, 1, 1)
        app_a.vendor_risk = None
        app_a.support_level = None
        app_a.vendor_type = None
        db_session.commit()

        from app.services.ai_chat_extensions.predictive_insights_service import (
            PredictiveInsightsService,
        )

        with tenant_ctx(org_a.id):
            result = PredictiveInsightsService().predict_application_lifecycle(
                app_id=app_a.id
            )
        signals = result["signals_analyzed"]
        assert signals["application_name"] == "Ledger Alpha"
        assert signals["vendor_support_status"] == "unknown"


class TestNoRawSQLOnApplicationsTable:
    """No code under app/ runs SQL against a table named `applications`."""

    def test_grep_no_applications_table_in_app(self):
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [
                "git", "grep", "-n", "-i", "-E",
                r"(select|insert|update|delete)\s+.*\s+(from|join)\s+applications\b"
                r"|^\s+(left\s+|inner\s+)?join\s+applications\b",
                "--", "app",
            ],
            capture_output=True,
            text=True,
            cwd=root,
        )
        assert result.stdout == "", f"raw SQL against 'applications':\n{result.stdout}"
        assert result.returncode == 1
