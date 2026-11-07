"""Every reader that sends a provider key resolves it through the one organisation-filtered lookup.

Two organisations hold their own rows for the same provider. Outside a request only the environment key may be
used; inside a request only that organisation's own key. Also: the spend ledger records only calls the provider
accepted, and counts output already streamed.
"""
from __future__ import annotations

import pytest

from app.modules.ai_chat.services.agent_runner import AgentRunner
from app.modules.ai_chat.services.llm_service_impl import PAUSED_MESSAGE, LLMService
from tests.deepseek_stub import KEY
from tests.test_llm_deepseek import _clear_ledger, _ledger, production_shape, stub  # noqa: F401

ENV_KEY = "sk-env-platform-key-never-real-0001"
KEY_A = "sk-org-a-reader-key-never-real-0003"
KEY_B = "sk-org-b-reader-key-never-real-0002"


@pytest.fixture
def reader_orgs(app, monkeypatch):
    """Organisations A and B with their own rows for every provider the readers use; C has none."""
    from app import db
    from app.models.models import APISettings
    from app.models.organization import Organization

    for provider in ("GEMINI", "OPENAI", "ANTHROPIC"):
        monkeypatch.setenv(provider + "_API_KEY", ENV_KEY)
        # The platform's key is sent only while the provider is in the order and under its ceiling.
        monkeypatch.setenv("LLM_SPEND_CEILING_USD_" + provider, "50")
    monkeypatch.delenv("AI_PROVIDER_ORDER", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "openai,anthropic,gemini")
    ids = []
    with app.app_context():
        for label, key in (("a", KEY_A), ("b", KEY_B), ("c", None)):
            org = Organization(name="Readers %s" % label, slug="readers-%s-%d" % (label, id(label) % 100000))
            db.session.add(org)
            db.session.flush()
            if key:
                for provider in ("gemini", "openai", "anthropic"):
                    row = APISettings(provider=provider, enabled=True, default_model="m", organization_id=org.id)
                    row.api_key = key
                    db.session.add(row)
            ids.append(org.id)
        db.session.commit()
    yield ids
    with app.app_context():
        db.session.rollback()
        APISettings.query.filter(APISettings.organization_id.in_(ids)).delete(synchronize_session=False)
        Organization.query.filter(Organization.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()


def _gemini_multimodal():
    from app.modules.architecture.services.multi_modal_llm_service import MultiModalLLMService

    return MultiModalLLMService()._resolve_api_key("gemini")


def _gemini_file_search():
    from app.modules.architecture.services.gemini_file_search_service import GeminiFileSearchService

    return GeminiFileSearchService._resolve_api_key()


def _inference_client():
    from app.modules.architecture.services.inference_providers import _get_llm_client

    client, _model, _kind = _get_llm_client()
    return client.api_key if client is not None else None


def _embedding_key(monkeypatch):
    import openai

    from app.services.vector_embedding_service import VectorEmbeddingService

    seen = []

    class Capture:
        def __init__(self, api_key=None, **_kw):
            seen.append(api_key)
            raise RuntimeError("stop before any network call")

    monkeypatch.setattr(openai, "OpenAI", Capture)
    monkeypatch.setattr(LLMService, "_guard_provider_call", staticmethod(lambda p, m, *a, **k: (p, m, None)))
    service = VectorEmbeddingService.__new__(VectorEmbeddingService)
    import logging

    service.logger = logging.getLogger("test")
    try:
        service.embed_with_openai("text", {"model": "text-embedding-3-small"})
    except Exception:  # noqa: BLE001 - the capture raises on purpose
        pass
    return seen[0] if seen else None


@pytest.fixture(params=["multimodal", "file_search", "inference", "embedding"])
def reader(request, monkeypatch):
    if request.param == "multimodal":
        return _gemini_multimodal
    if request.param == "file_search":
        return _gemini_file_search
    if request.param == "inference":
        return _inference_client
    return lambda: _embedding_key(monkeypatch)


def test_outside_a_request_a_reader_uses_only_the_environment_key(app, reader_orgs, reader):
    with app.app_context():
        assert reader() == ENV_KEY


@pytest.mark.parametrize("index,expected", [(0, KEY_A), (1, KEY_B), (2, ENV_KEY)])
def test_inside_a_request_a_reader_uses_only_that_organisations_key(app, reader_orgs, reader, index, expected):
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = reader_orgs[index]
        assert reader() == expected


# ---- the ceiling ledger -----------------------------------------------------------------------------------

def _runner_with_spy(monkeypatch):
    calls = []
    monkeypatch.setattr(
        AgentRunner, "_record_platform_spend",
        staticmethod(lambda provider, model, raw, prompt_chars=0, output_chars=0: calls.append((prompt_chars, output_chars))),
    )
    return AgentRunner(user_id=1), calls


def test_a_refused_call_records_nothing(app, production_shape, monkeypatch):
    production_shape.accepted = set()  # the provider answers 401 before any output
    runner, calls = _runner_with_spy(monkeypatch)
    with app.app_context():
        with pytest.raises(Exception):
            runner._call_llm("deepseek", "deepseek-chat", KEY, "system", [{"role": "user", "content": "hi"}], [],
                             stream=False, base_url=production_shape.base_url)
    assert calls == []
    assert _ledger(app, "deepseek") is None


def test_a_dropped_stream_records_the_prompt_and_the_output_already_streamed(app, production_shape, monkeypatch):
    production_shape.usage = None
    production_shape.fail_stream_after_first_chunk = True
    runner, calls = _runner_with_spy(monkeypatch)
    with app.app_context():
        with pytest.raises(Exception):
            runner._call_llm("deepseek", "deepseek-chat", KEY, "system", [{"role": "user", "content": "hi"}], [],
                             stream=True, base_url=production_shape.base_url)
    assert len(calls) == 1 and calls[0][0] > 0 and calls[0][1] > 0


def test_a_completed_call_records_once(app, production_shape, monkeypatch):
    runner, calls = _runner_with_spy(monkeypatch)
    with app.app_context():
        runner._call_llm("deepseek", "deepseek-chat", KEY, "system", [{"role": "user", "content": "hi"}], [],
                         stream=False, base_url=production_shape.base_url)
    assert len(calls) == 1
