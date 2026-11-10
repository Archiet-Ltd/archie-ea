"""A caller that gives ``generate_from_prompt`` a time limit gets one attempt, cut to that limit.

A provider double on loopback accepts the call and never replies. Without the limit the call retries (three
attempts of up to a minute each); with it the call stops at the limit after a single request. The stored-answer
lookup of the chat passes the limit, and the turn is given only what is left of its minute.
"""

from __future__ import annotations

import time

import pytest

from app.modules.ai_chat.services import model_defaults
from app.modules.ai_chat.services.agent_runner import AgentRunner
from app.modules.ai_chat.services.llm_service_impl import LLMService
from app.modules.intelligence.services import nl_query_interpreter as interpreter
from tests.test_chat_turn_budget import _Peer


@pytest.fixture
def never_answers(app, monkeypatch):
    peer = _Peer()
    for name in ("OPENAI", "ANTHROPIC", "GEMINI", "OPENROUTER", "HUGGINGFACE", "DEEPSEEK"):
        monkeypatch.setenv(name + "_API_KEY", "")
    monkeypatch.setenv("NVIDIA_API_KEY", "platform-key-for-the-test")
    monkeypatch.setenv("AI_PROVIDER_ORDER", "nvidia")
    monkeypatch.setenv("AI_PROVIDER_BASE_URL_NVIDIA", peer.url)
    monkeypatch.setattr(LLMService, "_get_configured_provider", staticmethod(lambda *a, **k: ("nvidia", "m")))
    monkeypatch.setattr(LLMService, "_guard_provider_call", staticmethod(lambda p, m, o, **k: (p, m, o)))
    monkeypatch.setattr(LLMService, "platform_call_allowed", staticmethod(lambda p: (True, "")))
    yield peer
    peer.close()


def test_a_call_with_a_time_limit_makes_one_attempt_and_stops_at_the_limit(app, never_answers):
    started = time.monotonic()
    with pytest.raises(Exception):
        LLMService.generate_from_prompt("which entry fits?", use_cache=False, time_limit=2.0)
    took = time.monotonic() - started
    assert took < 4.0, took
    assert never_answers.calls == 1


def test_the_interpreter_maps_a_question_to_nothing_when_the_model_does_not_answer_in_time(app, never_answers, monkeypatch):
    monkeypatch.setattr(interpreter, "_model_configured", lambda: True)
    started = time.monotonic()
    found = interpreter.interpret("zzz qqq vvv unrelated words nothing matches", time_limit=2.0)
    took = time.monotonic() - started
    assert found["entry_id"] is None and found["method"] == "none"
    assert took < 4.0, took
    assert never_answers.calls == 1


def test_the_turn_gets_what_is_left_of_its_minute():
    runner = AgentRunner(user_id=1)
    runner._turn_deadline = None
    for waited, expected in ((0, 60.0), (25, 35.0), (58, 10.0)):
        started = time.monotonic()
        # run() sets the deadline before anything else can fail; stop it at the system prompt.
        runner._build_system_prompt = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop"))
        with pytest.raises(RuntimeError):
            runner.run("hello", waited_seconds=waited)
        left = runner._turn_deadline - started
        assert expected - 1.0 < left <= expected + 0.5, (waited, left)
    assert model_defaults.CHAT_TURN_BUDGET_SECONDS == 60.0
