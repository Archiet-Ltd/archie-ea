"""
Tests for appstable01: no Entelim route answers 500 because raw SQL reads an
`applications` table the schema does not have.

These tests verify that the three raw SQL reads against the missing `applications`
table have been replaced with the canonical application reader.
"""

import pytest

from app import db
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


class TestSharedReferenceCapability:
    """A shared reference capability is open to every organisation; an unclassified one is not."""

    def _shared(self, db_session, name, scope):
        from app.models.unified_capability import UnifiedCapability
        import uuid

        cap = UnifiedCapability(
            name=name,
            code=f"SHR{uuid.uuid4().hex[:6].upper()}",
            level=1,
            scope=scope,
            organization_id=None,
            strategic_importance="high",
        )
        db_session.add(cap)
        db_session.commit()
        return cap

    def test_shared_reference_capability_generates_for_any_organisation(
        self, db_session, make_org, client, login_as
    ):
        org_a, user_a = _org_with_user(db_session, make_org, "a")
        shared = self._shared(db_session, "Shared Reference Capability", "reference")

        login_as(client, user_a)
        response = client.post(
            "/api/roadmap/automation/generate-work-packages",
            json={"source_type": "capability", "source_id": shared.id},
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        names = [p.get("name", "") for p in response.get_json()["generated_packages"]]
        # Applications are linked to an organisation's own capabilities only,
        # so a shared reference capability yields just its own package.
        assert names == ["Implement Shared Reference Capability"]

    def test_unclassified_null_organisation_capability_is_not_found(
        self, db_session, make_org, client, login_as
    ):
        org_a, user_a = _org_with_user(db_session, make_org, "a")
        legacy = self._shared(db_session, "Unclassified Capability", None)

        login_as(client, user_a)
        response = client.post(
            "/api/roadmap/automation/generate-work-packages",
            json={"source_type": "capability", "source_id": legacy.id},
        )
        assert response.status_code == 404, response.get_data(as_text=True)


class TestEngineApplicationWorkPackage:
    """The older engine turns each linked application into a named work package."""

    def test_capability_with_linked_application_yields_application_package(
        self, db_session, make_org, tenant_ctx
    ):
        org_a, _ = _org_with_user(db_session, make_org, "a")
        cap = _capability(db_session, org_a, "Capability A")
        linked = _application(db_session, org_a, "Ledger Alpha")
        _link_app_cap(db_session, org_a, linked, cap)

        from app.services.roadmap_automation import RoadmapAutomationEngine

        with tenant_ctx(org_a.id):
            packages = RoadmapAutomationEngine().generate_work_packages(
                "capability", cap.id
            )

        by_source = [p for p in packages if p.get("source_type") == "application"]
        assert [p["name"] for p in by_source] == ["Modernize Ledger Alpha"]
        assert by_source[0]["source_id"] == linked.id


class TestOrphanArchiveIsFencedByOrganisation:
    """Archiving orphaned deliverables touches only the organisation it was given."""

    def test_second_organisation_orphan_is_left_alone(
        self, db_session, make_org, tenant_ctx
    ):
        org_a, user_a = _org_with_user(db_session, make_org, "a")
        org_b, user_b = _org_with_user(db_session, make_org, "b")
        app_a = _application(db_session, org_a, "Ledger Alpha")
        app_b = _application(db_session, org_b, "Ledger Beta")

        from app.models.roadmap_models import RoadmapDeliverable

        def deliverable(org, user, app, name):
            d = RoadmapDeliverable(
                name=name,
                status="planned",
                organization_id=org.id,
                created_by=user.id,
                source_application_id=app.id,
            )
            db_session.add(d)
            db_session.flush()
            return d

        d_a = deliverable(org_a, user_a, app_a, "Alpha deliverable")
        d_b = deliverable(org_b, user_b, app_b, "Beta deliverable")
        # Both applications are now deleted; the foreign key is switched off
        # for this transaction only so the rows are real orphans.
        db_session.execute(db.text("SET LOCAL session_replication_role = replica"))
        db_session.execute(
            db.text("DELETE FROM application_components WHERE id IN (:a, :b)"),
            {"a": app_a.id, "b": app_b.id},
        )
        db_session.execute(db.text("SET LOCAL session_replication_role = DEFAULT"))
        db_session.commit()

        from app.modules.solutions_strategic.v2.services.roadmap_sync import (
            RoadmapDataSync,
        )

        # The caller sits in organisation B's context but asks for A.
        with tenant_ctx(org_b.id):
            archived = RoadmapDataSync()._archive_orphaned_deliverables(org_a.id)

        assert archived == 0
        assert db_session.get(RoadmapDeliverable, d_b.id).status == "planned"
        assert db_session.get(RoadmapDeliverable, d_a.id).status == "planned"


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
        assert signals["vendor_support_status"] == "unknown"
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
