"""The website read keeps what it has read, and the model pass falls back across free providers.

A person signs up with their company's website. A slow page must not cost them the pages already read,
and when one free model is rate limited the next one answers. The website's text is public, so free
providers may read it; anything carrying an organisation's own data never reaches a free model.
"""
from __future__ import annotations

import pytest
import requests
import urllib3

from tests.test_website_crawl import (  # noqa: F401  (fixtures and helpers of the crawl's own tests)
    _by_field,
    _crawl,
    _org,
    internal_hosts_allowed,
    other_site,
    site,
)

pytestmark = pytest.mark.usefixtures("db_session")


# ----------------------------------------------------------------------------- the slow page

def test_a_page_that_times_out_mid_read_keeps_the_pages_already_read(make_org, site, monkeypatch):
    """The third page's body stops arriving: urllib3 raises ReadTimeoutError from the raw read."""
    org = _org(make_org, site.url)
    real_get = requests.get

    class Stalled:
        status_code = 200
        headers = {"Content-Type": "text/html"}
        encoding = "utf-8"

        class raw:
            @staticmethod
            def read(*args, **kwargs):
                raise urllib3.exceptions.ReadTimeoutError(None, "/products", "Read timed out.")

        def close(self):
            pass

    def get(url, *args, **kwargs):
        return Stalled() if url.endswith("/products") else real_get(url, *args, **kwargs)

    monkeypatch.setattr(requests, "get", get)
    summary = _crawl(org)

    assert summary["pages_read"] >= 2, "the pages read before and after the slow one are kept"
    assert any(page["url"].endswith("/products") for page in summary["failed"]), summary["failed"]
    found = _by_field(org.id)
    assert found["company_name"][0]["value"] == "Acme Robotics Ltd"
    assert found["hq_city"][0]["value"] == "Bristol", "a fact from the page read after the slow one"


# ----------------------------------------------------------------------------- the model chain

OPENROUTER = ("google/gemma-4-31b-it:free", "meta-llama/llama-3.3-70b-instruct:free", "qwen/qwen-2.5-72b-instruct:free")


@pytest.fixture(autouse=True)
def _no_live_list(monkeypatch):
    """The live OpenRouter list is not read from the network here: the curated list stands."""
    from app.modules.ai_chat.services import model_defaults

    monkeypatch.setattr(model_defaults, "live_free_openrouter_models", lambda: [])


@pytest.fixture
def providers(monkeypatch):
    """Stand-ins for every provider caller. ``calls`` records (provider, model) in the order tried;
    ``script[provider]`` is a list of outcomes per call: an exception to raise or text to return."""
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    calls: list = []
    extras: list = []
    script: dict = {}
    keyed = {"openrouter", "nvidia", "mistral", "cerebras", "groq", "gemini"}

    def caller(provider):
        def call(prompt, model, api_key, max_tokens=None, timeout=None, **extra):
            calls.append((provider, model))
            if provider == "openrouter":
                extras.append(extra)
            outcome = script.get(provider, ["answer from " + provider])
            index = sum(1 for name, _ in calls if name == provider) - 1
            result = outcome[index] if index < len(outcome) else outcome[-1]
            if isinstance(result, Exception):
                raise result
            return result, 10, 10, 0.0
        return call

    for name in ("openrouter", "nvidia", "mistral", "cerebras", "groq", "gemini"):
        monkeypatch.setattr(LLMService, "_call_" + name, staticmethod(caller(name)), raising=False)
    # a public-input call reaches NVIDIA through the one-attempt caller; it is the same stand-in
    monkeypatch.setattr(LLMService, "_call_nvidia_once", staticmethod(caller("nvidia")), raising=False)
    monkeypatch.setattr(LLMService, "_get_all_api_keys",
                        staticmethod(lambda provider: ["key-for-" + provider] if provider in keyed else []))
    monkeypatch.setenv("LLM_SPEND_CEILING_USD_GEMINI", "5")  # the platform's paid provider, within its ceiling
    monkeypatch.setattr(LLMService, "_persist_interaction_record", staticmethod(lambda interaction: None))
    monkeypatch.setattr(LLMService, "_sleep", staticmethod(lambda seconds: None))
    return SimpleNamespaceLike(calls=calls, extras=extras, script=script, keyed=keyed, service=LLMService)


