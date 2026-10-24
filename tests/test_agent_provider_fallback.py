"""When one AI provider refuses, the chat answers from the next.

The first-answer timer found OpenRouter returning 429 with its daily free
allowance spent, the chat showing an error card, and NVIDIA never being asked.
These tests pin the behaviour that replaced it: a refusal that another provider
may not share moves to the next entry, anything else keeps the error card, and
the customer sees a plain message only when every entry has refused.
"""

from __future__ import annotations

import logging
import types

import httpx
import openai
import pytest

from app.modules.ai_chat.services import agent_runner as ar
from app.modules.ai_chat.services.agent_runner import AgentRunner, is_retryable_llm_error
from app.modules.ai_chat.services.llm_service_impl import LLMService

REAL_CHAIN = ar.build_provider_chain
SECRET = "sk-or-v1-very-secret-key-value"
NVIDIA_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
CHAIN = [
    ("openrouter", "google/gemma-4-31b-it:free", SECRET, ar._PROVIDER_BASE_URLS["openrouter"]),
    ("nvidia", NVIDIA_MODEL, "nvapi-secret", ar._PROVIDER_BASE_URLS["nvidia"]),
]


def _status_error(cls, status, message):
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    response = httpx.Response(status, request=request, json={"error": {"message": message}})
    return cls(message, response=response, body=None)


def _rate_limit():
    return _status_error(
        openai.RateLimitError, 429, "free-models-per-day-high-balance: 1000 a day, 0 left"
    )


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.setattr(LLMService, "_get_configured_provider", staticmethod(lambda *a, **k: CHAIN[0][:2]))
    monkeypatch.setattr(LLMService, "_get_all_api_keys", staticmethod(lambda p: [SECRET]))
    monkeypatch.setattr(ar, "build_provider_chain", lambda *a, **k: list(CHAIN))
    r = AgentRunner(user_id=1)
    monkeypatch.setattr(r, "_gate_mode", lambda: "off")
    monkeypatch.setattr(r, "_build_system_prompt", lambda *a, **k: "system")
    r.recorded = []
    monkeypatch.setattr(r, "_record_run", lambda *a, **k: r.recorded.append(k) or None)
    return r


def _script(monkeypatch, runner, outcomes):
    calls = []

    def fake(provider, model, api_key, *a, **k):
        calls.append((provider, model))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return {"text": outcome, "tool_calls": [], "raw": None}

    monkeypatch.setattr(runner, "_call_llm", fake)
    return calls


def test_rate_limited_provider_hands_the_question_to_the_next(runner, monkeypatch, caplog):
    calls = _script(monkeypatch, runner, [_rate_limit(), "Three applications are recorded."])
    with caplog.at_level(logging.WARNING):
        result = runner.run("How many applications do we run?")
    assert result["response"] == "Three applications are recorded."
    assert not result.get("error")
    assert [c[0] for c in calls] == ["openrouter", "nvidia"]
    assert runner.recorded[-1]["model_used"] == NVIDIA_MODEL
    refusals = [r for r in caplog.records if "refused" in r.getMessage()]
    assert len(refusals) == 1 and "openrouter" in refusals[0].getMessage()
    assert SECRET not in caplog.text and "nvapi-secret" not in caplog.text


def test_the_reply_names_the_provider_that_answered_not_the_first_tried(runner, monkeypatch):
    _script(monkeypatch, runner, [_rate_limit(), "Three applications are recorded."])
    result = runner.run("How many applications do we run?")
    assert result["answered_by"] == {"provider": "nvidia", "model": NVIDIA_MODEL}


def test_server_error_and_missing_model_also_move_on(runner, monkeypatch):
    first = _status_error(openai.InternalServerError, 503, "upstream unavailable")
    calls = _script(monkeypatch, runner, [first, "ok"])
    assert runner.run("q")["response"] == "ok"
    assert len(calls) == 2
    gone = _status_error(openai.NotFoundError, 404, "Function not found for account")
    assert is_retryable_llm_error(gone)


