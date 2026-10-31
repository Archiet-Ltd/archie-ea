"""Single source of truth for default LLM model ids and their prices.

These were hardcoded in six places that drifted independently, and every Claude
id in the tree had gone stale - several to models that are RETIRED and now
return 404:

    claude-3-5-sonnet-20241022   retired 2025-10-28   was the v2 admin default,
                                                      and the example shown to
                                                      admins in api_settings.html
    claude-3-sonnet-20240229     retired 2025-07-21   llm_service_impl fallback
    claude-3-opus / -sonnet / -haiku   retired/deprecated
    gemini-1.5-flash             retired

The newest Claude referenced anywhere was Haiku 4.5, so the Anthropic path was
either calling a model that no longer exists or routing enterprise-architecture
reasoning through the cheapest tier in the lineup.

    google/gemini-2.5-flash-preview:free   dead on OpenRouter    was the
                                                                  openrouter default

Found live, 8 Sep 2026: a real AI-chat verification run against the actual
OpenRouter API hit "not a valid model ID" on every call. The app's own
provider fallback silently caught it and fell through to deepseek/deepseek-chat
every time, so this had zero user-visible impact -- but it meant the documented
free tier was fiction. OpenRouter's free-tier catalog churns; if this id goes
stale again, list current free models with `GET
https://openrouter.ai/api/v1/models` and filter for ids ending `:free` rather
than guessing.

Operators still choose their own model per provider in Admin -> API Settings
(APISettings.default_model); this is only the fallback used when nothing is
configured, and the guidance shown alongside that field.

Keep DEFAULT_MODELS and PRICING_PER_1K in step. A price keyed to a model nobody
runs makes cost reporting fiction - ai_rate_limiter priced claude-3-opus, a model
that has been retired since January 2026.
"""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

# Provider -> the model to use when the operator has not chosen one.
#
# Anthropic defaults to Opus for reasoning-heavy architecture work; swap to
# claude-sonnet-5 for a cheaper high-volume deployment, or claude-haiku-4-5 for
# classification-style traffic.
DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "openai": "gpt-4o",
    "gemini": "gemini-2.0-flash",
    "azure": "gpt-4o",
    "huggingface": "meta-llama/Llama-2-7b-chat-hf",
    "openrouter": "google/gemma-4-31b-it:free",
    "nvidia": "nvidia/nemotron-3-ultra-550b-a55b",
}

# Ordered free fallback models tried within OpenRouter and NVIDIA before
# moving on to the next provider when the primary free model is rate-limited
# or unavailable.
FALLBACK_MODELS = {
    "openrouter": (
        "google/gemma-4-31b-it:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen-2.5-72b-instruct:free",
        "mistralai/mistral-small-3.1-24b-instruct:free",
        "google/gemma-3-27b-it:free",
        "openrouter/free",
    ),
    "nvidia": (
        "nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/llama-3.1-nemotron-ultra-253b-v1",
        "nvidia/llama-3.1-nemotron-70b-instruct",
        "openai/gpt-oss-20b",
        "google/gemma-4-31b-it",
    ),
}


# Calls that carry only public text (a company's own website) may be answered by free providers.
# This is the order they are tried in; a provider with no key is skipped silently. Calls that carry an
# organisation's data never use the curated free backups below or the providers that only offer free models.
PUBLIC_INPUT_CHAIN = ("openrouter", "nvidia", "mistral", "cerebras", "groq", "gemini")
PUBLIC_INPUT_MODELS = {
    "openrouter": DEFAULT_MODELS["openrouter"],
    "nvidia": DEFAULT_MODELS["nvidia"],
    "mistral": "mistral-small-latest",
    "cerebras": "llama-3.3-70b",
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-2.0-flash",
}
# Providers that are used only for public input.
FREE_ONLY_PROVIDERS = frozenset({"mistral", "cerebras", "groq"})
# The base address of each OpenAI-compatible free provider.
OPENAI_COMPATIBLE_BASE_URLS = {
    "cerebras": "https://api.cerebras.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
}
MISTRAL_CHAT_URL = "https://api.mistral.ai/v1/chat/completions"


