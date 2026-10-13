"""Archiet's organisation-setup reference lists, lifted as data.

The founder's question was direct: these lists already exist, and companies already answer against
them on Archiet's own products, so "Tell us more" should offer the same choices from one place rather
than a second, hand-written copy. The five JSON files this module reads (``app/seed_data/onboarding/``)
are Archiet's own reference data, copied unchanged -- see ``SOURCES.md`` beside them for exactly which
file came from where.

``grouped_compliance_standards()`` reproduces Archiet's own recommended / common / other split
(``organization-data-loader.ts``'s ``getRecommendedComplianceStandards``) rather than inventing a new
one: recommended is a region's or an industry's own named standards; common is everything else
international, or offered in every region, with a priority of 1 or 2; other is the rest.
"""
from __future__ import annotations

import functools
import json
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parents[3] / "seed_data" / "onboarding"

# compliance-maturity-step.tsx's maturityLevelOptions, copied as-is. The value on the left is what
# Entelim now stores; Archiet's own value is identical, so an id chosen there means the same thing here.
MATURITY_STATUS_OPTIONS = [
    {"key": "none", "label": "Not Implemented", "description": "No current implementation"},
    {"key": "planning", "label": "Planning", "description": "Currently planning implementation"},
    {"key": "partial", "label": "Partially Implemented", "description": "Some requirements met"},
    {"key": "substantial", "label": "Substantially Implemented", "description": "Most requirements met"},
    {"key": "full", "label": "Fully Implemented", "description": "All requirements met and maintained"},
]
_MATURITY_STATUS_KEYS = {o["key"] for o in MATURITY_STATUS_OPTIONS}

# A rough, honestly-labelled reading of each maturity status as a likelihood/impact pair for the
# organisation's Risk record (1-5 each, Risk.likelihood/Risk.impact are both required, non-null
# columns). Not a measurement -- a starting point a person can edit once the risk exists. Lower
# maturity reads as more exposed; "full" still carries a real residual risk, never zero.
_MATURITY_TO_RISK_SCORE = {
    "none": (5, 4),
    "planning": (4, 4),
    "partial": (3, 3),
    "substantial": (2, 3),
    "full": (1, 2),
}


@functools.lru_cache(maxsize=None)
def _load(filename: str) -> dict:
    with open(_DATA_DIR / filename, "r", encoding="utf-8") as fh:
        return json.load(fh)


def maturity_status_label(status_key: str) -> str:
    for opt in MATURITY_STATUS_OPTIONS:
        if opt["key"] == status_key:
            return opt["label"]
    return status_key.replace("_", " ").capitalize()


def maturity_risk_score(status_key: str) -> tuple[int, int]:
    """(likelihood, impact), both 1-5, for a chosen maturity status."""
    return _MATURITY_TO_RISK_SCORE.get(status_key, (3, 3))


# ---------------------------------------------------------------------------
# Organisation metadata: industries, sectors, company sizes, regions, governance maturity
# ---------------------------------------------------------------------------


def industry_categories() -> list[dict]:
    """[{category, items: [{value, label, maturityModifiers}]}], Archiet's own grouping."""
    return _load("organization_metadata.json")["industries"]


def flat_industries() -> list[dict]:
    """[{value, label}], every industry across every category, flattened for a single select."""
    out = []
    for cat in industry_categories():
        for item in cat["items"]:
            out.append({"key": item["value"], "label": item["label"]})
    return out


def industry_value_for_label(label: str) -> str | None:
    """Best-effort match of a free-text industry answer (Screen 2) to Archiet's coded industry
    value, so the compliance grouping below can use it. Exact, case-insensitive label match only --
    never a guess at a value nobody actually said."""
    if not label:
        return None
    needle = label.strip().casefold()
    for item in flat_industries():
        if item["label"].casefold() == needle:
            return item["key"]
    return None


def sectors() -> list[dict]:
    return [{"key": s["value"], "label": s["label"]} for s in _load("organization_metadata.json")["sectors"]]


def company_sizes() -> list[dict]:
    return [
        {"key": s["value"], "label": s["label"]}
        for s in _load("organization_metadata.json")["companySizes"]
    ]


def regions() -> list[dict]:
    return [
        {"key": r["value"], "label": r["label"]}
        for r in _load("organization_metadata.json")["regions"]
    ]


def governance_maturity_levels() -> list[dict]:
    return [
        {"key": g["value"], "label": g["label"]}
        for g in _load("organization_metadata.json")["governanceMaturityLevels"]
    ]


