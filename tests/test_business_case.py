"""
Tests for the Business Case module — a consolidated Business Case artifact
that links to (but does not duplicate) StrategicInitiative budgets,
CapabilityCostAllocation cost rows, UnifiedCapability financials, and
Solution cost/ROI fields.

Covers:
- Model import + column shape (business_cases table; every new column
  nullable except organization_id).
- The module's register(app) + service helpers import cleanly.
- The blueprint registers on a bare Flask app (no full create_app() boot)
  and business_case.index / detail / field-save / pull-financials routes
  resolve — mirrors tests/test_business_model.py.
- aggregate_financials() pulling cost/value from linked
  StrategicInitiative / CapabilityCostAllocation / UnifiedCapability /
  Solution, isolated by the shared rollback fixture.
"""

import datetime
import uuid
from decimal import Decimal

import pytest


@pytest.fixture(scope="module", autouse=True)
def _full_app_boot(app):
    """Load the canonical test app before model-only tests configure mappers."""
    return app


def _make_org_and_user(db, suffix):
    from app.models.organization import Organization
    from app.models.user import User

    org = Organization(name=f"BizCase Test Org {suffix}", slug=f"bizcase-test-org-{suffix}")
    db.session.add(org)
    db.session.flush()

    user = User(email=f"bizcase-test-{suffix}@example.com", first_name="BizCase", last_name="Tester")
    db.session.add(user)
    db.session.flush()

    return org, user


class TestBusinessCaseModel:
    def test_model_importable(self):
        from app.models.business_case import BUSINESS_CASE_STATUSES, BusinessCase

        assert BusinessCase is not None
        assert BUSINESS_CASE_STATUSES == ("draft", "submitted", "approved", "rejected")

    def test_table_name(self):
        from app.models.business_case import BusinessCase

        assert BusinessCase.__tablename__ == "business_cases"

    def test_has_expected_columns(self):
        from app.models.business_case import BusinessCase

        columns = BusinessCase.__table__.columns.keys()
        for col in (
            "id",
            "organization_id",
            "title",
            "description",
            "status",
            "capability_id",
            "strategic_initiative_id",
            "solution_id",
            "problem_statement",
            "options_considered",
            "recommended_option",
            "expected_benefits",
            "financial_benefit_annual",
            "capex",
            "opex_annual",
            "tco_3yr",
            "roi_percentage",
            "payback_months",
            "key_risks",
            "created_by_id",
            "created_at",
            "updated_at",
        ):
            assert col in columns, f"missing column {col}"

    def test_only_organization_id_is_not_nullable(self):
        """Only organization_id (TenantMixin, defaulted via _default_org_id) may be
        NOT NULL — every column this module adds must be nullable so create_all()
        never conflicts with the reconcile-schema drift-repair step."""
        from app.models.business_case import BusinessCase

        table = BusinessCase.__table__
        for col in table.columns:
            # The primary key is inherently NOT NULL; organization_id is the
            # tenant discriminator (defaulted via _default_org_id). Every other
            # column this module adds must be nullable so create_all() never
            # conflicts with the reconcile-schema drift-repair step.
            if col.name == "organization_id" or col.primary_key:
                continue
            assert col.nullable is True, f"{col.name} must be nullable"

    def test_to_dict_shape(self):
        from app.models.business_case import BusinessCase

        bc = BusinessCase(title="Test Case", status="draft")
        data = bc.to_dict()
        assert data["title"] == "Test Case"
        assert data["status"] == "draft"
        assert data["capex"] is None
        assert data["tco_3yr"] is None
        assert data["roi_percentage"] is None


class TestBusinessCaseModule:
    def test_module_register_function_importable(self):
        from app.modules.business_case import business_case_bp, register

        assert callable(register)
        assert business_case_bp.name == "business_case"
        assert business_case_bp.url_prefix == "/business-case"

    def test_service_importable(self):
        from app.modules.business_case import service

        assert hasattr(service, "list_business_cases")
        assert hasattr(service, "get_business_case_or_none")
        assert hasattr(service, "create_business_case")
        assert hasattr(service, "update_business_case")
        assert hasattr(service, "delete_business_case")
        assert hasattr(service, "save_field")
        assert hasattr(service, "aggregate_financials")
        assert hasattr(service, "refresh_financials_from_links")


