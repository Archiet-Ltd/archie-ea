"""Model ids must be current, and must live in one place.

Every Claude id in the tree had gone stale, several to RETIRED models that now
return 404 - including the v2 admin default (claude-3-5-sonnet-20241022, retired
2025-10-28) and the example shown to operators in api_settings.html. The newest
Claude referenced anywhere was Haiku 4.5, so the Anthropic path either called a
model that no longer exists or routed enterprise-architecture reasoning through
the cheapest tier available.

They drifted because they were hardcoded in six places. These tests pin both the
currency and the single source of truth.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.modules.ai_chat.services.model_defaults import (
    DEFAULT_MODELS,
    PRICING_PER_1K,
    default_model_for,
    price_for,
)

ROOT = Path(__file__).resolve().parents[1]

# Retired or deprecated ids that must not reappear as a default or as guidance.
RETIRED = [
    "claude-3-5-sonnet-20241022",
    "claude-3-sonnet-20240229",
    "claude-3-opus",
    "claude-3-haiku-20240307",
    "gemini-1.5-flash",
]

# Files that select or advertise a model. Excludes model_defaults.py, whose
# docstring names the retired ids deliberately to explain what it replaced.
SURFACES = [
    "app/modules/ai_chat/services/llm_service_impl.py",
    "app/modules/ai_chat/services/llm_router.py",
    "app/modules/admin/routes/admin_routes.py",
    "app/modules/admin/v2/routes/admin_routes.py",
    "app/utils/ai_rate_limiter.py",
    "app/templates/admin/api_settings.html",
]


def _code_lines(path: Path):
    """Lines excluding comments - a comment naming a retired id is history."""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("*"):
            continue
        yield line


@pytest.mark.parametrize("rel", SURFACES)
def test_no_surface_still_selects_a_retired_model(rel):
    offenders = [
        (line.strip(), dead)
        for line in _code_lines(ROOT / rel)
        for dead in RETIRED
        if dead in line
    ]
    assert not offenders, (
        "%s still references a retired model: %s" % (rel, offenders)
    )


def test_anthropic_default_is_a_current_model():
    assert DEFAULT_MODELS["anthropic"] in {
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5",
    }, DEFAULT_MODELS["anthropic"]


def test_model_ids_carry_no_date_suffix():
    """Current aliases are complete as written; a date suffix is a stale habit
    from the 3.x era and 404s."""
    for provider, model in DEFAULT_MODELS.items():
        if model.startswith("claude-"):
            assert not re.search(r"-20\d{6}$", model), (provider, model)


def test_every_default_has_a_price_or_is_deliberately_unpriced():
    """A price keyed to a model nobody runs makes cost reporting fiction - which
    is how claude-3-opus ended up in the rate limiter's table."""
    for model in ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"):
        assert model in PRICING_PER_1K, model
        assert PRICING_PER_1K[model]["input"] > 0


def test_an_unknown_model_prices_at_zero_rather_than_guessing():
    priced = price_for("some-model-nobody-configured")
    assert priced == {"input": 0.0, "output": 0.0}


def test_default_lookup_is_case_and_space_tolerant():
    assert default_model_for("  Anthropic ") == DEFAULT_MODELS["anthropic"]
    assert default_model_for("nope") == ""


def test_the_rate_limiter_prices_current_models():
    from app.utils.ai_rate_limiter import AIUsageTracker

    assert "claude-opus-5" in AIUsageTracker.MODEL_COSTS
    assert "claude-3-opus" not in AIUsageTracker.MODEL_COSTS


def test_requested_model_resolves_its_provider_too():
    """Overriding `model` without `provider` sent a Claude id to whichever
    provider the resolver happened to pick."""
    source = (
        ROOT / "app/modules/ai_chat/services/agent_runner.py"
    ).read_text(encoding="utf-8")

    block = source.split("if requested_model:", 1)[1][:2000]
    assert "_resolve_requested_model" in block, (
        "the requested model no longer resolves its provider"
    )
    assert "provider, model = resolved" in block


def test_the_settings_page_names_the_model_the_service_uses_for_nvidia():
    from app.modules.ai_chat.services.model_defaults import default_model_for, fallback_models_for

    assert default_model_for("nvidia") == "mistralai/mistral-large-4-1t-a52b"
    page = (ROOT / "app/templates/admin/api_settings.html").read_text(encoding="utf-8")
    assert "<strong>NVIDIA:</strong> %s</p>" % default_model_for("nvidia") in page
    order = fallback_models_for("nvidia", "")
    assert order[:2] == ["mistralai/mistral-large-4-1t-a52b", "nvidia/nemotron-3.5-super-vl-120b-a12b"]
    assert order[-1] == "nvidia/nemotron-3-ultra-550b-a55b"