# ---------------------------------------------------------------------------
# Compliance standards, with Archiet's own recommended / common / other grouping
# ---------------------------------------------------------------------------


def compliance_standards() -> list[dict]:
    """The raw standard records (id, name, regions, industries, priority, ...)."""
    return _load("compliance_standards.json")["complianceStandards"]


@functools.lru_cache(maxsize=1)
def _compliance_by_id() -> dict:
    return {s["id"]: s for s in compliance_standards()}


def compliance_standard(standard_id: str) -> dict | None:
    return _compliance_by_id().get(standard_id)


def compliance_standard_keys() -> list[str]:
    return list(_compliance_by_id().keys())


def grouped_compliance_standards(region: str | None, industry: str | None) -> list[dict]:
    """[{key, label, group}], group in "recommended" | "common" | "other" -- the exact split
    ``organization-data-loader.ts``'s ``getRecommendedComplianceStandards`` computes, over a region
    value and an industry value from this same data file's own ``regions``/``industrySpecific`` maps.
    """
    data = _load("compliance_standards.json")
    standards = data["complianceStandards"]
    region_map = data.get("regionMapping", {})
    industry_map = data.get("industrySpecific", {})

    region_recommended = region_map.get(region, {}).get("recommended", []) if region else []
    industry_recommended = industry_map.get(industry, []) if industry else []

    recommended = sorted(
        (s for s in standards if s["id"] in region_recommended or s["id"] in industry_recommended),
        key=lambda s: s.get("priority", 99),
    )
    recommended_ids = {s["id"] for s in recommended}

    common = sorted(
        (
            s for s in standards
            if s["id"] not in recommended_ids
            and (s.get("jurisdiction") == "international" or "all" in (s.get("regions") or []))
            and s.get("priority", 99) <= 2
        ),
        key=lambda s: s.get("priority", 99),
    )
    common_ids = {s["id"] for s in common}

    other = sorted(
        (s for s in standards if s["id"] not in recommended_ids and s["id"] not in common_ids),
        key=lambda s: s.get("priority", 99),
    )

    out = []
    for group_name, group in (("recommended", recommended), ("common", common), ("other", other)):
        for s in group:
            out.append({"key": s["id"], "label": s["name"], "group": group_name})
    return out


# ---------------------------------------------------------------------------
# Frameworks
# ---------------------------------------------------------------------------


def frameworks() -> list[dict]:
    return [
        {"key": f["id"], "label": f["name"], "category": f.get("category")}
        for f in _load("frameworks.json")["frameworks"]
    ]


@functools.lru_cache(maxsize=1)
def _framework_by_id() -> dict:
    return {f["id"]: f for f in _load("frameworks.json")["frameworks"]}


def framework(framework_id: str) -> dict | None:
    return _framework_by_id().get(framework_id)


# ---------------------------------------------------------------------------
# Digital transformation templates
# ---------------------------------------------------------------------------


def transformation_templates() -> list[dict]:
    return [
        {"key": t["id"], "label": t["name"]}
        for t in _load("digital_transformation_templates.json")["templates"]
    ]


@functools.lru_cache(maxsize=1)
def _transformation_template_by_id() -> dict:
    return {t["id"]: t for t in _load("digital_transformation_templates.json")["templates"]}


def transformation_template(template_id: str) -> dict | None:
    """The full template record: name, category, phases, key features, and so on -- used to build
    the programme's work packages (phases) and its capability (category), where the data provides
    them."""
    return _transformation_template_by_id().get(template_id)


# ---------------------------------------------------------------------------
# Implementation types
# ---------------------------------------------------------------------------


def implementation_types() -> list[dict]:
    return [
        {"key": t["id"], "label": t["title"]}
        for t in _load("implementation_types.json")["implementationTypes"]
    ]


@functools.lru_cache(maxsize=1)
def _implementation_type_by_id() -> dict:
    return {t["id"]: t for t in _load("implementation_types.json")["implementationTypes"]}


def implementation_type(type_id: str) -> dict | None:
    return _implementation_type_by_id().get(type_id)


# Entelim's own list -- deployment target is not one of Archiet's onboarding fields (searched
# implementation-planning-step.tsx and onboarding-data-loader.ts for "deployment"/"on_prem"/"hybrid",
# no matches), so this stays a small, hand-kept list rather than a fabricated "lift".
DEPLOYMENT_TARGETS = [
    {"key": "cloud", "label": "Cloud"},
    {"key": "on_prem", "label": "On-premises"},
    {"key": "hybrid", "label": "Hybrid"},
]