class SimpleNamespaceLike:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _public(providers_):
    return providers_.service.generate_from_prompt("Read this public page.", use_cache=False, timeout=5, public_input=True)


def test_a_rate_limited_model_then_an_error_then_an_answer_takes_the_third_models_facts(providers):
    providers.script["nvidia"] = [RuntimeError("down")]
    providers.script["openrouter"] = [RuntimeError("429 rate limit"), RuntimeError("upstream error"), "third model's facts"]
    assert _public(providers) == "third model's facts"
    assert [(p, m) for p, m in providers.calls if p == "openrouter"] == [("openrouter", m) for m in OPENROUTER]
    assert providers.calls[-1] == ("openrouter", OPENROUTER[2]), "stops at the first answer"
    assert providers.extras and all(extra == {"public_input": True} for extra in providers.extras), providers.extras


def test_the_public_input_chain_is_openrouter_then_nvidia_then_mistral_cerebras_groq_then_gemini(providers):
    from app.modules.ai_chat.services.model_defaults import fallback_models_for

    boom = RuntimeError("unavailable")
    for name in ("openrouter", "nvidia", "mistral", "cerebras"):
        providers.script[name] = [boom]
    providers.script["groq"] = ["groq answers"]
    assert _public(providers) == "groq answers"
    order = []
    for name, _model in providers.calls:
        if not order or order[-1] != name:
            order.append(name)
    assert order == ["openrouter", "nvidia", "mistral", "cerebras", "groq"]
    assert [m for p, m in providers.calls if p == "openrouter"] == fallback_models_for("openrouter", "")
    assert [m for p, m in providers.calls if p == "nvidia"] == fallback_models_for("nvidia", "")
    assert providers.extras and all(extra == {"public_input": True} for extra in providers.extras), providers.extras
    assert ("gemini", "gemini-2.0-flash") not in providers.calls, "stopped at the first answer"


def test_gemini_flash_is_the_last_resort(providers):
    for name in ("openrouter", "nvidia", "mistral", "cerebras", "groq"):
        providers.script[name] = [RuntimeError("down")]
    assert _public(providers) == "answer from gemini"
    assert providers.calls[-1] == ("gemini", "gemini-2.0-flash")


def test_a_provider_with_no_key_is_skipped_silently(providers, caplog):
    providers.keyed -= {"mistral", "cerebras", "groq"}
    providers.script["openrouter"] = [RuntimeError("down")]
    providers.script["nvidia"] = [RuntimeError("down")]
    assert _public(providers) == "answer from gemini"
    assert {p for p, _ in providers.calls}.isdisjoint({"mistral", "cerebras", "groq"})
    assert "mistral" not in caplog.text.lower() or "No API keys" not in caplog.text


def test_when_every_model_fails_the_pass_one_facts_stay_and_nothing_raises(make_org, site, providers):
    from app.modules.organization.website_crawl import default_model

    for name in providers.keyed:
        providers.script[name] = [RuntimeError("down")]
    org = _org(make_org, site.url)
    summary = _crawl(org, llm=default_model(timeout=5))
    assert summary["model_errors"] == 1
    assert _by_field(org.id)["company_name"][0]["value"] == "Acme Robotics Ltd"


# ----------------------------------------------------------------------------- organisation data

def test_a_call_carrying_organisation_data_never_reaches_a_free_model(providers):
    from app.modules.ai_chat.services.llm_service_impl import ProviderNotAllowed

    service = providers.service
    for provider in ("mistral", "cerebras", "groq"):
        with pytest.raises(ProviderNotAllowed):
            service._call_llm_with_failover("Our confidential risk register", "any", provider=provider)
    # the primary fails: the organisation's call falls over to its other paid providers only, never to
    # the curated free backups or the free-only providers
    providers.script["openrouter"] = [RuntimeError("429 rate limit")]
    answer, _ = service._call_llm_with_failover("Our confidential risk register", OPENROUTER[0], provider="openrouter")
    assert answer == "answer from gemini", "it fell over to a paid provider, not a free one"
    assert [m for p, m in providers.calls if p == "openrouter"] == [OPENROUTER[0]], "no free backup model tried"
    assert not {p for p, _ in providers.calls} & {"nvidia", "mistral", "cerebras", "groq"}
    assert providers.extras == [{}], "a call with organisation data keeps the original signature, no public-input flag"

