"""The platform answers through DeepSeek, behind its spend ceiling.

Production runs with LLM_PROVIDER_ORDER=deepseek, a DeepSeek key and a spend ceiling. Until DeepSeek
was a provider the service knew, every question died with "No enabled LLM provider found with a
configured model". These tests call a stub DeepSeek over loopback HTTP (no real provider is reached).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.modules.ai_chat.services import llm_service_impl as service_module
from app.modules.ai_chat.services.agent_runner import AgentRunner
from app.modules.ai_chat.services.llm_service_impl import PAUSED_MESSAGE, AnswersPaused, LLMService
from tests.deepseek_stub import KEY, DeepSeekStub


def _ledger(app, provider):
    from app import db

    with app.app_context():
        with db.engine.begin() as connection:
            row = connection.execute(
                text("SELECT usd_spent, requests FROM llm_spend_ledger WHERE provider = :p AND month = :m"),
                {"p": provider, "m": LLMService.month_key()},
            ).first()
    return (float(row[0]), int(row[1])) if row else None


def _clear_ledger(app):
    from app import db

    with app.app_context():
        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM llm_spend_ledger"))


@pytest.fixture
def stub():
    s = DeepSeekStub(reply="deepseek says ready")
    yield s
    s.close()


@pytest.fixture
def production_shape(app, stub, monkeypatch):
    for name in ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI", "NVIDIA", "OPENROUTER", "HUGGINGFACE"):
        monkeypatch.delenv(name + "_API_KEY", raising=False)
    monkeypatch.delenv("AI_PROVIDER_ORDER", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
    monkeypatch.setenv("DEEPSEEK_API_BASE", stub.base_url)
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_DEEPSEEK", "62")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setattr(LLMService, "_guard_provider_call", staticmethod(lambda p, m, *a, **k: (p, m, None)))
    monkeypatch.setattr(LLMService, "_persist_interaction_record", staticmethod(lambda i: i))
    _clear_ledger(app)
    yield stub
    _clear_ledger(app)


def test_a_only_the_deepseek_key_answers_and_spend_is_recorded(app, production_shape):
    with app.app_context():
        assert LLMService.is_priced("deepseek")
        assert LLMService._get_configured_provider() == ("deepseek", "deepseek-chat")
        answer = LLMService.generate_from_prompt("Reply with exactly the word: ready", use_cache=False)
    assert answer == "deepseek says ready"
    assert [r["model"] for r in production_shape.requests] == ["deepseek-chat"]
    spent, requests = _ledger(app, "deepseek")
    assert requests == 1 and spent == pytest.approx(1000 * 0.28 / 1e6 + 500 * 0.42 / 1e6, abs=1e-6)


def test_b_an_enabled_settings_row_for_deepseek_is_chosen(app, production_shape, monkeypatch):
    from app import db
    from app.models.models import APISettings

    from flask import g

    monkeypatch.delenv("DEEPSEEK_API_KEY")
    with app.app_context():
        g.current_org_id = 1  # an organisation's own row counts only for that organisation
        row = APISettings(provider="deepseek", enabled=True, default_model="deepseek-chat", organization_id=1)
        row.set_api_key(KEY) if hasattr(row, "set_api_key") else setattr(row, "api_key", KEY)
        db.session.add(row)
        db.session.commit()  # selection commits first thing, so the row is real; removed below
        try:
            assert LLMService._get_configured_provider() == ("deepseek", "deepseek-chat")
        finally:
            db.session.rollback()
            APISettings.query.filter_by(provider="deepseek").delete()
            db.session.commit()


def test_c_at_the_ceiling_the_call_pauses_and_never_reaches_the_stub(app, production_shape, monkeypatch):
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_DEEPSEEK", "0.0001")
    with app.app_context():
        LLMService.record_spend("deepseek", "deepseek-chat", 10000, 10000)  # well over the ceiling
        with pytest.raises(AnswersPaused) as paused:
            LLMService.generate_from_prompt("hello", use_cache=False)
        assert str(paused.value).startswith(PAUSED_MESSAGE)
    assert production_shape.requests == []


def test_d_a_tool_using_agent_turn_works_and_every_tool_name_passes_deepseeks_rule(app, production_shape, monkeypatch):
    from app.modules.ai_chat.tools.registry import TOOL_SCHEMAS

    production_shape.reply = "Three applications are recorded."
    assert any(s["name"] == "search_elements" for s in TOOL_SCHEMAS)
    production_shape.tool_script = [("search_elements", {"query": "payments"})]
    with app.app_context():
        runner = AgentRunner(user_id=1)
        monkeypatch.setattr(runner, "_gate_mode", lambda: "off")
        monkeypatch.setattr(runner, "_build_system_prompt", lambda *a, **k: "system")
        monkeypatch.setattr(runner, "_record_run", lambda *a, **k: None)
        result = runner.run("How many applications do we run?")
    assert not result.get("error"), result
    assert result["response"] == "Three applications are recorded."
    # the tool turn, then the answer turn that carries the tool's result; both offered tools by valid names
    assert len(production_shape.requests) == 2 and all(r["tool_names"] for r in production_shape.requests)
    assert len(production_shape.requests[0]["tool_names"]) > 50
    assert result["answered_by"]["provider"] == "deepseek"
    assert _ledger(app, "deepseek")[1] >= 1
