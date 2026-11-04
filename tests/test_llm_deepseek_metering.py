"""DeepSeek spend is metered on every path the platform pays for, and organisation keys stay first.

Each case is a point the first DeepSeek build missed: the streamed chat recorded nothing, a settings row for
the provider switched metering off, selection ignored the line's order when no order was configured, and a
partial price override zeroed the other direction. Calls go to a stub on loopback; no real provider is reached.
"""
from __future__ import annotations

import pytest

from app.modules.ai_chat.services.agent_runner import AgentRunner
from app.modules.ai_chat.services.llm_service_impl import PAUSED_MESSAGE, AnswersPaused, LLMService
from tests.deepseek_stub import KEY, DeepSeekStub
from tests.test_llm_deepseek import _clear_ledger, _ledger, production_shape, stub  # noqa: F401


def _stream_turn(app, base_url, key=KEY):
    runner = AgentRunner(user_id=1)
    with app.app_context():
        return runner._call_llm("deepseek", "deepseek-chat", key, "system prompt", [{"role": "user", "content": "hi"}],
                                [], stream=True, base_url=base_url)


def test_e_streamed_turns_are_recorded_and_pause_at_the_ceiling(app, production_shape, monkeypatch):
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_DEEPSEEK", "0.002")
    paused_at = None
    for turn in range(10):
        try:
            _stream_turn(app, production_shape.base_url)
        except AnswersPaused as paused:
            assert str(paused).startswith(PAUSED_MESSAGE)
            paused_at = turn
            break
        if turn == 0:
            assert _ledger(app, "deepseek")[0] > 0
    assert paused_at is not None and paused_at < 10
    assert len(production_shape.requests) == paused_at


def test_e_a_stream_without_usage_records_a_non_zero_estimate(app, production_shape):
    production_shape.usage = None
    _stream_turn(app, production_shape.base_url)
    spent, requests = _ledger(app, "deepseek")
    assert requests == 1 and spent > 0


def _org_row(db, key, org=1):
    from app.models.models import APISettings

    row = APISettings(provider="deepseek", enabled=True, default_model="deepseek-chat", organization_id=org)
    row.api_key = key
    db.session.add(row)
    db.session.commit()
    return row


@pytest.fixture
def org_row_cleanup(app):
    yield
    from app import db
    from app.models.models import APISettings

    with app.app_context():
        db.session.rollback()
        APISettings.query.filter_by(provider="deepseek").delete()
        db.session.commit()


def test_f_organisation_row_with_the_platform_key_is_still_metered_and_capped(
    app, production_shape, org_row_cleanup, monkeypatch
):
    from app import db

    monkeypatch.setenv("LLM_SPEND_CEILING_USD_DEEPSEEK", "0.0004")
    with app.app_context():
        _org_row(db, KEY)
    # outside a request, with a row present: recorded, then paused
    _stream_turn(app, production_shape.base_url)
    assert _ledger(app, "deepseek")[1] == 1
    with pytest.raises(AnswersPaused):
        _stream_turn(app, production_shape.base_url)
    # inside organisation 1's request
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = 1
        with pytest.raises(AnswersPaused):
            _stream_turn(app, production_shape.base_url)
        assert LLMService.platform_pays("deepseek", KEY)


def test_f_an_organisations_own_different_key_is_not_charged_to_the_platform(app, production_shape):
    assert LLMService.platform_pays("deepseek", "sk-organisations-own-key") is False
    assert LLMService.platform_pays("deepseek", KEY) is True
    assert _ledger(app, "deepseek") is None


def test_g_no_order_configured_an_organisations_own_openai_key_leads(app, monkeypatch):
    from app import db
    from app.models.models import APISettings

    monkeypatch.delenv("LLM_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("AI_PROVIDER_ORDER", raising=False)
    with app.app_context():
        try:
            for provider in ("openai", "gemini", "nvidia"):
                row = APISettings(provider=provider, enabled=True, default_model="m-" + provider, organization_id=1)
                row.api_key = "sk-own-" + provider
                db.session.add(row)
            db.session.commit()
            assert LLMService._get_configured_provider()[0] == "openai"
        finally:
            db.session.rollback()
            APISettings.query.filter(APISettings.provider.in_(["openai", "gemini", "nvidia"])).delete(
                synchronize_session=False)
            db.session.commit()


def test_h_one_configured_price_leaves_the_other_direction_at_the_list_price(monkeypatch):
    monkeypatch.setenv("LLM_PRICE_PER_MTOK_IN_DEEPSEEK", "0.5")
    monkeypatch.delenv("LLM_PRICE_PER_MTOK_OUT_DEEPSEEK", raising=False)
    assert LLMService.price_per_mtok("deepseek", "deepseek-chat") == (0.5, 0.42)
    monkeypatch.delenv("LLM_PRICE_PER_MTOK_IN_DEEPSEEK")
    monkeypatch.setenv("LLM_PRICE_PER_MTOK_OUT_DEEPSEEK", "0.9")
    assert LLMService.price_per_mtok("deepseek", "deepseek-chat") == (0.28, 0.9)


def test_i_the_real_guard_allows_deepseek(app):
    with app.app_context():
        provider, model, _org = LLMService._guard_provider_call("deepseek", "deepseek-chat")
    assert provider == "deepseek"


def test_label_reads_deepseek(app, production_shape):
    with app.app_context():
        rows = {r["provider"]: r for r in LLMService.spend_ledger_rows()}
    assert rows["deepseek"]["label"] == "DeepSeek"