def is_free_model(provider: str, model: str = "") -> bool:
    """True when this provider and model are a free tier that must not read an organisation's data."""
    provider = (provider or "").strip().lower()
    model = (model or "").strip().lower()
    return (provider in FREE_ONLY_PROVIDERS or provider == "nvidia"
            or model.endswith(":free") or model == "openrouter/free")


# OpenRouter's free catalogue churns: a slug that answered last week answers "unavailable for free" today.
# The free list is read from its own /models endpoint (ids ending ``:free``), kept 24 hours, and the last
# good list is kept when the endpoint cannot be read.
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
LIVE_FREE_TTL_SECONDS = 24 * 3600
LIVE_FREE_RETRY_SECONDS = 600  # after a failed read, try again no sooner than this
LIVE_FREE_MAX = 8
_live_free = {"read_at": 0.0, "tried_at": 0.0, "ids": []}


def live_free_openrouter_models() -> list[str]:
    """The ids OpenRouter lists today that end ``:free``; cached 24 hours; the last good list on a failed read."""
    now = time.time()
    fresh = _live_free["ids"] and now - _live_free["read_at"] < LIVE_FREE_TTL_SECONDS
    if fresh or now - _live_free["tried_at"] < LIVE_FREE_RETRY_SECONDS and _live_free["ids"]:
        return list(_live_free["ids"])
    _live_free["tried_at"] = now
    try:
        response = requests.get(OPENROUTER_MODELS_URL, timeout=10)
        response.raise_for_status()
        ids = [str(m.get("id", "")) for m in response.json().get("data", [])]
        ids = [i for i in ids if i.endswith(":free")]
        if ids:
            _live_free.update(read_at=now, ids=ids)
    except Exception as error:  # noqa: BLE001 - the last good list (or the curated one) is used instead
        logger.warning("OpenRouter free model list could not be read: %s", str(error)[:100])
    return list(_live_free["ids"])


def _openrouter_free_candidates(explicit: list[str]) -> list[str]:
    """Free OpenRouter models worth trying, newest knowledge first: the curated ones that are still listed,
    then the rest of the live list. A slug the live list does not carry is never tried."""
    live = live_free_openrouter_models()
    if not live:  # never read: nothing is known to be retired, so the curated list stands
        return [m for m in explicit + list(FALLBACK_MODELS["openrouter"]) if m != "openrouter/free"]
    curated = [m for m in explicit + list(FALLBACK_MODELS["openrouter"]) if m in live]
    rest = [m for m in live if m not in curated]
    return (curated + rest)[:LIVE_FREE_MAX]


def fallback_models_for(provider: str, primary_model: str = "") -> list[str]:
    """Return ordered list of models to try for *provider*, starting with *primary_model*."""
    norm = (provider or "").strip().lower()
    explicit = [m.strip() for m in (primary_model or "").split(",") if m.strip()]
    if norm == "openrouter":
        return list(dict.fromkeys(_openrouter_free_candidates(explicit)))
    defaults = list(FALLBACK_MODELS.get(norm, ()))
    seen: list[str] = []
    for candidate in explicit + defaults:
        if candidate and candidate not in seen:
            seen.append(candidate)
    return seen

# A cheaper model for high-volume, low-judgement work (classification, routing,
# summarisation) where the flagship is not worth its cost.
ECONOMY_MODELS = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
}

# USD per 1,000 tokens. Anthropic figures are first-party API list prices;
# partner platforms (Bedrock, Vertex) bill separately.
PRICING_PER_1K = {
    "claude-opus-5": {"input": 0.005, "output": 0.025},
    "claude-sonnet-5": {"input": 0.003, "output": 0.015},
    "claude-haiku-4-5": {"input": 0.001, "output": 0.005},
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
}


def default_model_for(provider: str) -> str:
    """The fallback model id for *provider*, or "" when unknown."""
    return DEFAULT_MODELS.get((provider or "").strip().lower(), "")


def price_for(model: str) -> dict:
    """Per-1k input/output price for *model*.

    Returns zeros for an unrecognised model rather than guessing. A wrong price
    is worse than a visibly absent one: it produces a spend figure a reader
    cannot tell from a measured one.
    """
    return PRICING_PER_1K.get((model or "").strip(), {"input": 0.0, "output": 0.0})
