"""The platform's own provider, named only in the environment, is the one chosen."""

from app.modules.ai_chat.services.llm_service_impl import LLMService


def _only_the_named_provider(monkeypatch):
    for name in ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI", "NVIDIA", "OPENROUTER", "HUGGINGFACE", "DEEPSEEK"):
        monkeypatch.delenv(name + "_API_KEY", raising=False)
    monkeypatch.delenv("AI_PROVIDER_ORDER", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "openai")
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_OPENAI", "62")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-not-real")


def test_environment_named_provider_is_chosen_with_no_settings_rows(db_session, tenant_ctx, make_org, monkeypatch):
    _only_the_named_provider(monkeypatch)
    org = make_org("env-provider")
    with tenant_ctx(org.id):
        provider, model = LLMService._get_configured_provider()
    assert provider == "openai"
    assert model


def test_without_the_order_the_same_key_is_not_used(db_session, tenant_ctx, make_org, monkeypatch):
    import pytest

    _only_the_named_provider(monkeypatch)
    monkeypatch.delenv("LLM_PROVIDER_ORDER")
    org = make_org("env-no-order")
    with tenant_ctx(org.id), pytest.raises(ValueError):
        LLMService._get_configured_provider()


def test_a_second_organisation_gets_the_same_platform_provider(db_session, tenant_ctx, make_org, monkeypatch):
    _only_the_named_provider(monkeypatch)
    org_a, org_b = make_org("env-a"), make_org("env-b")
    chosen = []
    for org in (org_a, org_b):
        db_session.expire_all()
        with tenant_ctx(org.id):
            chosen.append(LLMService._get_configured_provider()[0])
    assert chosen == ["openai", "openai"]


def test_without_the_ceiling_the_priced_provider_is_not_called(db_session, tenant_ctx, make_org, monkeypatch):
    import pytest

    _only_the_named_provider(monkeypatch)
    monkeypatch.delenv("LLM_SPEND_CEILING_USD_OPENAI")
    org = make_org("env-no-ceiling")
    with tenant_ctx(org.id), pytest.raises(ValueError):
        LLMService._get_configured_provider()


def test_production_environment_selects_deepseek_chat(db_session, tenant_ctx, make_org, monkeypatch):
    for name in ("OPENAI", "ANTHROPIC", "AZURE", "GEMINI", "NVIDIA", "OPENROUTER", "HUGGINGFACE", "DEEPSEEK"):
        monkeypatch.delenv(name + "_API_KEY", raising=False)
    monkeypatch.delenv("AI_PROVIDER_ORDER", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "deepseek")
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_DEEPSEEK", "62")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key-not-real")
    org = make_org("env-deepseek")
    with tenant_ctx(org.id):
        provider, model = LLMService._get_configured_provider()
    assert (provider, model) == ("deepseek", "deepseek-chat")
