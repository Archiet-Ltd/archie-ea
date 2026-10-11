"""LLMInteraction records the organisation a language-model call was made for.

The row needs an organisation so a later budget check can be scoped per
organisation instead of summing every tenant's spend together. The budget
check itself is handled separately; this only covers what gets written to
``llm_interactions``.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _track(db_session, **kwargs):
    from app.modules.ai_chat.services.llm_cost_tracker import LLMCostTracker

    tracker = LLMCostTracker()
    tracker.track_interaction(
        model_name="claude-opus-5",
        provider="anthropic",
        input_tokens=100,
        output_tokens=50,
        **kwargs,
    )
    db_session.flush()

    from app.models import LLMInteraction

    return LLMInteraction.query.order_by(LLMInteraction.id.desc()).first()


def _create_interaction(db_session, make_org, *, provider, response, latency_ms, org=None):
    from app.models import LLMInteraction

    organization = org or make_org(provider)
    interaction = LLMInteraction(
        provider=provider,
        model_name="test-model",
        response=response,
        latency_ms=latency_ms,
        organization_id=organization.id,
    )
    db_session.add(interaction)
    db_session.flush()
    return interaction


def _create_pipeline_stage(db_session, *, name="decision-pipeline", stage_type="analysis"):
    from app.models.models import GenerationPipeline, PipelineStage

    pipeline = GenerationPipeline(name=name)
    db_session.add(pipeline)
    db_session.flush()

    stage = PipelineStage(pipeline_id=pipeline.id, stage_type=stage_type, order=1)
    db_session.add(stage)
    db_session.flush()
    return stage


def test_interaction_tracked_in_organisation_context_records_that_organisation(
    db_session, make_org, tenant_ctx
):
    org_b = make_org("b")

    with tenant_ctx(org_b.id):
        interaction = _track(db_session)

    assert interaction.organization_id == org_b.id


def test_interaction_tracked_with_no_organisation_context_records_null(
    db_session, app
):
    with app.test_request_context("/"):
        interaction = _track(db_session)

    assert interaction.organization_id is None


def test_explicit_organisation_argument_is_recorded(db_session, make_org):
    org = make_org("explicit")

    interaction = _track(db_session, organization_id=org.id)

    assert interaction.organization_id == org.id


def test_get_domain_analytics_is_scoped_and_unknown_org_is_empty(
    db_session, app, make_org, tenant_ctx
):
    from app.modules.ai_chat.services.multi_domain_chat_service import MultiDomainChatService
    from flask import g

    org_a = make_org("domain-a")
    org_b = make_org("domain-b")
    _create_interaction(
        db_session,
        make_org,
        provider="domain-a",
        response="ok",
        latency_ms=10,
        org=org_a,
    )
    _create_interaction(
        db_session,
        make_org,
        provider="domain-b",
        response="ok",
        latency_ms=30,
        org=org_b,
    )

    service = MultiDomainChatService()

    with tenant_ctx(org_a.id):
        analytics_a = service.get_domain_analytics()

    with tenant_ctx(org_b.id):
        analytics_b = service.get_domain_analytics()

    with app.test_request_context("/"):
        g.current_org_id = None
        analytics_unknown = service.get_domain_analytics()

    assert analytics_a == {
        "domains": [{"domain": "domain-a", "message_count": 1}],
        "total_domains": 1,
        "total_messages": 1,
    }
    assert analytics_b == {
        "domains": [{"domain": "domain-b", "message_count": 1}],
        "total_domains": 1,
        "total_messages": 1,
    }
    assert analytics_unknown == {
        "domains": [],
        "total_domains": 0,
        "total_messages": 0,
    }


def test_get_quality_metrics_is_scoped_and_unknown_org_is_empty(
    db_session, app, make_org, tenant_ctx
):
    from app.modules.ai_chat.services.multi_domain_chat_service import MultiDomainChatService
    from flask import g

    org_a = make_org("quality-a")
    org_b = make_org("quality-b")
    _create_interaction(
        db_session,
        make_org,
        provider="provider-a",
        response="ok",
        latency_ms=10,
        org=org_a,
    )
    _create_interaction(
        db_session,
        make_org,
        provider="provider-b",
        response="",
        latency_ms=30,
        org=org_b,
    )

    service = MultiDomainChatService()

    with tenant_ctx(org_a.id):
        metrics_a = service.get_quality_metrics()

    with tenant_ctx(org_b.id):
        metrics_b = service.get_quality_metrics()

    with app.test_request_context("/"):
        g.current_org_id = None
        metrics_unknown = service.get_quality_metrics()

    assert metrics_a["success_rate"] == 100.0
    assert metrics_a["response_quality_score"] == 100.0
    assert metrics_a["avg_response_time_ms"] == 10
    assert metrics_a["total_interactions"] == 1

    assert metrics_b["success_rate"] == 0.0
    assert metrics_b["response_quality_score"] == 0.0
    assert metrics_b["avg_response_time_ms"] == 30
    assert metrics_b["total_interactions"] == 1

    assert metrics_unknown == {
        "response_quality_score": None,
        "avg_response_time_ms": None,
        "success_rate": 0,
        "feedback_count": None,
        "total_interactions": 0,
    }


def test_get_decision_log_is_scoped_and_log_decision_records_current_org(
    db_session, make_org, tenant_ctx
):
    from app.models import LLMInteraction
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    org_a = make_org("decision-a")
    org_b = make_org("decision-b")
    stage_a = _create_pipeline_stage(db_session, name="decision-pipeline-a")
    stage_b = _create_pipeline_stage(db_session, name="decision-pipeline-b")

    with tenant_ctx(org_a.id):
        log_a_id = LLMService.log_decision(
            decision_type="approval",
            context={"item": "a"},
            decision={"recommendation": "approve"},
            rationale="org a rationale",
            project_id=stage_a.id,
        )

    with tenant_ctx(org_b.id):
        log_b_id = LLMService.log_decision(
            decision_type="approval",
            context={"item": "b"},
            decision={"recommendation": "reject"},
            rationale="org b rationale",
            project_id=stage_b.id,
        )

    stored_a = db_session.get(LLMInteraction, log_a_id)
    stored_b = db_session.get(LLMInteraction, log_b_id)

    assert stored_a.organization_id == org_a.id
    assert stored_b.organization_id == org_b.id
    assert stored_a.pipeline_stage_id == stage_a.id
    assert stored_b.pipeline_stage_id == stage_b.id

    with tenant_ctx(org_a.id):
        logs_a = LLMService.get_decision_log(project_id=stage_a.id)

    with tenant_ctx(org_b.id):
        logs_b = LLMService.get_decision_log(project_id=stage_b.id)

    assert [entry["id"] for entry in logs_a] == [log_a_id]
    assert [entry["id"] for entry in logs_b] == [log_b_id]
    assert logs_a[0]["decision"] == {"recommendation": "approve"}
    assert logs_b[0]["decision"] == {"recommendation": "reject"}
    assert logs_a[0]["pipeline_stage_id"] == stage_a.id
    assert logs_b[0]["pipeline_stage_id"] == stage_b.id
    assert json.loads(stored_a.prompt)["context"] == {"item": "a"}
    assert json.loads(stored_b.prompt)["context"] == {"item": "b"}
