"""The AI chat runs on the platform's own keys in a configured order behind a hard stop.

The platform pays for Gemini on a credit project and for the free NVIDIA models, nothing else.
A key in the environment for a provider outside the order is never used, a priced provider
without a spend ceiling is never called, a provider at its ceiling hands over to the next, and
with none left the person reads a plain pause, never an exception. Every call the platform pays
for lands in this month's ledger row at the configured prices.
"""
from __future__ import annotations

import json
import logging
import types
import uuid

import pytest
from sqlalchemy import text

from app.modules.ai_chat.services import llm_service_impl as service_module
from app.modules.ai_chat.services.agent_runner import AgentRunner
from app.modules.ai_chat.services.llm_service_impl import PAUSED_MESSAGE, AnswersPaused, LLMService
from app.modules.ai_chat.services.model_defaults import DEFAULT_MODELS

GEMINI_KEY = "AIza-stub-platform-key-0001"
NVIDIA_KEY = "nvapi-stub-platform-key-0001"
CARD_KEY = "sk-card-billed-key-never-to-be-used-0001"
NVIDIA_ADDRESS = "http://127.0.0.1:1/nvidia-stub/v1"
GEMINI_ADDRESS = "http://127.0.0.1:1/gemini-stub"


class _Transport:
    """The two providers the platform pays for, stubbed; any call towards OpenAI fails the test."""

    def __init__(self):
        self.gemini_calls = []
        self.openai_compatible_calls = []

    def post(self, url, params=None, json=None, timeout=None, **kwargs):
        assert url.startswith(GEMINI_ADDRESS), "the Gemini call went to %s, not the configured address" % url
        self.gemini_calls.append((url, dict(params or {})))
        payload = {
            "candidates": [{"content": {"parts": [{"text": "from the gemini stub"}]}}],
            "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 80, "totalTokenCount": 200},
        }
        return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload, status_code=200, text="")

    def openai_class(self):
        transport = self

        class _Completions:
            def __init__(self, base_url):
                self.base_url = base_url

            def create(self, **kwargs):
                transport.openai_compatible_calls.append((self.base_url, kwargs.get("model")))
                message = types.SimpleNamespace(content="from the nvidia stub", tool_calls=None)
                usage = types.SimpleNamespace(prompt_tokens=7, completion_tokens=3)
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)], usage=usage)

        class _OpenAI:
            def __init__(self, api_key=None, base_url=None, **kwargs):
                if not base_url or "api.openai.com" in base_url:
                    pytest.fail("a request was about to reach OpenAI, a card-billed provider (key %s...)" % api_key[:3])
                self.chat = types.SimpleNamespace(completions=_Completions(base_url))

        return _OpenAI


def _clear_ledger(app):
    from app import db

    with app.app_context():
        with db.engine.begin() as connection:
            connection.execute(text("DELETE FROM llm_spend_ledger"))


def _ledger(app, provider):
    from app import db

    with app.app_context():
        with db.engine.begin() as connection:
            row = connection.execute(
                text("SELECT usd_spent, requests FROM llm_spend_ledger WHERE provider = :p AND month = :m"),
                {"p": provider, "m": LLMService.month_key()},
            ).first()
    return (float(row[0]), int(row[1])) if row else None