class TestBusinessCaseBlueprintRoutes:
    """Register the blueprint on a bare Flask app (no full create_app() boot)
    to verify routing without paying for the whole ORM/app-factory import."""

    @pytest.fixture(scope="module")
    def bare_app(self):
        from flask import Flask

        from app.modules.business_case.routes import business_case_bp

        flask_app = Flask(__name__)
        flask_app.register_blueprint(business_case_bp)
        return flask_app

    def test_index_endpoint_resolves(self, bare_app):
        with bare_app.test_request_context():
            from flask import url_for

            assert url_for("business_case.index") == "/business-case/"

    def test_detail_endpoint_resolves(self, bare_app):
        with bare_app.test_request_context():
            from flask import url_for

            assert url_for("business_case.detail", business_case_id=1) == "/business-case/1"

    def test_field_api_endpoint_registered_with_post_and_put(self, bare_app):
        methods = set()
        found = False
        for r in bare_app.url_map.iter_rules():
            if r.endpoint == "business_case.save_field":
                found = True
                methods |= set(r.methods or [])
        assert found, "business-case/<id>/api/field route not registered"
        assert "POST" in methods
        assert "PUT" in methods

    def test_create_update_delete_routes_registered(self, bare_app):
        endpoints = {r.endpoint for r in bare_app.url_map.iter_rules()}
        assert "business_case.create" in endpoints
        assert "business_case.update" in endpoints
        assert "business_case.delete" in endpoints

    def test_pull_financials_route_registered(self, bare_app):
        endpoints = {r.endpoint for r in bare_app.url_map.iter_rules()}
        assert "business_case.pull_financials" in endpoints


