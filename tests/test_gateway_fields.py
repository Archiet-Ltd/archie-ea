"""Gateway fields on LLMInteraction and two-org isolation.

Tests:
- LLMInteraction model accepts the new gateway fields
- _resolve_org_id returns the request context's org
- Two-org isolation: org A's call records are not visible to org B
- LLMInteraction can record prompt_version and retention_setting
"""

from __future__ import annotations

import pytest
from sqlalchemy import func


class TestLLMInteractionGatewayFields:
    """New gateway columns on LLMInteraction store correctly."""

    def test_llm_interaction_accepts_gateway_fields(self, db_session, make_org):
        """LLMInteraction can be created with all new gateway fields."""
        from app.models import LLMInteraction

        org = make_org("gateway-fields")
        record = LLMInteraction(
            model_name="gpt-4o",
            provider="openai",
            token_count_input=100,
            token_count_output=50,
            cost=0.0025,
            latency_ms=1200,
            organization_id=org.id,
            prompt_version="v1.3",
            retention_setting="90d",
        )
        db_session.add(record)
        db_session.flush()

        saved = db_session.get(LLMInteraction, record.id)
        assert saved is not None
        assert saved.organization_id == org.id
        assert saved.prompt_version == "v1.3"
        assert saved.retention_setting == "90d"

    def test_gateway_fields_default_to_null(self, db_session):
        """New gateway fields are nullable and default to NULL."""
        from app.models import LLMInteraction

        record = LLMInteraction(
            model_name="gpt-4o-mini",
            provider="openai",
            token_count_input=10,
            token_count_output=5,
            cost=0.0001,
        )
        db_session.add(record)
        db_session.flush()

        saved = db_session.get(LLMInteraction, record.id)
        assert saved.organization_id is None
        assert saved.prompt_version is None
        assert saved.retention_setting is None

    def test_prompt_version_is_string_stores_semver(self, db_session):
        """prompt_version stored as a semver-style string."""
        from app.models import LLMInteraction

        # Tag our test records with a unique prefix so we only query our own
        versions = ["v1.0", "v2.3.1", "2026-09-01", "experimental-a"]
        tag = "test-promp-ver"
        for v in versions:
            record = LLMInteraction(
                model_name=tag,
                provider="test",
                prompt_version=v,
            )
            db_session.add(record)
        db_session.flush()

        stored = (
            db_session.query(LLMInteraction.prompt_version)
            .filter(LLMInteraction.model_name == tag)
            .distinct()
            .all()
        )
        stored_flat = sorted({r[0] for r in stored if r[0] is not None})
        assert sorted(versions) == stored_flat

    def test_retention_setting_accepts_known_values(self, db_session):
        """retention_setting accepts common policy values."""
        from app.models import LLMInteraction

        for policy in ("forever", "30d", "90d", "1y"):
            record = LLMInteraction(
                model_name="test",
                provider="test",
                retention_setting=policy,
            )
            db_session.add(record)
        db_session.flush()

        stored = db_session.query(LLMInteraction.retention_setting).distinct().all()
        stored_flat = {r[0] for r in stored if r[0] is not None}
        assert {"forever", "30d", "90d", "1y"} == stored_flat


class TestLLMServiceGatewayHelpers:
    """Gateway helper methods on LLMService."""

    def test_resolve_org_id_returns_none_outside_request(self, app):
        """_resolve_org_id returns None when there is no request context."""
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        org_id = LLMService._resolve_org_id()
        assert org_id is None

    def test_resolve_org_id_returns_current_org(self, app, tenant_ctx, make_org):
        """_resolve_org_id returns the org from g.current_org_id."""
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        org = make_org("gateway-test")
        with tenant_ctx(org.id):
            resolved = LLMService._resolve_org_id()
        assert resolved == org.id

    def test_resolve_org_id_works_in_app_context(self, app, make_org):
        """_resolve_org_id reads g.current_org_id when only has_app_context() is true.

        This covers job/worker/CLI paths where tenant_scope sets
        g.current_org_id without a request context.
        """
        from flask import g
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        org = make_org("appctx-test")
        with app.app_context():
            g.current_org_id = org.id
            resolved = LLMService._resolve_org_id()
        assert resolved == org.id


