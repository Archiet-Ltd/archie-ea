"""A change is never reviewed by the model that drafted it, and the reviewer is a model DeepSeek serves.

DeepSeek serves two models. A deepseek-flash draft is reviewed by deepseek-v4-pro and a deepseek-v4-pro draft by
deepseek-flash; a name an organisation still holds from before is read as the model it maps to. The reviewer
thinks, so its call states a budget with room for the verdict after the thinking text. No real provider is reached.
"""
from __future__ import annotations

import json

import pytest

from app.modules.ai_chat.services.model_defaults import DEEPSEEK_MODELS, reviewer_model_for
from tests.deepseek_stub import KEY, DeepSeekStub

VERDICT = json.dumps({"verdict": "pass"})


@pytest.mark.parametrize("drafter, reviewer", [
    ("deepseek-flash", "deepseek-v4-pro"),
    ("deepseek-v4-pro", "deepseek-flash"),
    ("deepseek-chat", "deepseek-v4-pro"),
    ("deepseek-reasoner", "deepseek-v4-pro"),
    ("deepseek-v3.2", "deepseek-v4-pro"),
    ("", "deepseek-v4-pro"),
])
def test_the_reviewer_is_the_other_served_model(drafter, reviewer):
    assert reviewer_model_for("deepseek", drafter) == reviewer
    assert reviewer in DEEPSEEK_MODELS


def test_a_reviewer_call_returns_the_verdict_not_an_empty_answer(db_session, make_org, tenant_ctx, monkeypatch):
    from app.modules.ai_chat.services.llm_model_router import LLMModelRouter, ReviewerChoice
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    stub = DeepSeekStub(reply=VERDICT)
    monkeypatch.setattr(LLMService, "record_spend", staticmethod(lambda *a, **k: 0.0))
    try:
        for name in ("OPENAI", "ANTHROPIC", "GEMINI", "AZURE", "OPENROUTER", "NVIDIA"):
            monkeypatch.delenv(name + "_API_KEY", raising=False)
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "deepseek")
        monkeypatch.setenv("LLM_SPEND_CEILING_USD_DEEPSEEK", "62")
        monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
        monkeypatch.setenv("DEEPSEEK_API_BASE", stub.base_url)
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
        monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
        org = make_org("reviewer-verdict")
        with tenant_ctx(org.id):
            choice = LLMModelRouter().select_reviewer("deepseek", "deepseek-chat")
            assert (choice.provider, choice.model) == ("deepseek", "deepseek-v4-pro")
            answer = LLMModelRouter().call_model(ReviewerChoice(choice.provider, choice.model, True), "Review this.")
    finally:
        stub.close()
    assert answer == VERDICT
    assert [(r["model"], r["thinking"]) for r in stub.requests] == [("deepseek-v4-pro", "enabled")]
