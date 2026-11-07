"""Every call on the platform's key passes the ceiling and is written to the ledger once.

The readers (Gemini PDF, Gemini file search, element refinement, OpenAI embeddings) and the vision path of the
chat each ask LLMService for a key. An organisation's own key is its own money; the platform's key is sent only
while ``platform_call_allowed`` passes, and a platform-paid call writes exactly one ledger row. Keys are
placeholders; the OpenAI-compatible calls go to a stub on loopback, the Gemini REST calls and the Anthropic and
embeddings clients are replaced by capture shims at the network edge.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile

import pytest

from app.modules.ai_chat.services.agent_runner import AgentRunner
from app.modules.ai_chat.services.llm_service_impl import LLMService
from tests.deepseek_stub import KEY, DeepSeekStub
from tests.test_llm_deepseek import _clear_ledger, _ledger, production_shape, stub  # noqa: F401

ENV = "placeholder-env-platform-key"
KA = "placeholder-org-a-key"
KB = "placeholder-org-b-key"
KD = "placeholder-org-d-anthropic-key"
REFINED = '{"name": "Refined Name Here", "description": "d"}'


@pytest.fixture
def world(app, monkeypatch):
    """Organisations A and B with their own rows, C with none, D with only its own Anthropic row."""
    from app import db
    from app.models.models import APISettings
    from app.models.organization import Organization

    stub_server = DeepSeekStub(reply=REFINED)
    stub_server.accepted = {ENV, KA, KB, KD}
    for p in ("OPENAI", "ANTHROPIC", "GEMINI"):
        monkeypatch.setenv(p + "_API_KEY", ENV)
        monkeypatch.setenv("LLM_SPEND_CEILING_USD_" + p, "50")
        monkeypatch.delenv("LLM_PRICE_PER_MTOK_IN_" + p, raising=False)
        monkeypatch.delenv("LLM_PRICE_PER_MTOK_OUT_" + p, raising=False)
    for p in ("AZURE", "NVIDIA", "OPENROUTER", "HUGGINGFACE", "DEEPSEEK"):
        monkeypatch.delenv(p + "_API_KEY", raising=False)
    monkeypatch.delenv("AI_PROVIDER_ORDER", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "openai,anthropic,gemini")
    monkeypatch.setenv("OPENAI_BASE_URL", stub_server.base_url)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setattr(LLMService, "_guard_provider_call", staticmethod(lambda p, m, *a, **k: (p, m, None)))
    for p in ("openai", "anthropic", "gemini"):
        _clear_ledger(app)
    ids = []
    with app.app_context():
        for label, key, providers in (
            ("a", KA, ("gemini", "openai", "anthropic")),
            ("b", KB, ("gemini", "openai", "anthropic")),
            ("c", None, ()),
            ("d", KD, ("anthropic",)),
        ):
            org = Organization(name="Gate %s" % label, slug="gate-%s-%d" % (label, os.getpid()))
            db.session.add(org)
            db.session.flush()
            for p in providers:
                row = APISettings(provider=p, enabled=True, default_model="model-%s" % label, organization_id=org.id)
                row.api_key = key
                db.session.add(row)
            ids.append(org.id)
        db.session.commit()
    yield stub_server, dict(zip("ABCD", ids))
    stub_server.close()
    with app.app_context():
        db.session.rollback()
        APISettings.query.filter(APISettings.organization_id.in_(ids)).delete(synchronize_session=False)
        Organization.query.filter(Organization.id.in_(ids)).delete(synchronize_session=False)
        db.session.commit()
    _clear_ledger(app)


def _tag(k):
    if k in ("ENV", "A", "B", "D"):
        return k  # already a tag
    return {ENV: "ENV", KA: "A", KB: "B", KD: "D"}.get(k, repr(k)[:12])


class _Resp:
    def __init__(self, body):
        self._b = body
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._b


def _gemini_capture(monkeypatch, module):
    seen = []

    def post(url, params=None, headers=None, json=None, timeout=None, **kw):
        seen.append((params or {}).get("key") or (headers or {}).get("x-goog-api-key"))
        return _Resp({"candidates": [{"content": {"parts": [{"text": '{"elements": [], "relationships": []}'}]}}],
                      "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50}})

    monkeypatch.setattr(module.requests, "post", post)
    return seen


# ---- one driver per reader: returns (keys sent, error text or None, extra) --------------------------------

def run_multimodal(monkeypatch, stub_server):
    from app.modules.architecture.services import multi_modal_llm_service as m

    seen = _gemini_capture(monkeypatch, m)
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.write(fd, b"%PDF-1.4 placeholder")
    os.close(fd)
    err = None
    try:
        asyncio.run(m.MultiModalLLMService().analyze_pdf_with_gemini(path, "describe", model="gemini-2.0-flash"))
    except Exception as e:  # noqa: BLE001
        err = str(e)
    finally:
        os.unlink(path)
    return seen, err, None


def run_file_search(monkeypatch, stub_server):
    from app.modules.architecture.services import gemini_file_search_service as g

    seen = _gemini_capture(monkeypatch, g)
    err = None
    try:
        g.GeminiFileSearchService()._generate_with_file_search("stores/x", "sys", "user", "gemini-2.0-flash")
    except Exception as e:  # noqa: BLE001
        err = str(e)
    return seen, err, None


def _fake_anthropic(monkeypatch):
    import anthropic

    seen = []

    class FakeAnthropic:
        def __init__(self, api_key=None, **kw):
            seen.append(api_key)

            class Messages:
                @staticmethod
                def create(**kw):
                    usage = type("U", (), {"input_tokens": 100, "output_tokens": 20})()
                    text = type("T", (), {"text": REFINED})()
                    return type("R", (), {"content": [text], "usage": usage})()

            self.messages = Messages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    return seen


def run_inference(monkeypatch, stub_server):
    from app.modules.architecture.services import inference_providers as ip

    anth = _fake_anthropic(monkeypatch)
    before = len(stub_server.keys_seen)
    out = ip._llm_refine_element("Goal", "goal one", "ctx", "Motivation")
    sent = [_tag(k) for k in stub_server.keys_seen[before:]] + ["anthropic:" + _tag(k) for k in anth]
    return sent, None, out


def run_embedding(monkeypatch, stub_server):
    import openai

    from app.services.vector_embedding_service import VectorEmbeddingService

    seen = []

    class FakeOpenAI:
        def __init__(self, api_key=None, **kw):
            seen.append(api_key)

            class Embeddings:
                @staticmethod
                def create(**kw):
                    d = type("D", (), {"embedding": [0.1, 0.2]})()
                    u = type("U", (), {"prompt_tokens": 10, "total_tokens": 10})()
                    return type("R", (), {"data": [d], "usage": u})()

            self.embeddings = Embeddings()

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    service = VectorEmbeddingService.__new__(VectorEmbeddingService)
    service.logger = logging.getLogger("test")
    err = None
    try:
        service.embed_with_openai("text", {"model": "text-embedding-3-small"})
    except Exception as e:  # noqa: BLE001
        err = str(e)
    return seen, err, None


def run_vision(monkeypatch, stub_server):
    import openai

    from app.modules.ai_chat.services.multi_domain_chat_service import MultiDomainChatService

    seen = []

    class FakeOpenAI:
        def __init__(self, api_key=None, **kw):
            seen.append(api_key)

            class Completions:
                @staticmethod
                def create(**kw):
                    msg = type("M", (), {"content": "A diagram of two services."})()
                    choice = type("C", (), {"message": msg})()
                    usage = type("U", (), {"prompt_tokens": 100, "completion_tokens": 20})()
                    return type("R", (), {"choices": [choice], "usage": usage})()

            self.chat = type("Chat", (), {"completions": Completions()})()

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(LLMService, "_get_configured_provider",
                        staticmethod(lambda *a, **k: ("openai", "gpt-4o")))
    service = MultiDomainChatService.__new__(MultiDomainChatService)
    result = service._process_vision_message("what is this", "aGVsbG8=", "image/png", "general", {})
    return seen, (result.get("response") or "") if result.get("paused") else None, result


READERS = {
    "multimodal": (run_multimodal, "gemini"),
    "file_search": (run_file_search, "gemini"),
    "inference": (run_inference, "openai"),
    "embedding": (run_embedding, "openai"),
    "vision": (run_vision, "openai"),
}


def _enter(app, org_id):
    ctx = app.app_context() if org_id is None else app.test_request_context("/")
    ctx.push()
    if org_id is not None:
        from flask import g

        g.current_org_id = org_id
    return ctx


def _sent_keys(seen):
    return [s if isinstance(s, str) and (":" in s and not s.startswith("placeholder")) else _tag(s) for s in seen]


def _put_over_ceiling(app, monkeypatch, provider):
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_" + provider.upper(), "0.0001")
    monkeypatch.setenv("LLM_PRICE_PER_MTOK_IN_" + provider.upper(), "2.5")
    monkeypatch.setenv("LLM_PRICE_PER_MTOK_OUT_" + provider.upper(), "10")
    with app.app_context():
        LLMService.record_spend(provider, "m", 100000, 50000)
        assert LLMService.platform_call_allowed(provider)[0] is False
    return _ledger(app, provider)


@pytest.mark.parametrize("reader", list(READERS))
@pytest.mark.parametrize("where", ["outside", "C"])
def test_over_the_ceiling_no_key_is_sent_and_nothing_is_written(app, world, monkeypatch, reader, where):
    stub_server, orgs = world
    driver, provider = READERS[reader]
    before = _put_over_ceiling(app, monkeypatch, provider)
    if reader == "inference":
        # The refiner can use either provider; with only one over its ceiling it correctly takes the other.
        _put_over_ceiling(app, monkeypatch, "anthropic")
    ctx = _enter(app, None if where == "outside" else orgs["C"])
    try:
        seen, err, extra = driver(monkeypatch, stub_server)
    finally:
        ctx.pop()
    assert _sent_keys(seen) == [], "the platform key was sent over its ceiling: %s" % _sent_keys(seen)
    assert _ledger(app, provider) == before
    if reader == "multimodal":
        assert err and "paused" in err.lower()
    elif reader == "file_search":
        assert err and "paused" in err.lower()
    elif reader == "embedding":
        assert err and "unavailable" in err.lower()
    elif reader == "inference":
        assert extra is None
    elif reader == "vision":
        assert err and "paused" in err.lower()


@pytest.mark.parametrize("reader", list(READERS))
@pytest.mark.parametrize("where", ["outside", "C"])
def test_under_the_ceiling_one_platform_paid_call_writes_exactly_one_ledger_row(app, world, monkeypatch, reader, where):
    stub_server, orgs = world
    driver, provider = READERS[reader]
    if reader != "embedding":
        monkeypatch.setenv("LLM_PRICE_PER_MTOK_IN_" + provider.upper(), "2.5")
        monkeypatch.setenv("LLM_PRICE_PER_MTOK_OUT_" + provider.upper(), "10")
    before = _ledger(app, provider) or (0.0, 0)
    ctx = _enter(app, None if where == "outside" else orgs["C"])
    try:
        seen, err, extra = driver(monkeypatch, stub_server)
    finally:
        ctx.pop()
    assert err is None, err
    assert _sent_keys(seen) == ["ENV"]
    after = _ledger(app, provider)
    assert after is not None and after[1] == before[1] + 1, (before, after)
    assert after[0] > before[0], "the call was recorded at no cost: %s -> %s" % (before, after)


@pytest.mark.parametrize("reader", list(READERS))
def test_an_organisations_own_key_is_sent_and_nothing_is_written(app, world, monkeypatch, reader):
    stub_server, orgs = world
    driver, provider = READERS[reader]
    before = _ledger(app, provider)
    ctx = _enter(app, orgs["A"])
    try:
        seen, err, extra = driver(monkeypatch, stub_server)
    finally:
        ctx.pop()
    assert err is None, err
    assert _sent_keys(seen) == ["A"]
    assert _ledger(app, provider) == before


def test_an_organisation_with_only_its_own_anthropic_row_is_answered_with_that_key(app, world, monkeypatch):
    stub_server, orgs = world
    ctx = _enter(app, orgs["D"])
    try:
        seen, err, extra = run_inference(monkeypatch, stub_server)
    finally:
        ctx.pop()
    assert seen == ["anthropic:D"], seen
    assert extra == {"name": "Refined Name Here", "description": "d"}
    assert _ledger(app, "openai") is None and _ledger(app, "anthropic") is None


def test_an_organisation_with_no_row_and_the_platform_over_its_ceiling_gets_no_embedding_call(app, world, monkeypatch):
    from app.services.vector_embedding_service import EmbeddingsUnavailable

    stub_server, orgs = world
    monkeypatch.setenv("ENABLE_LOCAL_EMBEDDINGS", "false")
    before = _put_over_ceiling(app, monkeypatch, "openai")
    ctx = _enter(app, orgs["C"])
    try:
        seen, err, _ = run_embedding(monkeypatch, stub_server)
        # the public entry point: a plain, handled result, not a server error
        from app.services.vector_embedding_service import VectorEmbeddingService

        service = VectorEmbeddingService.__new__(VectorEmbeddingService)
        service.logger = logging.getLogger("test")
        service.embedding_models = VectorEmbeddingService.embedding_models if hasattr(
            VectorEmbeddingService, "embedding_models") else {"openai-small": {"provider": "openai", "model": "text-embedding-3-small"}}
        service._embedding_cache = {}
        service._cache_ttl = 60
        service.enable_local_embeddings = False
        service.default_model = "openai-small"
        with pytest.raises(EmbeddingsUnavailable) as raised:
            service.embed_text("hello")
    finally:
        ctx.pop()
    assert seen == []
    assert "unavailable" in str(raised.value).lower()
    assert isinstance(raised.value, ValueError)
    assert _ledger(app, "openai") == before


def test_embedding_cost_uses_the_embedding_price_from_the_one_price_table(app, world, monkeypatch):
    ctx = _enter(app, None)
    try:
        assert LLMService.price_per_mtok("openai", "text-embedding-3-small") == (0.02, 0.0)
        assert LLMService.price_per_mtok("openai", "gpt-4o") == (0.0, 0.0)
        seen, err, _ = run_embedding(monkeypatch, world[0])
    finally:
        ctx.pop()
    assert err is None
    ledger = _ledger(app, "openai")
    assert ledger is not None and ledger[1] == 1 and ledger[0] > 0


# ---- the image path hands a plain function to the thread ----------------------------------------------------

def test_the_gemini_image_path_reaches_the_provider(app, world, monkeypatch):
    from app.modules.architecture.services import multi_modal_llm_service as m

    seen = _gemini_capture(monkeypatch, m)
    monkeypatch.setenv("LLM_PRICE_PER_MTOK_IN_GEMINI", "2.5")
    fd, path = tempfile.mkstemp(suffix=".png")
    os.write(fd, b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    os.close(fd)
    ctx = _enter(app, None)
    try:
        text, _ = asyncio.run(m.MultiModalLLMService().analyze_image_with_gemini(path, "describe", model="gemini-2.0-flash"))
    finally:
        ctx.pop()
        os.unlink(path)
    assert _sent_keys(seen) == ["ENV"]
    assert text
    assert _ledger(app, "gemini")[1] == 1


# ---- the ledger: a stream that drops after a tool-call chunk ---------------------------------------------------

TOOLS = [{"type": "function", "function": {"name": "search_estate", "description": "d",
                                           "parameters": {"type": "object", "properties": {}}}}]


def test_a_stream_that_drops_after_a_tool_call_chunk_records_the_prompt_and_what_was_streamed(app, production_shape):
    production_shape.usage = None
    production_shape.fail_stream_after_first_chunk = True
    production_shape.tool_script = [("search_estate", {"q": "x" * 300})]
    runner = AgentRunner(user_id=1)
    with app.app_context():
        with pytest.raises(Exception):
            runner._call_llm("deepseek", "deepseek-chat", KEY, "sys" * 300, [{"role": "user", "content": "hi"}], TOOLS,
                             stream=True, base_url=production_shape.base_url)
    ledger = _ledger(app, "deepseek")
    assert ledger is not None and ledger[1] == 1 and ledger[0] > 0, ledger


# ---- the admin Test action ---------------------------------------------------------------------------------------

def test_the_admin_test_action_on_an_own_row_calls_the_provider_and_reports_the_result(
    app, client, db_session, make_org, login_as, monkeypatch
):
    from app.models.models import APISettings
    from app.modules.admin.v2.routes import admin_routes as ar
    from tests.test_admin_nav_offers_only_permitted_links import _make_admin

    admin = _make_admin(db_session, make_org, org_admin=True, platform_admin=False)
    other = make_org("gate-other")
    row_other = APISettings(provider="openai", enabled=True, default_model="m", organization_id=other.id, key_label="b")
    row_other.api_key = KB
    row_own = APISettings(provider="openai", enabled=True, default_model="model-own",
                          organization_id=admin.organization_id, key_label="a")
    row_own.api_key = KA
    db_session.add_all([row_other, row_own])
    db_session.flush()
    calls = []
    outcome = {"success": True, "message": "Key accepted"}
    monkeypatch.setattr(ar, "test_api_key", lambda *a, **k: calls.append((a, k)) or dict(outcome))
    app.config["WTF_CSRF_ENABLED"] = False
    db_session.expunge_all()

    login_as(client, admin)
    ok = client.post("/admin/api-settings/%d/test" % row_own.id, follow_redirects=True)
    assert calls and calls[0][0][:2] == ("openai", KA), calls
    assert b"Key accepted" in ok.data

    outcome.update(success=False, message="The provider refused the key")
    login_as(client, admin)
    refused = client.post("/admin/api-settings/%d/test" % row_own.id, follow_redirects=True)
    assert b"The provider refused the key" in refused.data

    count = len(calls)
    login_as(client, admin)
    assert client.post("/admin/api-settings/%d/test" % row_other.id).status_code == 404
    assert len(calls) == count and all(KB not in c[0] for c in calls)
