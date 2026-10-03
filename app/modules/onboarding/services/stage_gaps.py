"""Stage-gap comparison — what a company at its stage is expected to have,
compared with what is actually recorded, per onboarding-redesign-v3's three-state
Coverage rule: recorded / worked out / expected-at-your-stage. This module only
ever compares; it never asserts a gap into the model as a fact.

No existing service in this codebase computes organisation-level stage gaps
across company functions (the nearest matches — DomainCompletenessService,
ArchitecturalGapAnalyzer — score a single solution or application, a different
shape of question), so this is new, narrowly-scoped comparison logic over the
stage_baseline.yml data file, not a duplicate of an existing store or scorer.
"""
from __future__ import annotations

import functools
from pathlib import Path

import yaml

_STAGE_ORDER = ["pre_revenue", "early_revenue", "growing", "established"]
_CATEGORIES = ("roles", "functions", "capabilities", "systems")

_DATA_PATH = Path(__file__).resolve().parents[3] / "seed_data" / "onboarding" / "stage_baseline.yml"


@functools.lru_cache(maxsize=1)
def _load_baseline() -> dict:
    with open(_DATA_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def label_for(key: str) -> str:
    """Plain label for a baseline key, falling back to underscores-as-spaces."""
    data = _load_baseline()
    labels = data.get("labels", {})
    return labels.get(key, key.replace("_", " ").capitalize())


def expected_for_stage(stage: str) -> dict:
    """Everything expected at *stage*, additive with every earlier stage.

    Returns {"roles": [...], "functions": [...], "capabilities": [...],
    "systems": [...], "controls": [...]} — controls carry their condition,
    unresolved conditions are left for the caller to evaluate.
    """
    data = _load_baseline()
    if stage not in _STAGE_ORDER:
        stage = "pre_revenue"
    idx = _STAGE_ORDER.index(stage)
    merged = {cat: [] for cat in _CATEGORIES}
    merged["controls"] = []
    for s in _STAGE_ORDER[: idx + 1]:
        stage_data = data.get(s, {})
        for cat in _CATEGORIES:
            merged[cat].extend(stage_data.get(cat, []))
        merged["controls"].extend(stage_data.get("controls", []))
    return merged


def applicable_controls(stage: str, *, region_europe_or_eu_customers: bool, handles_card_data_directly: bool) -> list[dict]:
    """Controls whose condition is met, from the stage's expected list."""
    expected = expected_for_stage(stage)
    triggers = {
        "always": True,
        "region_europe_or_eu_customers": region_europe_or_eu_customers,
        "handles_card_data_directly": handles_card_data_directly,
    }
    return [c for c in expected["controls"] if triggers.get(c.get("condition"), False)]


def compute_gaps(stage: str, recorded: dict, *, region_europe_or_eu_customers: bool = False, handles_card_data_directly: bool = False) -> list[dict]:
    """Compare expected-at-stage against what is recorded.

    *recorded* is {"roles": set-like, "functions": set-like, "capabilities":
    set-like, "systems": set-like, "controls": set-like} of keys already
    present for the organisation. Returns a list of gap dicts, one per
    missing expected item, each {"category", "key", "label"} — never a
    fact, only a comparison result for the UI to render as "expected at
    your stage".
    """
    expected = expected_for_stage(stage)
    gaps = []
    for cat in _CATEGORIES:
        have = set(recorded.get(cat, []) or [])
        for key in expected[cat]:
            if key not in have:
                gaps.append({"category": cat, "key": key, "label": label_for(key)})
    have_controls = set(recorded.get("controls", []) or [])
    for control in applicable_controls(
        stage,
        region_europe_or_eu_customers=region_europe_or_eu_customers,
        handles_card_data_directly=handles_card_data_directly,
    ):
        key = control["key"]
        if key not in have_controls:
            gaps.append({"category": "controls", "key": key, "label": label_for(key)})
    return gaps
