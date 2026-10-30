"""Organisation data never reaches a free model in the production configuration.

Under the production rules (the ``LLM_FREE_FENCE`` setting, true in the production config) a free
provider is refused for every request: the chat, the website reader and every other call that goes
through the one language-model service. With no paid route open the person reads the plain pause and
nothing leaves the process; with a paid route configured the request goes there and only there. A free
provider named in ``LLM_PROVIDER_ORDER`` stops the app at boot. Testing and preview keep their behaviour.
"""
from __future__ import annotations

import pytest

from app.modules.ai_chat.services import llm_service_impl as service_module
from app.modules.ai_chat.services.agent_runner import AgentRunner
from app.modules.ai_chat.services.llm_service_impl import PAUSED_MESSAGE, AnswersPaused, LLMService
from tests.test_llm_hard_stop import (
    GEMINI_ADDRESS,
    GEMINI_KEY,
    NVIDIA_ADDRESS,
    NVIDIA_KEY,
    _Transport,
    _clear_ledger,
)


@pytest.fixture
def stubs(app, monkeypatch):
    """Every outbound call is recorded by a stand-in; none reaches a real provider."""
    for name in ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI", "NVIDIA", "OPENROUTER", "DEEPSEEK", "HUGGINGFACE",
                 "MISTRAL", "CEREBRAS", "GROQ"):
        monkeypatch.delenv(name + "_API_KEY", raising=False)
    for name in ("AI_PROVIDER_ORDER", "LLM_PROVIDER_ORDER", "LLM_FREE_FENCE", "LLM_REQUEST_CEILING_NVIDIA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_BASE", GEMINI_ADDRESS)
    monkeypatch.setenv("NVIDIA_API_BASE", NVIDIA_ADDRESS)
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_GEMINI", "1.00")
    transport = _Transport()
    import openai

    monkeypatch.setattr(openai, "OpenAI", transport.openai_class())
    monkeypatch.setattr(service_module.requests, "post", transport.post)
    monkeypatch.setattr(LLMService, "_guard_provider_call", staticmethod(lambda p, m, *a, **k: (p, m, None)))
    monkeypatch.setattr(LLMService, "_persist_interaction_record", staticmethod(lambda i: i))
    _clear_ledger(app)
    yield transport
    _clear_ledger(app)


def _production_rules(app, monkeypatch, on=True):
    monkeypatch.setitem(app.config, "LLM_FREE_FENCE", on)


def _chat_reply(app, monkeypatch):
    runner = AgentRunner(user_id=1)
    monkeypatch.setattr(runner, "_gate_mode", lambda: "off")
    monkeypatch.setattr(runner, "_build_system_prompt", lambda *a, **k: "system")
    monkeypatch.setattr(runner, "_record_run", lambda *a, **k: None)
    return runner.run("What breaks if billing fails?")


def _no_outbound_call(transport):
    assert transport.gemini_calls == [] and transport.openai_compatible_calls == []


def test_chat_with_only_a_free_provider_configured_is_paused_and_calls_nothing(app, stubs, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", NVIDIA_KEY)
    _production_rules(app, monkeypatch)
    with app.app_context():
        result = _chat_reply(app, monkeypatch)
    assert result["response"] == PAUSED_MESSAGE
    _no_outbound_call(stubs)


def test_a_website_read_with_only_a_free_provider_configured_is_paused_and_calls_nothing(app, stubs, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", NVIDIA_KEY)
    _production_rules(app, monkeypatch)
    with app.app_context():
        with pytest.raises(AnswersPaused) as paused:
            LLMService.generate_from_prompt("Summarise this public page.", public_input=True, use_cache=False)
        assert str(paused.value).startswith(PAUSED_MESSAGE)
    _no_outbound_call(stubs)


def test_every_other_call_with_only_a_free_provider_is_paused_and_calls_nothing(app, stubs, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", NVIDIA_KEY)
    _production_rules(app, monkeypatch)
    with app.app_context():
        with pytest.raises(AnswersPaused):
            LLMService.generate_from_prompt("Summarise our applications.", use_cache=False)
        with pytest.raises(AnswersPaused):
            LLMService._call_llm_with_failover(prompt="x", model="any", provider="nvidia")
        with pytest.raises(AnswersPaused):
            LLMService._call_llm_with_failover(prompt="x", model="any", provider="nvidia", public_input=True)
    _no_outbound_call(stubs)


def test_with_a_paid_route_configured_requests_go_there_only(app, stubs, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_KEY)
    monkeypatch.setenv("NVIDIA_API_KEY", NVIDIA_KEY)
    _production_rules(app, monkeypatch)
    with app.app_context():
        assert LLMService.provider_order() == ["gemini"]
        assert LLMService.generate_from_prompt("Summarise our applications.", use_cache=False) == "from the gemini stub"
        assert LLMService.generate_from_prompt("Public page.", public_input=True, use_cache=False) == "from the gemini stub"
    assert len(stubs.gemini_calls) == 2
    assert stubs.openai_compatible_calls == []


def test_the_default_order_holds_paid_routes_only_under_production_rules(app, stubs, monkeypatch):
    _production_rules(app, monkeypatch)
    with app.app_context():
        assert LLMService.provider_order() == ["gemini"]


def test_a_free_provider_in_the_order_is_dropped_at_the_point_of_use(app, stubs, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "nvidia,gemini")
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_KEY)
    monkeypatch.setenv("NVIDIA_API_KEY", NVIDIA_KEY)
    _production_rules(app, monkeypatch)
    with app.app_context():
        assert LLMService.provider_order() == ["gemini"]


def test_testing_and_preview_keep_their_behaviour(app, stubs, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", NVIDIA_KEY)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini,nvidia")
    _production_rules(app, monkeypatch, on=False)
    with app.app_context():
        assert LLMService.provider_order() == ["gemini", "nvidia"]
        assert LLMService.generate_from_prompt("hello", use_cache=False) == "from the nvidia stub"
    assert len(stubs.openai_compatible_calls) == 1


def test_a_flag_in_the_environment_turns_the_production_rules_on(app, stubs, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", NVIDIA_KEY)
    monkeypatch.setenv("LLM_FREE_FENCE", "1")
    with app.app_context():
        with pytest.raises(AnswersPaused):
            LLMService.generate_from_prompt("hello", use_cache=False)
    _no_outbound_call(stubs)


def test_booting_under_production_with_a_free_provider_in_the_order_fails_plainly(monkeypatch):
    from app import create_app

    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini,nvidia")
    with pytest.raises(RuntimeError) as refused:
        create_app("production")
    message = str(refused.value)
    assert "LLM_PROVIDER_ORDER" in message and "nvidia" in message


def test_the_boot_check_passes_for_a_paid_order_and_ignores_other_configurations(monkeypatch):
    from app._bootstrap.services import check_free_fence

    class _App:
        def __init__(self, fence):
            self.config = {"LLM_FREE_FENCE": fence}

    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini")
    check_free_fence(_App(True))
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "nvidia")
    check_free_fence(_App(False))
    with pytest.raises(RuntimeError):
        check_free_fence(_App(True))