def test_all_providers_refusing_shows_a_plain_busy_message(runner, monkeypatch):
    _script(monkeypatch, runner, [_rate_limit(), _status_error(openai.InternalServerError, 502, "bad gateway")])
    result = runner.run("q")
    assert "busy" in result["response"].lower() and "Retry" in result["response"]
    assert "API Settings" not in result["response"]
    assert result["error"]


def test_a_turn_nobody_answered_names_no_model(runner, monkeypatch):
    _script(monkeypatch, runner, [_rate_limit(), _status_error(openai.InternalServerError, 502, "bad gateway")])
    result = runner.run("q")
    assert not result.get("answered_by")
    assert NVIDIA_MODEL not in result["response"] and "gemma" not in result["response"]


def test_a_bad_request_is_not_retried(runner, monkeypatch):
    calls = _script(monkeypatch, runner, [_status_error(openai.BadRequestError, 400, "messages must not be empty")])
    result = runner.run("q")
    assert [c[0] for c in calls] == ["openrouter"]
    assert result["error"]
    assert not is_retryable_llm_error(_status_error(openai.AuthenticationError, 401, "Invalid key"))


def test_timeouts_and_dropped_connections_are_retryable():
    request = httpx.Request("POST", "https://example.invalid")
    assert is_retryable_llm_error(openai.APITimeoutError(request=request))
    assert is_retryable_llm_error(openai.APIConnectionError(request=request))
    assert is_retryable_llm_error(RuntimeError("monthly quota exceeded"))
    assert not is_retryable_llm_error(ValueError("bad schema"))


