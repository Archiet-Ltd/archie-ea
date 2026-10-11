"""The AI controls (audit trail, cost monitor, data classifier) run on every model call.

Every model call in the product goes through LLMService: either ``_call_llm``
or, for a few callers, ``_call_llm_with_failover`` directly. These tests stub
the provider and check that each call, by either route, is classified, audited
from request to response (or error) and costed, exactly once, under the calling
organisation; and that one organisation's records never show up in another's.

Uses the shared fixtures in tests/conftest.py (db_session, make_org, tenant_ctx).
"""

from __future__ import annotations

import subprocess
import sys
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
def stub_provider(monkeypatch):
    """One OpenAI key, a provider that answers, and no other provider."""
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    calls = []

    def fake_keys(provider):
        return ["test-key"] if provider == "openai" else []

    def fake_openai(prompt, model, api_key, max_tokens=None, timeout=None):
        calls.append(prompt)
        return f"answer to {len(prompt)} chars", 11, 7, 0.0042

    monkeypatch.setattr(LLMService, "_get_all_api_keys", staticmethod(fake_keys))
    monkeypatch.setattr(LLMService, "_call_openai", staticmethod(fake_openai))
    return calls


def _marker():
    return f"controls-{uuid.uuid4().hex}"


def _events_for(org_id, correlation_id=None):
    from app.ai.audit_trail import ai_audit_trail

    return ai_audit_trail.get_events(
        limit=10000, organization_id=org_id, correlation_id=correlation_id
    )


def _costs_for(org_id, model):
    from app.ai.cost_monitor import ai_cost_monitor

    with ai_cost_monitor._lock:
        records = list(ai_cost_monitor._cost_records)
    return [r for r in records if r.organization_id == org_id and r.metadata.get("model") == model]


def _all_costs_for(org_id):
    from app.ai.cost_monitor import ai_cost_monitor

    with ai_cost_monitor._lock:
        records = list(ai_cost_monitor._cost_records)
    return [r for r in records if r.organization_id == org_id]


def _request_event(org_id, model):
    requests = [
        e for e in _events_for(org_id)
        if e["event_type"] == "ai_request" and e["details"]["metadata"].get("model") == model
    ]
    assert len(requests) == 1, f"expected one audited request for {model}, got {len(requests)}"
    return requests[0]


def test_every_control_runs_once_on_a_call_through_call_llm(make_org, tenant_ctx, stub_provider):
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    org = make_org("controls-a")
    model = _marker()
    with tenant_ctx(org.id):
        text, interaction = LLMService._call_llm("Describe the claims platform", model, "openai")

    assert text.startswith("answer to")
    request = _request_event(org.id, model)
    related = _events_for(org.id, request["correlation_id"])
    kinds = sorted(e["event_type"] for e in related)
    assert kinds == ["ai_request", "ai_response", "data_classification"]
    response = next(e for e in related if e["event_type"] == "ai_response")
    assert response["details"]["tokens_used"] == 18
    assert response["details"]["cost"] == pytest.approx(0.0042)
    costs = _costs_for(org.id, model)
    assert len(costs) == 1
    assert costs[0].cost_amount == pytest.approx(0.0042)
    assert costs[0].metadata["tokens"] == 18


def test_direct_failover_callers_are_covered_too(make_org, tenant_ctx, stub_provider):
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    org = make_org("controls-direct")
    model = _marker()
    with tenant_ctx(org.id):
        LLMService._call_llm_with_failover(prompt="Map this application", model=model, provider="openai")

    request = _request_event(org.id, model)
    kinds = sorted(e["event_type"] for e in _events_for(org.id, request["correlation_id"]))
    assert kinds == ["ai_request", "ai_response", "data_classification"]
    assert len(_costs_for(org.id, model)) == 1