@pytest.fixture
def platform(app, monkeypatch):
    """The production shape: the order is Gemini then NVIDIA, both keyed, a card-billed OpenAI key lying around."""
    for name in ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI", "NVIDIA", "OPENROUTER", "DEEPSEEK", "HUGGINGFACE"):
        monkeypatch.delenv(name + "_API_KEY", raising=False)
    for name in ("AI_PROVIDER_ORDER", "LLM_REQUEST_CEILING_NVIDIA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "gemini,nvidia")
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_KEY)
    monkeypatch.setenv("NVIDIA_API_KEY", NVIDIA_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", CARD_KEY)
    monkeypatch.setenv("GEMINI_API_BASE", GEMINI_ADDRESS)
    monkeypatch.setenv("NVIDIA_API_BASE", NVIDIA_ADDRESS)
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_GEMINI", "1.00")
    monkeypatch.setenv("LLM_PRICE_PER_MTOK_IN_GEMINI", "1000")
    monkeypatch.setenv("LLM_PRICE_PER_MTOK_OUT_GEMINI", "2000")

    transport = _Transport()
    import openai

    monkeypatch.setattr(openai, "OpenAI", transport.openai_class())
    monkeypatch.setattr(service_module.requests, "post", transport.post)
    monkeypatch.setattr(LLMService, "_guard_provider_call", staticmethod(lambda p, m, *a, **k: (p, m, None)))
    monkeypatch.setattr(LLMService, "_persist_interaction_record", staticmethod(lambda i: i))
    _clear_ledger(app)
    yield transport
    _clear_ledger(app)


def test_the_order_is_gemini_then_nvidia_and_a_card_billed_key_is_ignored(app, platform, caplog):
    caplog.set_level(logging.DEBUG)
    with app.app_context():
        assert LLMService.provider_order() == ["gemini", "nvidia"]
        assert LLMService._get_configured_provider() == ("gemini", DEFAULT_MODELS["gemini"])
        text_out, _ = LLMService._call_llm_with_failover(prompt="hello", model=DEFAULT_MODELS["gemini"], provider="gemini")
    assert text_out == "from the gemini stub"
    assert len(platform.gemini_calls) == 1 and platform.gemini_calls[0][1] == {"key": GEMINI_KEY}
    assert platform.openai_compatible_calls == []
    # 120 input tokens at USD 1000 per million and 80 output tokens at USD 2000 per million.
    assert _ledger(app, "gemini") == (pytest.approx(0.28, abs=0.00005), 1)
    assert round(_ledger(app, "gemini")[0], 4) == 0.2800
    for secret in (GEMINI_KEY, NVIDIA_KEY, CARD_KEY):
        assert secret not in caplog.text


def test_a_provider_at_its_ceiling_hands_over_to_the_next(app, platform, monkeypatch):
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_GEMINI", "0.25")
    with app.app_context():
        LLMService.record_spend("gemini", DEFAULT_MODELS["gemini"], 120, 80)  # USD 0.28, over the ceiling
        assert LLMService._get_configured_provider() == ("nvidia", DEFAULT_MODELS["nvidia"])
        with pytest.raises(AnswersPaused):
            LLMService._call_llm_with_failover(prompt="hello", model=DEFAULT_MODELS["gemini"], provider="gemini")
        answer = LLMService.generate_from_prompt("hello", use_cache=False)
    assert answer == "from the nvidia stub"
    assert platform.gemini_calls == []
    assert platform.openai_compatible_calls == [(NVIDIA_ADDRESS, DEFAULT_MODELS["nvidia"])]
    assert _ledger(app, "nvidia") == (0.0, 1)


def test_with_every_provider_exhausted_answers_are_paused_in_plain_words(app, platform, monkeypatch):
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_GEMINI", "0.25")
    monkeypatch.setenv("LLM_REQUEST_CEILING_NVIDIA", "1")
    with app.app_context():
        LLMService.record_spend("gemini", DEFAULT_MODELS["gemini"], 120, 80)
        LLMService.record_spend("nvidia", DEFAULT_MODELS["nvidia"], 7, 3)
        with pytest.raises(AnswersPaused) as paused:
            LLMService._get_configured_provider()
        assert str(paused.value).startswith(PAUSED_MESSAGE)
        assert LLMService.configuration_status()["configured"] is False
        # The chat feature stays open: a configured platform at its ceiling answers with the pause,
        # it is not "unconfigured" (that path refuses the request with a 503 the person never reads).
        from app.services.feature_flag_service import FeatureFlagService

        monkeypatch.setitem(app.config, "AI_CHAT_ENABLED", None)
        assert FeatureFlagService.is_ai_enabled("chat") is True

        runner = AgentRunner(user_id=1)
        monkeypatch.setattr(runner, "_gate_mode", lambda: "off")
        monkeypatch.setattr(runner, "_build_system_prompt", lambda *a, **k: "system")
        monkeypatch.setattr(runner, "_record_run", lambda *a, **k: None)
        result = runner.run("What should we do first?")
    assert result["response"] == PAUSED_MESSAGE
    assert result["error"]
    assert platform.gemini_calls == [] and platform.openai_compatible_calls == []


def test_the_chat_route_returns_the_pause_as_an_ordinary_answer(app, platform, monkeypatch):
    from tests.test_ai_chat_stream_error import _login, _make_user

    monkeypatch.setenv("LLM_SPEND_CEILING_USD_GEMINI", "0.25")
    monkeypatch.setenv("LLM_REQUEST_CEILING_NVIDIA", "1")
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setitem(app.config, "AI_CHAT_ENABLED", None)  # the real feature gate decides
    with app.app_context():
        from app import db

        LLMService.record_spend("gemini", DEFAULT_MODELS["gemini"], 120, 80)
        LLMService.record_spend("nvidia", DEFAULT_MODELS["nvidia"], 7, 3)
        user_id = _make_user(db)
    client = app.test_client()
    _login(client, user_id)
    response = client.post(
        "/ai-chat/message",
        data=json.dumps({"message": "What should we do first? %s" % uuid.uuid4().hex[:6]}),
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["response"] == PAUSED_MESSAGE
    assert body["success"] is False
    assert body["agent_error"] is None, "the pause is the whole reply; no error rides beside it"
    assert body["paused"] is True and body["sources"] == [] and body["context_used"] is False
    assert "Traceback" not in response.get_data(as_text=True)
    assert platform.gemini_calls == [] and platform.openai_compatible_calls == []


def test_a_priced_provider_without_a_ceiling_is_never_called(app, platform, monkeypatch):
    monkeypatch.delenv("LLM_SPEND_CEILING_USD_GEMINI")
    with app.app_context():
        assert LLMService._get_configured_provider() == ("nvidia", DEFAULT_MODELS["nvidia"])
        monkeypatch.delenv("NVIDIA_API_KEY")
        with pytest.raises(AnswersPaused):
            LLMService._get_configured_provider()
    assert platform.gemini_calls == []


def test_a_key_outside_the_order_configures_nothing(app, platform, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.delenv("NVIDIA_API_KEY")
    with app.app_context():
        with pytest.raises(ValueError) as refused:
            LLMService._get_configured_provider()
        assert not isinstance(refused.value, AnswersPaused)
        assert "No enabled LLM provider" in str(refused.value)
        assert LLMService.is_available() is False
    assert platform.openai_compatible_calls == []


def test_the_ledger_adds_each_call_at_the_configured_prices(app, platform):
    with app.app_context():
        first = LLMService.record_spend("gemini", DEFAULT_MODELS["gemini"], 100, 50)
        second = LLMService.record_spend("gemini", DEFAULT_MODELS["gemini"], 10, 5)
        rows = {row["provider"]: row for row in LLMService.spend_ledger_rows()}
    assert (round(first, 4), round(second, 4)) == (0.2, 0.02)
    usd, requests = _ledger(app, "gemini")
    assert (round(usd, 4), requests) == (0.22, 2)
    assert [row["provider"] for row in rows.values()] == ["gemini", "nvidia"]
    assert round(rows["gemini"]["usd_spent"], 4) == 0.22
    assert round(rows["gemini"]["remaining_usd"], 4) == 0.78
    assert rows["gemini"]["open"] is True and rows["nvidia"]["open"] is True
    assert rows["nvidia"]["request_ceiling"] == 0 and rows["nvidia"]["ceiling_usd"] is None


def test_the_built_in_gemini_prices_apply_when_none_are_configured(monkeypatch):
    monkeypatch.delenv("LLM_PRICE_PER_MTOK_IN_GEMINI", raising=False)
    monkeypatch.delenv("LLM_PRICE_PER_MTOK_OUT_GEMINI", raising=False)
    assert LLMService.price_per_mtok("gemini", "gemini-2.0-flash") == (0.5, 1.5)
    assert LLMService.price_per_mtok("gemini", "gemini-2.5-pro") == (3.5, 10.5)
    assert LLMService.price_per_mtok("nvidia", "nvidia/nemotron-3-ultra-550b-a55b") == (0.0, 0.0)
    assert LLMService.is_priced("gemini") and not LLMService.is_priced("nvidia")


def test_a_priced_key_outside_the_order_is_refused_at_the_point_of_call(app, platform):
    """A caller that names the provider itself still never reaches a card-billed key: the order and the
    ceilings are enforced where the call is made, not only where the provider is chosen."""
    with app.app_context(), pytest.raises(AnswersPaused) as refused:
        LLMService._call_llm_with_failover("hello", "gpt-4o-mini", provider="openai")
    assert "openai is not in the provider order" in str(refused.value)
    assert platform.openai_compatible_calls == [] and platform.gemini_calls == []


def test_a_priced_provider_in_the_order_with_no_ceiling_is_refused_at_the_point_of_call(app, platform, monkeypatch):
    monkeypatch.delenv("LLM_SPEND_CEILING_USD_GEMINI")
    with app.app_context(), pytest.raises(AnswersPaused) as refused:
        LLMService._call_llm_with_failover("hello", DEFAULT_MODELS["gemini"], provider="gemini")
    assert "no spend ceiling" in str(refused.value)
    assert platform.gemini_calls == []


def test_the_stream_marks_the_pause_and_cites_nothing(app, platform, monkeypatch):
    """The streamed reply carries the pause flag, no error and no source, so the footer names the pause."""
    from tests.test_ai_chat_stream_error import _login, _make_user, _parse_sse_events

    monkeypatch.setenv("LLM_SPEND_CEILING_USD_GEMINI", "0.25")
    monkeypatch.setenv("LLM_REQUEST_CEILING_NVIDIA", "1")
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    monkeypatch.setitem(app.config, "AI_CHAT_ENABLED", None)
    with app.app_context():
        from app import db

        LLMService.record_spend("gemini", DEFAULT_MODELS["gemini"], 120, 80)
        LLMService.record_spend("nvidia", DEFAULT_MODELS["nvidia"], 7, 3)
        user_id = _make_user(db)
    client = app.test_client()
    _login(client, user_id)
    response = client.post(
        "/ai-chat/message/stream",
        data=json.dumps({"message": "What breaks if the ledger fails? %s" % uuid.uuid4().hex[:6]}),
        content_type="application/json",
    )
    body = response.get_data()
    assert response.status_code == 200
    done = [e for e in _parse_sse_events(body) if e.get("type") == "done"]
    assert len(done) == 1, done
    assert done[0]["paused"] is True
    assert done[0]["error"] is None
    assert done[0]["response"] == PAUSED_MESSAGE
    assert not done[0].get("sources") and not done[0].get("answered_by")
    assert platform.gemini_calls == [] and platform.openai_compatible_calls == []


def test_a_provider_without_a_key_is_not_configured_on_the_admin_card(app, platform, monkeypatch, db_session, make_org,
                                                                      client, login_as):
    """NVIDIA is in the order with headroom but no key is stored: the card says Not configured, never Answering."""
    import re

    from tests.test_admin_nav_offers_only_permitted_links import _make_admin

    monkeypatch.delenv("NVIDIA_API_KEY")
    with app.app_context():
        rows = {r["provider"]: r for r in LLMService.spend_ledger_rows()}
    assert rows["nvidia"]["configured"] is False and rows["nvidia"]["open"] is True
    assert rows["gemini"]["configured"] is True

    admin = _make_admin(db_session, make_org, org_admin=True, platform_admin=True)
    login_as(client, admin)
    response = client.get("/admin/api-settings")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    nvidia = re.search(r'data-provider="nvidia".*?</dd>', html, re.DOTALL).group(0)
    gemini = re.search(r'data-provider="gemini".*?</dd>', html, re.DOTALL).group(0)
    assert "Not configured" in nvidia and "Answering" not in nvidia, nvidia
    assert "No key is stored for this provider" in nvidia
    assert "Answering" in gemini and "Not configured" not in gemini, gemini
