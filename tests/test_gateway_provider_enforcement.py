"""Provider restriction enforcement in the gateway.

Tests:
- ModelProvider.is_allowed_for_org resolution (platform default, org override)
- Provider restriction enforced in _call_llm raises ProviderNotAllowed
- Allowed provider proceeds through the gateway
- Interaction persisted with latency
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import db
from app.models import LLMInteraction


class TestIsAllowedForOrg:
    """ModelProvider.is_allowed_for_org resolution logic."""

    def test_platform_default_allowed(self, db_session):
        """Platform-default allowed row permits the provider."""
        from app.models.model_provider import ModelProvider

        db_session.add(ModelProvider(
            provider="anthropic", model_version="claude-opus-5",
            organization_id=None, is_platform_default=True, is_allowed=True,
        ))
        db_session.flush()

        assert ModelProvider.is_allowed_for_org("anthropic", "claude-opus-5", None) is True

    def test_platform_default_blocked(self, db_session):
        """Platform-default blocked row denies the provider organisation-wide."""
        from app.models.model_provider import ModelProvider

        db_session.add(ModelProvider(
            provider="deepseek", model_version="deepseek-chat",
            organization_id=None, is_platform_default=True, is_allowed=False,
        ))
        db_session.flush()

        assert ModelProvider.is_allowed_for_org("deepseek", "deepseek-chat", None) is False

    def test_org_override_allows_blocked_platform(self, db_session, make_org):
        """Org override can re-allow what platform default blocks."""
        from app.models.model_provider import ModelProvider

        org = make_org("override")
        db_session.add(ModelProvider(
            provider="deepseek", model_version="deepseek-chat",
            organization_id=None, is_platform_default=True, is_allowed=False,
        ))
        db_session.add(ModelProvider(
            provider="deepseek", model_version="deepseek-chat",
            organization_id=org.id, is_platform_default=False, is_allowed=True,
        ))
        db_session.flush()

        assert ModelProvider.is_allowed_for_org("deepseek", "deepseek-chat", org.id) is True

    def test_org_override_blocks_allowed_platform(self, db_session, make_org):
        """Org override can block what platform default allows."""
        from app.models.model_provider import ModelProvider

        org = make_org("blocker")
        db_session.add(ModelProvider(
            provider="openai", model_version="gpt-4o",
            organization_id=None, is_platform_default=True, is_allowed=True,
        ))
        db_session.add(ModelProvider(
            provider="openai", model_version="gpt-4o",
            organization_id=org.id, is_platform_default=False, is_allowed=False,
        ))
        db_session.flush()

        assert ModelProvider.is_allowed_for_org("openai", "gpt-4o", org.id) is False
        assert ModelProvider.is_allowed_for_org("openai", "gpt-4o", None) is True

    def test_unknown_provider_allowed_by_default(self, db_session):
        """A provider with no register entry is permitted."""
        from app.models.model_provider import ModelProvider

        assert ModelProvider.is_allowed_for_org("nonexistent-provider", "v1", 1) is True

    def test_org_b_not_affected_by_org_a_block(self, db_session, make_org):
        """Org A's restriction does not affect org B's resolution."""
        from app.models.model_provider import ModelProvider

        org_a = make_org("a")
        org_b = make_org("b")

        db_session.add(ModelProvider(
            provider="openai", model_version="gpt-4o",
            organization_id=org_a.id, is_platform_default=False, is_allowed=False,
        ))
        db_session.flush()

        assert ModelProvider.is_allowed_for_org("openai", "gpt-4o", org_a.id) is False
        assert ModelProvider.is_allowed_for_org("openai", "gpt-4o", org_b.id) is True
        assert ModelProvider.is_allowed_for_org("openai", "gpt-4o", None) is True


class TestProviderRestrictionEnforcement:
    """Gateway enforces the provider register in _call_llm."""

    def test_call_blocked_when_provider_not_allowed(self, db_session, app, make_org):
        """_call_llm raises ProviderNotAllowed when the org restricts the provider."""
        from app.models.model_provider import ModelProvider
        from app.modules.ai_chat.services.llm_service_impl import LLMService, ProviderNotAllowed

        org = make_org("restricted")
        db_session.add(ModelProvider(
            provider="openai", model_version="gpt-4o",
            organization_id=org.id, is_platform_default=False, is_allowed=False,
        ))
        db_session.flush()

        with app.app_context(), pytest.raises(ProviderNotAllowed, match="not allowed"):
            with patch.object(LLMService, "_resolve_org_id", return_value=org.id):
                with patch.object(LLMService, "_get_all_api_keys", return_value=["sk-fake-key"]):
                    LLMService._call_llm(prompt="test", model="gpt-4o", provider="openai")

    def test_call_allowed_when_no_register_entry(self, db_session, make_org):
        """_call_llm proceeds when there is no register entry (default allowed)."""
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        org = make_org("unrestricted")
        with patch.object(LLMService, "_resolve_org_id", return_value=org.id):
            with patch.object(LLMService, "_call_llm_with_failover") as mock:
                mock.return_value = (
                    "ok",
                    LLMInteraction(
                        model_name="gpt-4o", provider="openai",
                        prompt="test", response="ok",
                        token_count_input=5, token_count_output=5, cost=0.001,
                        organization_id=org.id,
                    ),
                )
                response, interaction = LLMService._call_llm(
                    prompt="test", model="gpt-4o", provider="openai",
                )

        assert response == "ok"

    def test_direct_failover_call_resolves_org_and_persists(self, db_session, app, make_org, tenant_ctx):
        """Direct _call_llm_with_failover callers inherit org context and save the interaction."""
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        org = make_org("direct-failover")
        db_session.flush()

        with tenant_ctx(org.id):
            with patch.object(LLMService, "_get_all_api_keys", return_value=["sk-fake-key"]):
                with patch.object(
                    LLMService,
                    "_call_openai",
                    return_value=("ok", 11, 7, 0.0021),
                ):
                    response, interaction = LLMService._call_llm_with_failover(
                        prompt="direct-failover",
                        model="gpt-4o",
                        provider="openai",
                    )

                    assert response == "ok"
                    assert interaction.organization_id == org.id
                    assert interaction.id is not None
                    saved = db_session.get(LLMInteraction, interaction.id)
                    assert saved is not None
                    assert saved.organization_id == org.id
                    assert saved.provider == "openai"
                    assert saved.model_name == "gpt-4o"

    def test_platform_block_applies_without_org_context(self, db_session, app):
        """Platform-default blocks still apply when no organisation is resolved."""
        from app.models.model_provider import ModelProvider
        from app.modules.ai_chat.services.llm_service_impl import LLMService, ProviderNotAllowed

        db_session.add(ModelProvider(
            provider="openai", model_version="gpt-4o",
            organization_id=None, is_platform_default=True, is_allowed=False,
        ))
        db_session.flush()

        with app.app_context(), pytest.raises(ProviderNotAllowed, match="not allowed"):
            with patch.object(LLMService, "_resolve_org_id", return_value=None):
                with patch.object(LLMService, "_get_all_api_keys", return_value=["sk-fake-key"]):
                    with patch.object(LLMService, "_call_openai") as mock_openai:
                        LLMService._call_llm_with_failover(
                            prompt="blocked-without-org",
                            model="gpt-4o",
                            provider="openai",
                        )
        mock_openai.assert_not_called()

    def test_platform_block_with_org_override_only_allows_that_org(self, db_session, app, make_org):
        """Platform block applies with no org and remains isolated from an explicit org allow."""
        from app.models.model_provider import ModelProvider
        from app.modules.ai_chat.services.llm_service_impl import LLMService, ProviderNotAllowed

        org_a = make_org("allow-a")
        org_b = make_org("allow-b")

        db_session.add(ModelProvider(
            provider="openai", model_version="gpt-4o",
            organization_id=None, is_platform_default=True, is_allowed=False,
        ))
        db_session.add(ModelProvider(
            provider="openai", model_version="gpt-4o",
            organization_id=org_a.id, is_platform_default=False, is_allowed=True,
        ))
        db_session.flush()

        with app.app_context():
            with patch.object(LLMService, "_get_all_api_keys", return_value=["sk-fake-key"]):
                with patch.object(
                    LLMService,
                    "_call_openai",
                    return_value=("ok", 2, 3, 0.0004),
                ):
                    with patch.object(LLMService, "_resolve_org_id", return_value=org_a.id):
                        response, interaction = LLMService._call_llm_with_failover(
                            prompt="allowed-org-a",
                            model="gpt-4o",
                            provider="openai",
                        )
                        assert response == "ok"
                        assert interaction.organization_id == org_a.id

                    with patch.object(LLMService, "_resolve_org_id", return_value=org_b.id):
                        with pytest.raises(ProviderNotAllowed, match="not allowed"):
                            LLMService._call_llm_with_failover(
                                prompt="blocked-org-b",
                                model="gpt-4o",
                                provider="openai",
                            )

                    with patch.object(LLMService, "_resolve_org_id", return_value=None):
                        with pytest.raises(ProviderNotAllowed, match="not allowed"):
                            LLMService._call_llm_with_failover(
                                prompt="blocked-no-org",
                                model="gpt-4o",
                                provider="openai",
                            )

    def test_interaction_persisted_with_latency(self, db_session, make_org):
        """A successful call through the gateway persists the LLMInteraction with latency."""
        from app.models.model_provider import ModelProvider
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        org = make_org("latency-test")
        db_session.add(ModelProvider(
            provider="openai", model_version="gpt-4o",
            organization_id=None, is_platform_default=True, is_allowed=True,
        ))
        db_session.flush()

        with patch.object(LLMService, "_resolve_org_id", return_value=org.id):
            with patch.object(LLMService, "_call_llm_with_failover") as mock:
                mock.return_value = (
                    "latency test",
                    LLMInteraction(
                        model_name="gpt-4o", provider="openai",
                        prompt="latency", response="latency test",
                        token_count_input=10, token_count_output=10, cost=0.001,
                        organization_id=org.id,
                        prompt_version="v1", retention_setting="30d",
                    ),
                )
                response, interaction = LLMService._call_llm(
                    prompt="latency", model="gpt-4o", provider="openai",
                    prompt_version="v1", retention_setting="30d",
                )

        assert interaction.id is not None
        fetched = db_session.get(LLMInteraction, interaction.id)
        assert fetched is not None
        assert fetched.organization_id == org.id
        assert fetched.model_name == "gpt-4o"
        assert fetched.provider == "openai"
        assert fetched.prompt_version == "v1"
        assert fetched.retention_setting == "30d"
        assert fetched.latency_ms is not None and fetched.latency_ms >= 0


class TestFallbackBypassPrevention:
    """Fallback to a blocked provider is prevented."""

    def test_fallback_skips_blocked_provider(self, db_session, app, make_org):
        """When both requested and fallback providers are blocked, neither is called.

        Patches the actual _call_<provider> methods with spies and asserts
        that neither spy was invoked when the register blocks both providers.
        """
        from unittest.mock import patch
        from app.models.model_provider import ModelProvider
        from app.modules.ai_chat.services.llm_service_impl import LLMService, ProviderNotAllowed

        org = make_org("fallback-test")

        # Block openai/gpt-4o and deepseek/deepseek-chat for this org
        db_session.add(ModelProvider(
            provider="openai", model_version="gpt-4o",
            organization_id=org.id, is_platform_default=False, is_allowed=False,
        ))
        db_session.add(ModelProvider(
            provider="deepseek", model_version="deepseek-chat",
            organization_id=None, is_platform_default=True, is_allowed=False,
        ))
        db_session.flush()

        with app.app_context():
            with patch.object(LLMService, "_resolve_org_id", return_value=org.id):
                with patch.object(LLMService, "_call_openai") as mock_openai:
                    with patch.object(LLMService, "_call_deepseek") as mock_deepseek:
                        with patch.object(LLMService, "_get_all_api_keys") as mock_keys:
                            # Make key lookup succeed but the calls themselves fail
                            mock_keys.return_value = ["sk-fake-key"]

                            with pytest.raises(ProviderNotAllowed, match="not allowed"):
                                LLMService._call_llm_with_failover(
                                    prompt="test",
                                    model="gpt-4o",
                                    provider="openai",
                                    organization_id=org.id,
                                )

        # Neither spy should have been called (register blocked them before the call)
        mock_openai.assert_not_called()
        mock_deepseek.assert_not_called()

    def test_openrouter_vendor_prefix_obeys_blocked_vendor(self, db_session, app, make_org):
        """An OpenRouter vendor-prefixed model is blocked by the mapped provider rule."""
        from app.models.model_provider import ModelProvider
        from app.modules.ai_chat.services.llm_service_impl import LLMService, ProviderNotAllowed

        org = make_org("openrouter-vendor-block")
        db_session.add(ModelProvider(
            provider="openai", model_version="gpt-4o",
            organization_id=org.id, is_platform_default=False, is_allowed=False,
        ))
        db_session.flush()

        with app.app_context(), pytest.raises(ProviderNotAllowed, match="not allowed"):
            with patch.object(LLMService, "_resolve_org_id", return_value=org.id):
                with patch.object(LLMService, "_get_all_api_keys", return_value=["sk-fake-key"]):
                    with patch.object(LLMService, "_call_openrouter") as mock_openrouter:
                        LLMService._call_llm_with_failover(
                            prompt="blocked-openrouter",
                            model=" OpenAI/GPT-4O ",
                            provider="openrouter",
                        )
        mock_openrouter.assert_not_called()

    def test_model_name_normalization_blocks_variants(self, db_session, app, make_org):
        """Case and whitespace variants of a blocked model are refused."""
        from app.models.model_provider import ModelProvider
        from app.modules.ai_chat.services.llm_service_impl import LLMService, ProviderNotAllowed

        org = make_org("normalized-model-block")
        db_session.add(ModelProvider(
            provider="deepseek", model_version="deepseek-chat",
            organization_id=org.id, is_platform_default=False, is_allowed=False,
        ))
        db_session.flush()

        with app.app_context(), pytest.raises(ProviderNotAllowed, match="not allowed"):
            with patch.object(LLMService, "_resolve_org_id", return_value=org.id):
                with patch.object(LLMService, "_get_all_api_keys", return_value=["sk-fake-key"]):
                    with patch.object(LLMService, "_call_deepseek") as mock_deepseek:
                        LLMService._call_llm_with_failover(
                            prompt="blocked-normalized-model",
                            model="  DeepSeek-Chat  ",
                            provider="DeepSeek ",
                        )
        mock_deepseek.assert_not_called()

    def test_allow_list_only_org_with_no_rows_refuses_and_records(self, db_session, app, make_org, tenant_ctx):
        """An allow-list-only org with no register rows reaches no provider and records the refusal."""
        from app.models.organization import Organization
        from app.modules.ai_chat.services.llm_service_impl import LLMService, ProviderNotAllowed

        org = make_org("allow-list-only")
        org.settings = {"allow_list_only": True}
        db_session.flush()

        with tenant_ctx(org.id):
            with pytest.raises(ProviderNotAllowed, match="not allowed"):
                with patch.object(LLMService, "_resolve_org_id", return_value=org.id):
                    with patch.object(LLMService, "_get_all_api_keys", return_value=["sk-fake-key"]):
                        with patch.object(LLMService, "_call_openai") as mock_openai:
                            LLMService._call_llm_with_failover(
                                prompt="allow-list-only refusal",
                                model="gpt-4o",
                                provider="openai",
                            )
            mock_openai.assert_not_called()

            refusal = (
                db_session.query(LLMInteraction)
                .filter(LLMInteraction.prompt == "allow-list-only refusal")
                .order_by(LLMInteraction.id.desc())
                .first()
            )
            assert refusal is not None
            assert refusal.organization_id == org.id
            assert refusal.provider == "openai"
            assert refusal.model_name == "gpt-4o"
            assert refusal.cost == 0
            assert "not allowed" in (refusal.response or "")


class TestRetentionDefault:
    """retention_setting defaults when not explicitly provided."""

    def test_retention_defaults_when_not_provided(self, db_session, make_org):
        """_call_llm persists non-null gateway defaults when none are passed.

        Goes through _call_llm -> _call_llm_with_failover so the full
        gateway path is exercised.
        """
        from unittest.mock import patch
        from app.models import LLMInteraction
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        org = make_org("retention-default")
        with patch.object(LLMService, "_resolve_org_id", return_value=org.id):
            with patch.object(LLMService, "_call_llm_with_failover") as mock:
                mock.return_value = (
                    "ok",
                    LLMInteraction(
                        model_name="gpt-4o", provider="openai",
                        prompt="test", response="ok",
                        token_count_input=5, token_count_output=5, cost=0.001,
                        organization_id=org.id,
                    ),
                )
                response, interaction = LLMService._call_llm(
                    prompt="test", model="gpt-4o", provider="openai",
                    # No prompt_version or retention_setting passed
                )

        assert response == "ok"
        saved = db_session.get(LLMInteraction, interaction.id)
        assert saved is not None
        assert saved.prompt_version == "unknown"
        assert saved.retention_setting == "30d"


class TestGatewayPersistenceUsesSavepoints:
    """Gateway persistence must not commit or roll back caller work."""

    def test_persisting_interaction_does_not_commit_outer_transaction(self, app):
        """_call_llm leaves caller work rollbackable after interaction persistence."""
        from app.models.organization import Organization
        from app.modules.ai_chat.services.llm_service_impl import LLMService, db as llm_db

        slug = "savepoint-org-commit-check"
        prompt = "savepoint-commit-check"

        with app.app_context():
            session = Session(bind=db.engine)
            try:
                session.add(Organization(name="Savepoint Commit Check", slug=slug))

                with patch.object(llm_db, "session", session):
                    with patch.object(LLMService, "_resolve_org_id", return_value=None):
                        with patch(
                            "app.modules.ai_chat.services.llm_service_impl.LLMCostTracker.check_budget_before_call",
                            return_value=(True, None),
                        ):
                            with patch.object(LLMService, "_call_llm_with_failover") as mock:
                                mock.return_value = (
                                    "ok",
                                    LLMInteraction(
                                        model_name="gpt-4o",
                                        provider="openai",
                                        prompt=prompt,
                                        response="ok",
                                        token_count_input=5,
                                        token_count_output=5,
                                        cost=0.001,
                                    ),
                                )
                                LLMService._call_llm(prompt=prompt, model="gpt-4o", provider="openai")

                session.rollback()
                persisted = session.execute(
                    select(db.func.count()).select_from(Organization).where(Organization.slug == slug)
                ).scalar_one()
            finally:
                session.execute(delete(LLMInteraction).where(LLMInteraction.prompt == prompt))
                session.execute(delete(Organization).where(Organization.slug == slug))
                session.commit()
                session.close()

        assert persisted == 0

    def test_persist_failure_does_not_rollback_caller_transaction(self, app):
        """A failed interaction save leaves caller work intact in the outer transaction."""
        from app.models.organization import Organization
        from app.modules.ai_chat.services.llm_service_impl import LLMService, db as llm_db

        slug = "savepoint-org-rollback-check"
        prompt = "savepoint-rollback-check"

        with app.app_context():
            session = Session(bind=db.engine)
            try:
                session.add(Organization(name="Savepoint Rollback Check", slug=slug))
                real_add = session.add

                def add_with_failure(obj):
                    if isinstance(obj, LLMInteraction):
                        raise RuntimeError("simulated interaction persistence failure")
                    return real_add(obj)

                with patch.object(llm_db, "session", session):
                    with patch.object(LLMService, "_resolve_org_id", return_value=None):
                        with patch(
                            "app.modules.ai_chat.services.llm_service_impl.LLMCostTracker.check_budget_before_call",
                            return_value=(True, None),
                        ):
                            with patch.object(session, "add", side_effect=add_with_failure):
                                with patch.object(LLMService, "_call_llm_with_failover") as mock:
                                    mock.return_value = (
                                        "ok",
                                        LLMInteraction(
                                            model_name="gpt-4o",
                                            provider="openai",
                                            prompt=prompt,
                                            response="ok",
                                            token_count_input=5,
                                            token_count_output=5,
                                            cost=0.001,
                                        ),
                                    )
                                    LLMService._call_llm(prompt=prompt, model="gpt-4o", provider="openai")

                count_inside_txn = session.execute(
                    select(db.func.count()).select_from(Organization).where(Organization.slug == slug)
                ).scalar_one()
            finally:
                session.rollback()
                cleanup = Session(bind=db.engine)
                try:
                    cleanup.execute(delete(LLMInteraction).where(LLMInteraction.prompt == prompt))
                    cleanup.execute(delete(Organization).where(Organization.slug == slug))
                    cleanup.commit()
                finally:
                    cleanup.close()
                session.close()

        assert count_inside_txn == 1
