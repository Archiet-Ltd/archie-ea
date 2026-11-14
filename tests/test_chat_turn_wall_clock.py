"""Every chat request ends by its budget whatever the provider does, on every provider path.

A client timeout is a gap between bytes: a provider that keeps sending bytes, or a provider outside the
tool list, would hold a call far past it. These tests give each path a short budget and a provider that
outlasts it, and measure when the call ends.
"""

from __future__ import annotations

import time

import pytest

from app.modules.ai_chat.services import agent_runner as ar
from app.modules.ai_chat.services import model_defaults
from app.modules.ai_chat.services.agent_runner import AgentRunner
from app.modules.ai_chat.services.llm_service_impl import LLMService

BUSY = "The assistant is busy right now. Wait a minute, then press Retry."


def test_a_lookup_with_a_time_limit_ends_at_the_limit_however_long_the_provider_keeps_sending(monkeypatch):
    monkeypatch.setattr(LLMService, "_get_configured_provider", staticmethod(lambda *a, **k: ("openai", "m")))

    def trickles(**kwargs):
        for _ in range(100):  # a byte every tenth of a second: no gap between bytes ever shows
            time.sleep(0.1)
        return "late", None

    monkeypatch.setattr(LLMService, "_call_llm", staticmethod(trickles))
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        LLMService.generate_from_prompt("which application?", use_cache=False, time_limit=1.0)
    assert time.monotonic() - started < 2.5


def test_a_lookup_that_answers_within_its_limit_still_answers(monkeypatch):
    monkeypatch.setattr(LLMService, "_get_configured_provider", staticmethod(lambda *a, **k: ("openai", "m")))
    monkeypatch.setattr(LLMService, "_call_llm", staticmethod(lambda **k: ("{}", None)))
    assert LLMService.generate_from_prompt("which application?", use_cache=False, time_limit=5.0) == "{}"


def test_a_provider_outside_the_tool_list_ends_at_the_turn_budget(monkeypatch):
    monkeypatch.setattr(model_defaults, "CHAT_TURN_BUDGET_SECONDS", 4.0)
    monkeypatch.setattr(model_defaults, "CHAT_TURN_MARGIN_SECONDS", 1.0)
    chain = [("gemini", "m0", "test-key-0", None)]
    monkeypatch.setattr(LLMService, "_get_configured_provider", staticmethod(lambda *a, **k: chain[0][:2]))
    monkeypatch.setattr(LLMService, "_get_all_api_keys", staticmethod(lambda p, **k: ["test-key-0"]))
    monkeypatch.setattr(ar, "build_provider_chain", lambda *a, **k: list(chain))
    runner = AgentRunner(user_id=1)
    monkeypatch.setattr(runner, "_gate_mode", lambda: "off")
    monkeypatch.setattr(runner, "_build_system_prompt", lambda *a, **k: "system")
    monkeypatch.setattr(runner, "_record_run", lambda *a, **k: None)

    def slow(**kwargs):
        time.sleep(12)
        return "late answer", {}

    monkeypatch.setattr(LLMService, "_call_llm_with_failover", staticmethod(slow))
    started = time.monotonic()
    result = runner.run("a question")
    took = time.monotonic() - started
    print("TEXT_ONLY_PROVIDER_ENDED_AT %.1f s of a 3 s budget" % took)
    assert took < 5.0, took
    assert result["response"] == BUSY


def test_closing_the_client_is_left_out_when_it_has_no_close_method(monkeypatch):
    """A client without a close method is read as before, not failed."""
    monkeypatch.setattr(model_defaults, "CHAT_TURN_BUDGET_SECONDS", 12.0)
    runner = AgentRunner(user_id=1)
    runner._turn_deadline = time.monotonic() + 5

    class Client:  # no close()
        pass

    client = Client()
    assert runner._read_within_turn(lambda read: "ok", closer=getattr(client, "close", None)) == "ok"
