"""An organisation's AI requests follow its plan and keys, and never cross to another route.

A free plan is answered by the free models and never spends the paid route. A paid plan is answered
on the paid route and, when that is down, gets the plain pause rather than a free model. An
organisation's own key is used for that organisation alone. Provider callers are patched, so each
test reads which providers were actually asked.
"""
from __future__ import annotations

import pytest

from app.modules.ai_chat.services import agent_runner as ar
from app.modules.ai_chat.services.ai_route import FREE, OWN, PAID, route_for
from app.modules.ai_chat.services.llm_service_impl import PAUSED_MESSAGE, AnswersPaused, LLMService

PLATFORM = ("openai", "anthropic", "gemini", "nvidia", "openrouter", "azure", "huggingface")


@pytest.fixture
def providers(monkeypatch):
    """Platform keys for a free and a paid provider; every caller records (provider, key) and answers."""
    for name in PLATFORM:
        monkeypatch.delenv(name.upper() + "_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("AI_PROVIDER_ORDER", raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "platform-nvidia")
    monkeypatch.setenv("GEMINI_API_KEY", "platform-gemini")
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_GEMINI", "5")
    seen: list = []
    down: set = set()

    def caller(provider):
        def call(prompt, model, api_key, max_tokens=None, timeout=None, **_ignored):
            seen.append((provider, api_key))
            if provider in down:
                raise RuntimeError("503 unavailable")
            return "answer from " + provider, 1, 1, 0.0
        return call

    for name in ("openai", "anthropic", "gemini", "nvidia", "openrouter"):
        monkeypatch.setattr(LLMService, "_call_" + name, staticmethod(caller(name)))
    monkeypatch.setattr(LLMService, "_call_nvidia_once", staticmethod(caller("nvidia")))
    monkeypatch.setattr(LLMService, "_persist_interaction_record", staticmethod(lambda interaction: None))
    monkeypatch.setattr(LLMService, "_sleep", staticmethod(lambda seconds: None))
    monkeypatch.setattr(LLMService, "record_spend", staticmethod(lambda *a, **k: 0.0))
    monkeypatch.setattr(LLMService, "spend_this_month", staticmethod(lambda provider: (0.0, 0)))
    return type("Providers", (), {"seen": seen, "down": down})


def _own_key(db_session, org, provider, key):
    from app.models.models import APISettings

    row = APISettings(provider=provider, api_key=key, enabled=True, default_model="own-model", organization_id=org.id)
    db_session.add(row)
    db_session.flush()


def _paid(db_session, org):
    from app.services import billing_plans

    assert billing_plans.set_contract_plan(org, "team", 5)
    db_session.flush()


def _ask(public=False):
    return LLMService.generate_from_prompt("Summarise this.", use_cache=False, public_input=public)


def test_two_organisations_on_different_plans_each_go_to_their_own_route(
    providers, db_session, make_org, tenant_ctx
):
    free_org, paid_org = make_org("free"), make_org("paid")
    _paid(db_session, paid_org)
    for public in (False, True):
        providers.seen.clear()
        with tenant_ctx(free_org.id):
            assert route_for(free_org.id).kind == FREE
            _ask(public)
        assert {p for p, _ in providers.seen} == {"nvidia"}, "a free plan is answered by the free models only"
        providers.seen.clear()
        db_session.expire_all()
        with tenant_ctx(paid_org.id):
            assert route_for(paid_org.id).kind == PAID
            _ask(public)
        assert {p for p, _ in providers.seen} == {"gemini"}, "a paid plan is answered on the paid route only"


def test_a_free_plan_never_spends_the_paid_route_when_every_free_model_is_down(
    providers, db_session, make_org, tenant_ctx
):
    org = make_org("free")
    providers.down.update({"nvidia", "openrouter"})
    for public in (False, True):
        providers.seen.clear()
        with tenant_ctx(org.id):
            with pytest.raises(Exception) as failure:
                _ask(public)
        assert not isinstance(failure.value, AnswersPaused)
        assert "gemini" not in {p for p, _ in providers.seen}


def test_a_paid_plan_with_the_paid_route_down_gets_the_plain_pause_and_no_free_call(
    providers, db_session, make_org, tenant_ctx
):
    org = make_org("paid")
    _paid(db_session, org)
    providers.down.add("gemini")
    for public in (False, True):
        providers.seen.clear()
        with tenant_ctx(org.id):
            with pytest.raises(AnswersPaused) as paused:
                _ask(public)
        assert str(paused.value).startswith(PAUSED_MESSAGE)
        assert {p for p, _ in providers.seen} <= {"gemini"}, "no free model was asked"


def test_an_organisation_with_its_own_key_uses_that_key_only_and_never_another_organisations(
    providers, db_session, make_org, tenant_ctx
):
    org_a, org_b = make_org("a"), make_org("b")
    _own_key(db_session, org_a, "openai", "key-of-a")
    _own_key(db_session, org_b, "openai", "key-of-b")
    for public in (False, True):
        providers.seen.clear()
        db_session.expire_all()
        with tenant_ctx(org_a.id):
            assert route_for(org_a.id).kind == OWN
            _ask(public)
        assert providers.seen == [("openai", "key-of-a")]
    # its key fails: the plain pause, never the platform's free or paid models
    providers.seen.clear()
    providers.down.add("openai")
    db_session.expire_all()
    with tenant_ctx(org_a.id):
        with pytest.raises(AnswersPaused):
            _ask()
    assert {key for _, key in providers.seen} == {"key-of-a"}


def test_the_assistant_chain_follows_each_organisations_route(providers, db_session, make_org, tenant_ctx, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "platform-openrouter")
    monkeypatch.setenv("OPENAI_API_KEY", "platform-openai")
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "openai,nvidia,openrouter")
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_OPENAI", "5")
    free_org, paid_org, own_org = make_org("free"), make_org("paid"), make_org("own")
    _paid(db_session, paid_org)
    _own_key(db_session, own_org, "anthropic", "own-anthropic")
    db_session.expire_all()
    with tenant_ctx(free_org.id):
        free_chain = ar.build_provider_chain("openai", "gpt-4o-mini")
    db_session.expire_all()
    with tenant_ctx(paid_org.id):
        paid_chain = ar.build_provider_chain("nvidia", "any")
    db_session.expire_all()
    with tenant_ctx(own_org.id):
        own_chain = ar.build_provider_chain("nvidia", "any")
    assert {e[0] for e in free_chain} == {"nvidia", "openrouter"}
    assert {e[0] for e in paid_chain} == {"openai"}
    assert [(e[0], e[2]) for e in own_chain] == [("anthropic", "own-anthropic")]


def test_a_route_that_cannot_read_the_plan_is_never_the_free_one(monkeypatch):
    from app.modules.ai_chat.services import ai_route

    def broken(organization_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(ai_route, "_own_providers", broken)
    assert route_for(12345).kind == PAID
    assert route_for(None).kind == PAID