def test_cross_provider_failover_runs_each_control_once(make_org, tenant_ctx, monkeypatch):
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    provider_calls = []

    def fake_keys(provider):
        if provider == "openai":
            return ["openai-key"]
        if provider == "anthropic":
            return ["anthropic-key"]
        return []

    def failing_openai(prompt, model, api_key, max_tokens=None, timeout=None):
        provider_calls.append(("openai", model, api_key))
        raise RuntimeError("quota exceeded for openai")

    def successful_anthropic(prompt, model, api_key, max_tokens=None, timeout=None):
        provider_calls.append(("anthropic", model, api_key))
        return "fallback answer", 13, 8, 0.0061

    monkeypatch.setattr(LLMService, "_get_all_api_keys", staticmethod(fake_keys))
    monkeypatch.setattr(LLMService, "_call_openai", staticmethod(failing_openai))
    monkeypatch.setattr(LLMService, "_call_anthropic", staticmethod(successful_anthropic))

    org = make_org("controls-cross-provider")
    model = _marker()
    with tenant_ctx(org.id):
        text, interaction = LLMService._call_llm_with_failover(
            prompt="Map this application",
            model=model,
            provider="openai",
        )

    assert text == "fallback answer"
    assert interaction.provider == "anthropic"
    assert [call[0] for call in provider_calls] == ["openai", "anthropic"]

    request = _request_event(org.id, model)
    related = _events_for(org.id, request["correlation_id"])
    assert sorted(e["event_type"] for e in related) == ["ai_request", "ai_response", "data_classification"]
    assert len([e for e in related if e["event_type"] == "ai_request"]) == 1
    assert len([e for e in related if e["event_type"] == "data_classification"]) == 1
    assert len([e for e in related if e["event_type"] == "ai_response"]) == 1
    costs = _all_costs_for(org.id)
    assert len(costs) == 1
    assert costs[0].cost_amount == pytest.approx(0.0061)
    assert costs[0].metadata["provider"] == "anthropic"


def test_a_failed_call_is_audited_as_an_error(make_org, tenant_ctx, monkeypatch):
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    def fake_keys(provider):
        return ["test-key"] if provider == "openai" else []

    def failing_openai(prompt, model, api_key, max_tokens=None, timeout=None):
        raise ConnectionError("provider unreachable")

    monkeypatch.setattr(LLMService, "_get_all_api_keys", staticmethod(fake_keys))
    monkeypatch.setattr(LLMService, "_call_openai", staticmethod(failing_openai))

    org = make_org("controls-error")
    model = _marker()
    with tenant_ctx(org.id), pytest.raises(RuntimeError):
        LLMService._call_llm_with_failover(prompt="Summarise", model=model, provider="openai")

    request = _request_event(org.id, model)
    related = _events_for(org.id, request["correlation_id"])
    assert sorted(e["event_type"] for e in related) == ["ai_error", "ai_request", "data_classification"]
    assert _costs_for(org.id, model) == []


def test_sensitive_prompt_is_classified_without_keeping_the_value(make_org, tenant_ctx, stub_provider):
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    org = make_org("controls-classify")
    model = _marker()
    card = "4111111111111111"
    with tenant_ctx(org.id):
        LLMService._call_llm_with_failover(prompt=f"Card on file {card}", model=model, provider="openai")

    request = _request_event(org.id, model)
    classification = next(
        e for e in _events_for(org.id, request["correlation_id"])
        if e["event_type"] == "data_classification"
    )
    assert classification["details"]["risk"] == "critical"
    assert classification["details"]["safe_for_ai"] is False
    assert "credit_card" in classification["details"]["pattern_names"]
    assert card not in repr(_events_for(org.id, request["correlation_id"]))


def test_one_organisation_never_sees_anothers_records(make_org, tenant_ctx, stub_provider):
    from app.ai.cost_monitor import ai_cost_monitor
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    org_a, org_b = make_org("controls-iso-a"), make_org("controls-iso-b")
    model_a, model_b = _marker(), _marker()
    with tenant_ctx(org_a.id):
        LLMService._call_llm_with_failover(prompt="A's question", model=model_a, provider="openai")
    with tenant_ctx(org_b.id):
        LLMService._call_llm_with_failover(prompt="B's question", model=model_b, provider="openai")

    a_models = {e["details"].get("metadata", {}).get("model") for e in _events_for(org_a.id)}
    b_models = {e["details"].get("metadata", {}).get("model") for e in _events_for(org_b.id)}
    assert model_a in a_models and model_b not in a_models
    assert model_b in b_models and model_a not in b_models
    assert _costs_for(org_a.id, model_b) == []
    assert ai_cost_monitor.get_cost_summary(organization_id=org_b.id)["total_requests"] == len(
        [r for r in ai_cost_monitor._cost_records if r.organization_id == org_b.id]
    )


def test_placeholder_budgets_raise_no_alert(make_org, tenant_ctx, stub_provider, monkeypatch):
    """The monitor's built-in limits are placeholders, so recording spend alerts nobody."""
    from app.ai.cost_monitor import ai_cost_monitor

    raised = []
    monkeypatch.setattr(ai_cost_monitor, "_trigger_budget_alert", lambda *a, **k: raised.append(a))
    monkeypatch.setattr(ai_cost_monitor, "_budgets_configured", False)
    ai_cost_monitor.record_cost("model_call", 10_000.0, organization_id=make_org("controls-budget").id)
    assert raised == []


def test_controls_package_imports_without_an_application_context():
    result = subprocess.run(
        [sys.executable, "-c", "import app.ai.audit_trail, app.ai.data_classifier; print('imported')"],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "imported" in result.stdout
