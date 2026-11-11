"""Every DeepSeek call names a model DeepSeek serves, in the mode its role needs, and is counted at list price.

DeepSeek's model list returns two names, ``deepseek-flash`` and ``deepseek-v4-pro``; the two older names stopped
being served on 24 Jul 2026. Both models think unless the request says otherwise, and the thinking text uses the
token budget, so a call that does not state its mode can come back with no answer. The stub refuses any name
outside the list and answers an empty message to a call that leaves thinking on. No real provider is reached.
"""
from __future__ import annotations

import pytest

from app.modules.ai_chat.services.agent_runner import AgentRunner
from app.modules.ai_chat.services.llm_service_impl import LLMService
from app.modules.ai_chat.services.model_defaults import (
    DEEPSEEK_MODELS, PLATFORM_DEFAULT_MODELS, deepseek_request_for)
from tests.deepseek_stub import KEY
from tests.test_llm_deepseek import _ledger, production_shape, stub  # noqa: F401


@pytest.mark.parametrize("held, served, mode", [
    ("deepseek-flash", "deepseek-flash", "disabled"),
    ("deepseek-chat", "deepseek-flash", "disabled"),
    ("deepseek-reasoner", "deepseek-flash", "enabled"),
    ("deepseek-v3.2", "deepseek-flash", "disabled"),
    ("deepseek-v4-pro", "deepseek-v4-pro", "enabled"),
    ("", "deepseek-flash", "disabled"),
    ("something-else", "deepseek-flash", "disabled"),
])
def test_a_held_name_becomes_a_served_name_and_the_mode_its_role_needs(held, served, mode):
    assert deepseek_request_for(held) == (served, {"thinking": {"type": mode}})
    assert deepseek_request_for(held)[0] in DEEPSEEK_MODELS


def test_a_tool_loop_never_asks_for_thinking():
    assert deepseek_request_for("deepseek-v4-pro", tools=True) == ("deepseek-v4-pro", {"thinking": {"type": "disabled"}})


def test_the_platform_default_is_a_served_name():
    assert PLATFORM_DEFAULT_MODELS["deepseek"] == "deepseek-flash"


@pytest.mark.parametrize("held, served, mode", [
    ("deepseek-flash", "deepseek-flash", "disabled"),
    ("deepseek-chat", "deepseek-flash", "disabled"),
    ("deepseek-reasoner", "deepseek-flash", "enabled"),
    ("deepseek-v4-pro", "deepseek-v4-pro", "enabled"),
])
def test_a_drafting_call_sends_a_served_name_with_its_mode(app, production_shape, held, served, mode):
    with app.app_context():
        text, _tin, _tout, _cost = LLMService._call_deepseek("Reply with one word", held, KEY, max_tokens=3)
    request = production_shape.requests[-1]
    assert (request["model"], request["thinking"]) == (served, mode)
    # a non-thinking call comes back with the answer; a thinking one spends the budget on reasoning
    assert (text == "") == (mode == "enabled")


@pytest.mark.parametrize("stream", [False, True])
def test_the_chat_tool_loop_sends_a_served_name_non_thinking(app, production_shape, stream):
    runner = AgentRunner(user_id=1)
    with app.app_context():
        result = runner._call_llm("deepseek", "deepseek-chat", KEY, "system", [{"role": "user", "content": "hi"}], [],
                                  stream=stream, base_url=production_shape.base_url)
    request = production_shape.requests[-1]
    assert (request["model"], request["thinking"]) == ("deepseek-flash", "disabled")
    assert result["text"] == production_shape.reply


@pytest.mark.parametrize("model, price_in, price_out", [
    ("deepseek-flash", 0.30, 1.20),
    ("deepseek-chat", 0.30, 1.20),
    ("deepseek-reasoner", 0.30, 1.20),
    ("deepseek-v4-pro", 1.32, 3.96),
])
def test_a_deepseek_call_is_counted_at_no_less_than_the_peak_list_price(monkeypatch, model, price_in, price_out):
    for name in ("IN", "OUT"):
        monkeypatch.delenv("LLM_PRICE_PER_MTOK_%s_DEEPSEEK" % name, raising=False)
    assert LLMService.price_per_mtok("deepseek", model) == (price_in, price_out)


def test_the_ledger_counts_one_million_output_tokens_at_peak_output_price(app, production_shape):
    with app.app_context():
        LLMService.record_spend("deepseek", "deepseek-flash", 0, 1_000_000)
    assert _ledger(app, "deepseek")[0] == pytest.approx(1.20, abs=1e-6)