def _settings(db_session, org, provider, model, key):
    from app.models.models import APISettings

    row = APISettings(provider=provider, api_key=key, enabled=True, default_model=model, organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _no_env_keys(monkeypatch):
    for name in ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI", "NVIDIA", "OPENROUTER"):
        monkeypatch.delenv(name + "_API_KEY", raising=False)
    monkeypatch.delenv("AI_PROVIDER_ORDER", raising=False)


def test_chain_puts_free_providers_first_and_stays_inside_the_organisation(
    db_session, make_org, tenant_ctx, monkeypatch
):
    _no_env_keys(monkeypatch)
    org_a, org_b = make_org("a"), make_org("b")
    _settings(db_session, org_a, "openrouter", "model-a:free, model-b:free", "key-or-a")
    _settings(db_session, org_a, "nvidia", "", "key-nv-a")
    _settings(db_session, org_b, "openrouter", "model-c:free", "key-or-b")
    with tenant_ctx(org_a.id):
        chain = ar.build_provider_chain("openrouter", "model-a:free")
    assert [e[0] for e in chain] == ["nvidia", "openrouter", "openrouter"]
    assert chain[0][1] == NVIDIA_MODEL
    assert [e[1] for e in chain[1:]] == ["model-a:free", "model-b:free"]
    assert {e[2] for e in chain} == {"key-nv-a", "key-or-a"}
    db_session.expire_all()
    with tenant_ctx(org_b.id):
        chain_b = ar.build_provider_chain("openrouter", "model-c:free")
    assert [(e[0], e[1], e[2]) for e in chain_b] == [("openrouter", "model-c:free", "key-or-b")]


def test_chain_order_is_a_setting(db_session, make_org, tenant_ctx, monkeypatch):
    _no_env_keys(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER_ORDER", "openrouter,nvidia")
    org = make_org("a")
    _settings(db_session, org, "openrouter", "model-a:free", "key-or")
    _settings(db_session, org, "nvidia", "", "key-nv")
    with tenant_ctx(org.id):
        chain = ar.build_provider_chain("nvidia", NVIDIA_MODEL)
    assert [e[0] for e in chain] == ["openrouter", "nvidia"]


def test_a_provider_can_be_pointed_at_a_gateway_address(db_session, make_org, tenant_ctx, monkeypatch):
    _no_env_keys(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER_BASE_URL_NVIDIA", "http://gateway.internal/v1")
    org = make_org("gw")
    _settings(db_session, org, "nvidia", "", "key-nv")
    _settings(db_session, org, "openrouter", "model-a:free", "key-or")
    with tenant_ctx(org.id):
        chain = ar.build_provider_chain("nvidia", NVIDIA_MODEL)
    urls = {e[0]: e[3] for e in chain}
    assert urls["nvidia"] == "http://gateway.internal/v1"
    assert urls["openrouter"] == ar._PROVIDER_BASE_URLS["openrouter"]


def test_an_organisation_with_its_own_paid_key_is_asked_before_any_free_model(
    db_session, make_org, tenant_ctx, monkeypatch
):
    _no_env_keys(monkeypatch)
    monkeypatch.setenv("NVIDIA_API_KEY", "shared-nv")
    monkeypatch.setenv("OPENROUTER_API_KEY", "shared-or")
    org = make_org("own")
    _settings(db_session, org, "anthropic", "claude-own", "own-anthropic-key")
    with tenant_ctx(org.id):
        # the caller's default is a free route, as when nothing was chosen
        chain = ar.build_provider_chain("openrouter", "model-a:free")
    assert chain[0][:3] == ("anthropic", "claude-own", "own-anthropic-key")
    # when that key fails the chain moves to the free models, NVIDIA first
    assert [e[0] for e in chain] == ["anthropic", "nvidia", "openrouter"]


def test_a_working_own_key_never_reaches_the_free_models(
    runner, db_session, make_org, tenant_ctx, monkeypatch
):
    _no_env_keys(monkeypatch)
    monkeypatch.setenv("NVIDIA_API_KEY", "shared-nv")
    monkeypatch.setenv("OPENROUTER_API_KEY", "shared-or")
    monkeypatch.setattr(ar, "build_provider_chain", REAL_CHAIN)
    org = make_org("own")
    _settings(db_session, org, "anthropic", "claude-own", "own-key")
    monkeypatch.setattr(LLMService, "_get_all_api_keys", staticmethod(lambda p: ["own-key"]))
    calls = _script(monkeypatch, runner, ["Answered with the organisation's key."])
    with tenant_ctx(org.id):
        result = runner.run("How many applications do we run?")
    assert result["response"] == "Answered with the organisation's key."
    assert [c[0] for c in calls] == ["anthropic"]


def test_when_the_own_key_fails_the_free_models_answer_nvidia_first(
    runner, db_session, make_org, tenant_ctx, monkeypatch
):
    _no_env_keys(monkeypatch)
    monkeypatch.setenv("NVIDIA_API_KEY", "shared-nv")
    monkeypatch.setenv("OPENROUTER_API_KEY", "shared-or")
    monkeypatch.setattr(ar, "build_provider_chain", REAL_CHAIN)
    org = make_org("own")
    _settings(db_session, org, "anthropic", "claude-own", "own-key")
    monkeypatch.setattr(LLMService, "_get_all_api_keys", staticmethod(lambda p: ["own-key"]))
    calls = _script(monkeypatch, runner, [_rate_limit(), "Answered by NVIDIA."])
    with tenant_ctx(org.id):
        result = runner.run("How many applications do we run?")
    assert result["response"] == "Answered by NVIDIA."
    assert [c[0] for c in calls] == ["anthropic", "nvidia"]


def test_one_organisations_key_is_never_used_for_another(db_session, make_org, tenant_ctx, monkeypatch):
    _no_env_keys(monkeypatch)
    org_a, org_b = make_org("a"), make_org("b")
    _settings(db_session, org_a, "anthropic", "claude-a", "key-of-a")
    with tenant_ctx(org_a.id):
        assert [e[2] for e in ar.build_provider_chain("anthropic", "claude-a")] == ["key-of-a"]
    db_session.expire_all()
    with tenant_ctx(org_b.id):
        chain_b = ar.build_provider_chain("anthropic", "claude-a")
    assert "key-of-a" not in {e[2] for e in chain_b}