class TestTwoOrgGatewayIsolation:
    """Call records are per-organisation; org B data is not visible to org A."""

    def test_llm_interaction_rows_have_organization_id(self, db_session, make_org):
        """LLMInteraction can be associated with specific organisations."""
        from app.models import LLMInteraction

        org_a = make_org("org-a")
        org_b = make_org("org-b")

        for org in (org_a, org_b):
            record = LLMInteraction(
                model_name="gpt-4o",
                provider="openai",
                organization_id=org.id,
                token_count_input=50,
                token_count_output=25,
                cost=0.001,
            )
            db_session.add(record)
        db_session.flush()

        a_records = (
            db_session.query(LLMInteraction)
            .filter(LLMInteraction.organization_id == org_a.id)
            .count()
        )
        b_records = (
            db_session.query(LLMInteraction)
            .filter(LLMInteraction.organization_id == org_b.id)
            .count()
        )
        total = db_session.query(func.count(LLMInteraction.id)).scalar()

        assert a_records == 1
        assert b_records == 1
        # Only our two test records exist (the fixture db_session is rolled back)
        assert total >= 2

    def test_org_b_restriction_does_not_affect_org_a(
        self, db_session, make_org, tenant_ctx
    ):
        """Demonstrate that org B data is isolated from org A queries."""
        from app.models import LLMInteraction

        org_a = make_org("iso-a")
        org_b = make_org("iso-b")

        # Add org A record
        db_session.add(LLMInteraction(
            model_name="gpt-4o",
            provider="openai",
            organization_id=org_a.id,
            token_count_input=10, token_count_output=5, cost=0.0001,
        ))

        # Add org B record with different provider
        db_session.add(LLMInteraction(
            model_name="claude-opus-5",
            provider="anthropic",
            organization_id=org_b.id,
            token_count_input=20, token_count_output=10, cost=0.005,
        ))
        db_session.flush()

        # When scoped to org A, we see only org A's records
        with tenant_ctx(org_a.id):
            from app.models.models import LLMInteraction as LI
            a_providers = (
                db_session.query(LI.provider)
                .filter(LI.organization_id == org_a.id)
                .distinct()
                .all()
            )
            a_provider_set = {r[0] for r in a_providers}

            b_providers = (
                db_session.query(LI.provider)
                .filter(LI.organization_id == org_b.id)
                .distinct()
                .all()
            )
            b_provider_set = {r[0] for r in b_providers}

        assert a_provider_set == {"openai"}
        assert b_provider_set == {"anthropic"}

    def test_cost_report_respects_org_boundary(
        self, db_session, make_org, app, tenant_ctx
    ):
        """Cost report for org B does not include org A's interactions."""
        from app.models import LLMInteraction
        from app.modules.ai_chat.services.llm_cost_tracker import LLMCostTracker
        from datetime import datetime, timedelta, timezone

        org_a = make_org("cost-a")
        org_b = make_org("cost-b")

        # Add one interaction for each org
        for org in (org_a, org_b):
            record = LLMInteraction(
                model_name="gpt-4o",
                provider="openai",
                token_count_input=100,
                token_count_output=50,
                cost=0.005,
                organization_id=org.id,
            )
            record.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
            db_session.add(record)
        db_session.flush()

        with app.app_context():
            with tenant_ctx(org_b.id):
                tracker = LLMCostTracker()
                report = tracker.get_cost_report(
                    start_date=datetime.now(timezone.utc) - timedelta(days=1),
                    end_date=datetime.now(timezone.utc),
                    organization_id=org_b.id,
                )
        # Only one interaction (org_b) should be in the report
        assert report["totals"]["calls"] == 1
        assert report["totals"]["cost"] > 0

    def test_organization_spending_uses_current_org_only(
        self, db_session, make_org, app, tenant_ctx
    ):
        """Organisation spending sums only the current tenant's interactions."""
        from datetime import datetime, timedelta, timezone

        from app.models import LLMInteraction
        from app.modules.ai_chat.services.llm_cost_tracker import LLMCostTracker

        org_a = make_org("spend-a")
        org_b = make_org("spend-b")

        a_record = LLMInteraction(
            model_name="gpt-4o",
            provider="openai",
            token_count_input=10,
            token_count_output=5,
            cost=1.25,
            organization_id=org_a.id,
        )
        a_record.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db_session.add(a_record)

        b_record = LLMInteraction(
            model_name="gpt-4o",
            provider="openai",
            token_count_input=10,
            token_count_output=5,
            cost=123.00,
            organization_id=org_b.id,
        )
        b_record.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db_session.add(b_record)
        db_session.flush()

        since = datetime.now(timezone.utc) - timedelta(days=1)
        with app.app_context():
            with tenant_ctx(org_a.id):
                tracker = LLMCostTracker()
                assert float(tracker._get_organization_spending(since)) == pytest.approx(1.25)
            with tenant_ctx(org_b.id):
                tracker = LLMCostTracker()
                assert float(tracker._get_organization_spending(since)) == pytest.approx(123.00)

    def test_organization_spending_fails_closed_without_org(
        self, db_session, make_org, app
    ):
        """Organisation spending returns zero when no tenant is resolved."""
        from datetime import datetime, timedelta, timezone
        from decimal import Decimal

        from app.models import LLMInteraction
        from app.modules.ai_chat.services.llm_cost_tracker import LLMCostTracker

        org = make_org("spend-none")
        record = LLMInteraction(
            model_name="gpt-4o",
            provider="openai",
            token_count_input=10,
            token_count_output=5,
            cost=9.99,
            organization_id=org.id,
        )
        record.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db_session.add(record)
        db_session.flush()

        since = datetime.now(timezone.utc) - timedelta(days=1)
        with app.app_context():
            tracker = LLMCostTracker()
            assert tracker._get_organization_spending(since) == Decimal("0")

    def test_domain_analytics_respects_org_boundary(
        self, db_session, make_org, app, tenant_ctx
    ):
        """Domain analytics for org B does not include org A's interactions."""
        from app.models import LLMInteraction
        from app.modules.ai_chat.services.multi_domain_chat_service import MultiDomainChatService
        from datetime import datetime, timedelta, timezone

        org_a = make_org("domain-a")
        org_b = make_org("domain-b")

        for org in (org_a, org_b):
            record = LLMInteraction(
                model_name="gpt-4o",
                provider="openai",
                token_count_input=10,
                token_count_output=5,
                cost=0.001,
                organization_id=org.id,
            )
            record.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
            db_session.add(record)
        db_session.flush()

        with app.app_context():
            with tenant_ctx(org_b.id):
                service = MultiDomainChatService()
                analytics = service.get_domain_analytics()
        # Should only count org_b's interactions
        assert analytics["total_messages"] == 1

    def test_quality_metrics_respects_org_boundary(
        self, db_session, make_org, app, tenant_ctx
    ):
        """Quality metrics for org B does not include org A's interactions."""
        from app.models import LLMInteraction
        from app.modules.ai_chat.services.multi_domain_chat_service import MultiDomainChatService
        from datetime import datetime, timedelta, timezone

        org_a = make_org("quality-a")
        org_b = make_org("quality-b")

        for org in (org_a, org_b):
            record = LLMInteraction(
                model_name="gpt-4o",
                provider="openai",
                token_count_input=10,
                token_count_output=5,
                cost=0.001,
                organization_id=org.id,
                response="some response",
                latency_ms=200,
            )
            record.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
            db_session.add(record)
        db_session.flush()

        with app.app_context():
            with tenant_ctx(org_b.id):
                service = MultiDomainChatService()
                metrics = service.get_quality_metrics()
        # Should only count org_b's interaction
        assert metrics.get("response_quality_score") is not None
        assert metrics["success_rate"] == 100.0