class TestAggregateFinancials:
    """aggregate_financials() pulling from linked StrategicInitiative /
    CapabilityCostAllocation / UnifiedCapability / Solution."""

    def test_aggregate_financials_pulls_from_all_linked_sources(self, app, db_session):
        from app import db
        from app.models.business_capabilities import BusinessCapability
        from app.models.business_case import BusinessCase
        from app.models.cost_intelligence import CapabilityCostAllocation
        from app.models.solution_models import Solution
        from app.models.strategic import StrategicInitiative
        from app.models.unified_capability import UnifiedCapability
        from app.modules.business_case import service

        # Request context + g.current_org_id so the tenant-scoped ArchiMateElement
        # rows auto-created by seeded entities' listeners (Solution, etc.) resolve
        # organization_id on a fresh NOT-NULL schema (local DBs had it backfilled
        # nullable by reconcile-schema, which masked this).
        with app.test_request_context():
            suffix = uuid.uuid4().hex[:8]
            org, user = _make_org_and_user(db, suffix)
            from flask import g
            g.current_org_id = org.id

            initiative = StrategicInitiative(
                name=f"Initiative {suffix}",
                organization_id=org.id,
                budget_allocated=100000.0,
                budget_spent=40000.0,
                business_value_score=8,
            )
            db.session.add(initiative)

            capability = BusinessCapability(name=f"Capability {suffix}")
            db.session.add(capability)
            db.session.flush()

            allocation = CapabilityCostAllocation(
                capability_id=capability.id,
                organization_id=org.id,
                fiscal_year=2026,
                period_start_date=datetime.date(2026, 1, 1),
                period_end_date=datetime.date(2026, 12, 31),
                software_licensing_cost=Decimal("10000.00"),
                infrastructure_hosting_cost=Decimal("5000.00"),
                business_value_generated=Decimal("50000.00"),
            )
            db.session.add(allocation)

            # Inserting the BusinessCapability projected it into unified_capabilities
            # (ADR 0008 write-time sync). aggregate_financials reaches that row by its
            # provenance, so give the projected row the costs rather than inserting a
            # second capability under a pinned id -- a pinned id collides with the
            # projection's own sequence-assigned row on a fresh database.
            unified = UnifiedCapability.query.filter_by(
                source_table="business_capability", source_id=str(capability.id)
            ).one()
            unified.annual_cost = 20000.0
            unified.roi_percentage = 15.0

            solution = Solution(
                name=f"Solution {suffix}",
                organization_id=org.id,
                estimated_cost=Decimal("30000.00"),
                actual_cost=Decimal("35000.00"),
                roi_percentage=25.0,
            )
            db.session.add(solution)
            db.session.flush()

            business_case = BusinessCase(
                title=f"Business Case {suffix}",
                organization_id=org.id,
                capability_id=capability.id,
                strategic_initiative_id=initiative.id,
                solution_id=solution.id,
                created_by_id=user.id,
            )
            db.session.add(business_case)
            db.session.commit()

            report = service.aggregate_financials(business_case, apply_missing=True)

            # Every source resolved.
            assert report["strategic_initiative"] is not None
            assert report["capability_cost_allocation"] is not None
            assert report["unified_capability"] is not None
            assert report["unified_capability"]["id"] == unified.id
            assert report["solution"] is not None

            # capex candidates: initiative.budget_allocated=100000, solution.actual_cost=35000 -> max = 100000
            assert business_case.capex == Decimal("100000")
            # opex candidates: allocation total_cost=15000, unified.annual_cost=20000 -> max = 20000
            assert business_case.opex_annual == Decimal("20000")
            # tco = capex + 3*opex = 100000 + 60000 = 160000
            assert business_case.tco_3yr == Decimal("160000")
            # benefit = allocation.business_value_generated = 50000
            assert business_case.financial_benefit_annual == Decimal("50000")
            # roi = average(allocation roi, unified roi=15, solution roi=25)
            assert business_case.roi_percentage is not None

            assert set(report["applied_fields"]) == {
                "capex",
                "opex_annual",
                "tco_3yr",
                "roi_percentage",
                "financial_benefit_annual",
            }
            assert report["cross_checks"] == {}

    def test_aggregate_financials_is_fault_tolerant_with_no_links(self, app, db_session):
        from app import db
        from app.models.business_case import BusinessCase
        from app.modules.business_case import service

        with app.app_context():
            suffix = uuid.uuid4().hex[:8]
            org, user = _make_org_and_user(db, suffix)

            business_case = BusinessCase(
                title=f"Unlinked Case {suffix}",
                organization_id=org.id,
                created_by_id=user.id,
            )
            db.session.add(business_case)
            db.session.commit()

            report = service.aggregate_financials(business_case)
            assert report["strategic_initiative"] is None
            assert report["capability_cost_allocation"] is None
            assert report["unified_capability"] is None
            assert report["solution"] is None
            assert report["applied_fields"] == []
            # Nothing to pull, so user-entered (here: unset) values are left alone.
            assert business_case.capex is None
            assert business_case.tco_3yr is None

    def test_aggregate_financials_does_not_overwrite_user_entered_values(
        self, app, db_session
    ):
        """Fields the user already filled in must be left untouched; a
        cross-check delta is reported instead."""
        from app import db
        from app.models.business_case import BusinessCase
        from app.models.strategic import StrategicInitiative
        from app.modules.business_case import service

        # Request context + g.current_org_id so the tenant-scoped ArchiMateElement
        # rows auto-created by seeded entities' listeners (Solution, etc.) resolve
        # organization_id on a fresh NOT-NULL schema (local DBs had it backfilled
        # nullable by reconcile-schema, which masked this).
        with app.test_request_context():
            suffix = uuid.uuid4().hex[:8]
            org, user = _make_org_and_user(db, suffix)
            from flask import g
            g.current_org_id = org.id

            initiative = StrategicInitiative(
                name=f"Initiative {suffix}",
                organization_id=org.id,
                budget_allocated=100000.0,
            )
            db.session.add(initiative)
            db.session.flush()

            business_case = BusinessCase(
                title=f"Manually Entered Case {suffix}",
                organization_id=org.id,
                strategic_initiative_id=initiative.id,
                capex=Decimal("5000.00"),  # user already entered a value
                created_by_id=user.id,
            )
            db.session.add(business_case)
            db.session.commit()

            report = service.aggregate_financials(business_case)
            # Untouched — still the user's value, not the initiative's 100000.
            assert business_case.capex == Decimal("5000.00")
            assert "capex" not in report["applied_fields"]
            assert report["cross_checks"]["capex"]["current"] == 5000.0
            assert report["cross_checks"]["capex"]["suggested"] == 100000.0


class TestBusinessCaseDetailTemplate:
    """Canvas/framework UI fix: the three linked drop-downs must read "Not linked"
    instead of "None" when no link is selected."""

    def test_detail_template_uses_not_linked(self):
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..", "app", "templates", "business_case", "detail.html"
        )
        with open(path) as fh:
            content = fh.read()
        assert ">Not linked<" in content, "detail.html must use 'Not linked' not 'None'"
        assert ">None<" not in content, "detail.html must not contain 'None' as an option"
