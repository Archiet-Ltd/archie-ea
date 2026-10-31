"""The AI chat runs on the free OpenRouter and NVIDIA routes, with prompts scrubbed.

Pins four things: DeepSeek is gone from the defaults, the environment template
and the failover; NVIDIA is the fallback after OpenRouter; every request on the
chat path leaves scrubbed of email addresses, phone numbers and credentials; and
OpenRouter requests carry the data-policy setting that excludes endpoints which
train on or log prompts.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from app.modules.ai_chat.services.agent_runner import AgentRunner
from app.modules.ai_chat.services.llm_service_impl import LLMService
from app.modules.ai_chat.services.model_defaults import DEFAULT_MODELS, default_model_for

ROOT = Path(__file__).resolve().parents[1]

SENSITIVE_QUESTION = (
    "Who owns billing? Ask jane.doe@acme-corp.example or call +44 20 7946 0958, "
    "login password=hunter2hunter2 for the portal."
)


class _FakeCompletions:
    def __init__(self, sink):
        self._sink = sink

    def create(self, **kwargs):
        self._sink.append(kwargs)
        message = types.SimpleNamespace(content="ok", tool_calls=None)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


def _fake_openai(sink):
    class FakeOpenAI:
        def __init__(self, **kwargs):
            sink.append({"client": kwargs})
            self.chat = types.SimpleNamespace(completions=_FakeCompletions(sink))

    return FakeOpenAI


@pytest.fixture
def captured(monkeypatch):
    sink: list = []
    import openai

    monkeypatch.setattr(openai, "OpenAI", _fake_openai(sink))
    monkeypatch.setattr(
        LLMService, "_guard_provider_call", staticmethod(lambda *a, **k: (a[0], a[1], None))
    )
    return sink


def _sent(sink):
    return [call for call in sink if "messages" in call][-1]


def test_chat_request_is_scrubbed_before_it_leaves(captured):
    runner = AgentRunner(user_id=1)
    runner._call_llm(
        "openrouter",
        "google/gemma-4-31b-it:free",
        "key",
        "You help the team. Contact ops@acme-corp.example for access.",
        [
            {"role": "user", "content": SENSITIVE_QUESTION},
            {"role": "tool", "tool_call_id": "call_1", "content": "Owner: sam@acme-corp.example [ref:12345678-1234]"},
        ],
        [],
        base_url="https://openrouter.ai/api/v1",
    )
    sent = _sent(captured)
    everything = " ".join(str(m["content"]) for m in sent["messages"])
    for leaked in ("jane.doe@acme-corp.example", "7946 0958", "hunter2hunter2", "ops@acme-corp.example", "sam@acme-corp.example"):
        assert leaked not in everything
    assert "[EMAIL_REDACTED]" in everything
    assert "[PHONE_REDACTED]" in everything
    assert "[CREDENTIAL_REDACTED]" in everything
    # A citation marker must survive so the answer can still cite the record.
    assert "[ref:12345678-1234]" in everything
    # Structure is untouched.
    assert sent["messages"][2]["tool_call_id"] == "call_1"


def test_streamed_chat_request_is_scrubbed_too(captured, monkeypatch):
    class _Stream:
        def __enter__(self):
            return iter(())

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(_FakeCompletions, "create", lambda self, **kw: (self._sink.append(kw), _Stream())[1])
    AgentRunner(user_id=1)._call_llm(
        "nvidia",
        "nvidia/nemotron-3-ultra-550b-a55b",
        "key",
        "system",
        [{"role": "user", "content": SENSITIVE_QUESTION}],
        [],
        stream=True,
        base_url="https://integrate.api.nvidia.com/v1",
    )
    assert "jane.doe@acme-corp.example" not in str(_sent(captured)["messages"])


def test_openrouter_requests_exclude_endpoints_that_train_or_log(captured):
    AgentRunner(user_id=1)._call_llm(
        "openrouter", "google/gemma-4-31b-it:free", "key", "s",
        [{"role": "user", "content": "hi"}], [], base_url="https://openrouter.ai/api/v1",
    )
    assert _sent(captured)["extra_body"] == {"provider": {"data_collection": "deny"}}


def test_nvidia_requests_go_to_nvidia_with_no_openrouter_setting(captured):
    AgentRunner(user_id=1)._call_llm(
        "nvidia", "nvidia/nemotron-3-ultra-550b-a55b", "key", "s",
        [{"role": "user", "content": "hi"}], [], base_url="https://integrate.api.nvidia.com/v1",
    )
    assert _sent(captured).get("extra_body") is None
    assert captured[0]["client"]["base_url"] == "https://integrate.api.nvidia.com/v1"


def test_openrouter_text_call_carries_the_data_policy(monkeypatch):
    import requests

    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    monkeypatch.setattr(requests, "post", lambda url, **kw: (seen.update(kw, url=url), _Resp())[1])
    LLMService._call_openrouter("hello", "google/gemma-4-31b-it:free", "key")
    assert seen["json"]["provider"] == {"data_collection": "deny"}


def test_deepseek_is_absent_from_defaults_and_template():
    assert "deepseek" not in DEFAULT_MODELS
    assert default_model_for("deepseek") == ""
    assert default_model_for("nvidia")
    assert default_model_for("openrouter").endswith(":free")
    template = (ROOT / ".env.example").read_text()
    assert "DEEPSEEK" not in template.upper()
    assert "NVIDIA_API_KEY=" in template


def test_deepseek_key_alone_configures_nothing_and_nvidia_key_does(app, monkeypatch):
    for var in ("OPENROUTER_API_KEY", "NVIDIA_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    with app.app_context():
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-not-used-any-more")
        _, providers = LLMService._validate_database_configuration()
        assert "deepseek" not in providers
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
        _, providers = LLMService._validate_database_configuration()
        assert "nvidia" in providers


def test_failover_goes_from_openrouter_to_nvidia_never_to_deepseek(app, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-not-used-any-more")
    calls = []

    def _openrouter(*a, **k):
        calls.append("openrouter")
        raise RuntimeError("OpenRouter API error: 429 rate limit")

    def _nvidia(prompt, model, key, **k):
        calls.append("nvidia")
        return "answer", 1, 1, 0.0

    monkeypatch.setattr(LLMService, "_call_openrouter", staticmethod(_openrouter))
    monkeypatch.setattr(LLMService, "_call_nvidia", staticmethod(_nvidia))
    monkeypatch.setattr(
        LLMService, "_guard_provider_call", staticmethod(lambda p, m, *a, **k: (p, m, None))
    )
    monkeypatch.setattr(LLMService, "_persist_interaction_record", staticmethod(lambda i: i))
    with app.app_context():
        text, _ = LLMService._call_llm_with_failover(
            prompt="hello", model="google/gemma-4-31b-it:free", provider="openrouter"
        )
    assert text == "answer"
    assert calls[-1] == "nvidia"


def test_rate_limit_message_is_plain_and_offers_retry():
    reply = AgentRunner(user_id=1)._fallback("OpenRouter API error: 429 rate limit")
    assert "Retry" in reply["response"]
    assert "billing" not in reply["response"].lower()


# --- the fallback actually happens when the first route is out of quota -------


class _QuotaExhausted(Exception):
    def __str__(self):
        return "Error code: 429 - Rate limit exceeded: free-models-per-day"


def _runner_with_routes(monkeypatch, outcomes):
    """An AgentRunner whose provider calls follow ``outcomes`` (provider -> text or exception)."""
    from app.modules.ai_chat.services import agent_runner as module

    calls: list = []
    runner = AgentRunner(user_id=1)

    def fake_call(self, provider, model, api_key, system_prompt, messages, tools, stream=False, base_url=None):
        calls.append(provider)
        outcome = outcomes[provider]
        if isinstance(outcome, Exception):
            raise outcome
        return {"text": outcome, "tool_calls": [], "raw": None}

    monkeypatch.setattr(AgentRunner, "_call_llm", fake_call)
    monkeypatch.setattr(
        LLMService, "_get_configured_provider",
        staticmethod(lambda exclude_providers=None, user_id=None: next(
            (p, m) for p, m in [("openrouter", "google/gemma-4-31b-it:free"), ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b")]
            if p not in (exclude_providers or [])
        )),
    )
    monkeypatch.setattr(LLMService, "_get_all_api_keys", staticmethod(lambda provider: ["key"]))
    # The chain lists only providers that have a key. Give the two free routes
    # one and take every paid and ordering setting away, so the order under test
    # is the decided one for an organisation with no key of its own.
    for name in ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI"):
        monkeypatch.delenv(name + "_API_KEY", raising=False)
    monkeypatch.delenv("AI_PROVIDER_ORDER", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "nvidia,openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")
    monkeypatch.setenv("NVIDIA_API_KEY", "key")
    monkeypatch.setattr(module.AgentRunner, "_gate_mode", lambda self: "off", raising=False)
    return runner, calls


def test_chat_falls_back_to_the_next_free_route_when_the_first_is_out_of_quota(app, monkeypatch):
    # Decided order with no key of the organisation's own: NVIDIA first, then
    # OpenRouter (the shared OpenRouter allowance runs out daily).
    runner, calls = _runner_with_routes(
        monkeypatch, {"nvidia": _QuotaExhausted(), "openrouter": "Answered by the second route."}
    )
    with app.test_request_context("/ai-chat/message"):
        result = runner.run("What depends on our primary database?")
    assert calls == ["nvidia", "openrouter"]
    assert "Answered by the second route." in str(result.get("response"))
    assert not result.get("agent_error")


def test_chat_reports_the_failure_when_every_free_route_is_out_of_quota(app, monkeypatch):
    runner, calls = _runner_with_routes(
        monkeypatch, {"openrouter": _QuotaExhausted(), "nvidia": _QuotaExhausted()}
    )
    with app.test_request_context("/ai-chat/message"):
        result = runner.run("What depends on our primary database?")
    assert calls == ["nvidia", "openrouter"]
    assert "LLM call failed" in str(result.get("error"))
    assert "busy" in str(result.get("response"))


def test_nvidia_default_is_a_model_the_service_serves():
    """The earlier default is listed by NVIDIA but answers 404, so every fallback turn failed."""
    assert default_model_for("nvidia") == "nvidia/nemotron-3-ultra-550b-a55b"
    for path in (
        "app/modules/ai_chat/services/llm_router.py",
        "app/modules/admin/routes/admin_routes.py",
        "app/modules/admin/v2/routes/admin_routes.py",
        "app/modules/ai_chat/services/model_defaults.py",
    ):
        assert '"nvidia/nemotron-3-super-120b-a12b"' not in (ROOT / path).read_text(), path
