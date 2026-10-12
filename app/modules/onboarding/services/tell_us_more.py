"""Tell us more (P1 Enrich) — five optional sections, each skippable and
resumable, per onboarding-redesign-v3 §3.

Reuse note (CLAUDE.md §12): the only per-organisation, resumable store this
codebase already has for onboarding facts is ``profile.py``'s reader/writer
over ``Organization.settings["onboarding"]`` — the same place Screen 2's
stage, Screen 3's first-question answer and Screen 4's accepted/assigned gaps
already live. Section progress and section answers are written there too,
under one extra key (``tell_us_more``), rather than a new table: nothing here
needs its own identity, history or relationships a table would buy, it is
exactly the same shape of fact (a per-organisation preference, editable,
resumable) the existing store was built for. "Who's on the team" answers are
kept here as well rather than in ``Stakeholder``/``OrganizationUnit``
(app/models/motivation.py, app/models/enterprise_intelligence.py): both are
global tables with no ``organization_id`` column of their own (tenant scoping,
where it exists at all for either table, is handled elsewhere, per-caller, and
is a known gap the Accountability lens already withdrew itself over — see
reason_codes.py's ``ownership_reader_not_built``); writing a fresh onboarding
answer into either would either add a tenant-isolation bug this brief did not
scope in fixing, or require exactly the kind of new schema work the reuse
rule asks to avoid when an existing store already fits. The five sections'
option lists (frameworks, standards, transformation templates, build
questions) are this module's own small constants, not the vendor pack /
reference-architecture system in srs-v4-onboarding-and-reference-packs.md —
that is a separate, much larger piece of work than this flow.
"""
from __future__ import annotations

import datetime

from app.models.organization import Organization

from . import profile

_KEY = "tell_us_more"

_MAX_TEXT = 300
_MAX_PEOPLE = 30
_MAX_NAME = 200

FRAMEWORKS = [
    {"key": "cobit2019", "label": "COBIT 2019"},
    {"key": "itil_v4", "label": "ITIL v4"},
    {"key": "iso27001", "label": "ISO 27001"},
    {"key": "nist_csf", "label": "NIST CSF"},
    {"key": "togaf", "label": "TOGAF"},
    {"key": "safe", "label": "SAFe"},
    {"key": "agile", "label": "Agile"},
    {"key": "devops", "label": "DevOps"},
    {"key": "lean_six_sigma", "label": "Lean Six Sigma"},
    {"key": "okr", "label": "OKR"},
]

STANDARD_STATUS_OPTIONS = [
    {"key": "not_started", "label": "Not started"},
    {"key": "planning", "label": "Planning"},
    {"key": "partly", "label": "Partly"},
    {"key": "mostly", "label": "Mostly"},
    {"key": "fully", "label": "Fully"},
]

# A starting list, not the region/industry recommendation engine
# (onboarding-intake-v1 §1's compliance-standards.json rule) — that is a
# solution-architect-scoped piece of the reference-pack work, out of scope
# here. Grouping only distinguishes what most organisations meet first.
STANDARDS = [
    {"key": "gdpr", "label": "GDPR / data protection", "group": "recommended"},
    {"key": "iso27001", "label": "ISO 27001", "group": "recommended"},
    {"key": "soc2", "label": "SOC 2", "group": "common"},
    {"key": "pci_dss", "label": "PCI DSS", "group": "common"},
    {"key": "hipaa", "label": "HIPAA", "group": "other"},
    {"key": "nist_csf", "label": "NIST CSF", "group": "other"},
]

TRANSFORMATION_TEMPLATES = [
    {"key": "crm", "label": "CRM"},
    {"key": "erp", "label": "ERP"},
    {"key": "data_platform", "label": "Data platform"},
    {"key": "cloud_migration", "label": "Cloud migration"},
    {"key": "digital_workplace", "label": "Digital workplace"},
    {"key": "ecommerce", "label": "E-commerce"},
]

IMPLEMENTATION_TYPES = [
    {"key": "platform", "label": "Platform (mostly configured, not coded)"},
    {"key": "custom", "label": "Custom-built"},
    {"key": "hybrid", "label": "Hybrid — some platform, some custom"},
]

DEPLOYMENT_TARGETS = [
    {"key": "cloud", "label": "Cloud"},
    {"key": "on_prem", "label": "On-premises"},
    {"key": "hybrid", "label": "Hybrid"},
]

SECTIONS = [
    {
        "key": "how_you_work",
        "title": "How you work",
        "unlock_line": "Sets the words we use and which advanced views to show",
        "fields": [
            {
                "key": "frameworks_in_use",
                "type": "multiselect",
                "label": "Frameworks you already use",
                "options": FRAMEWORKS,
            },
            {
                "key": "frameworks_want",
                "type": "multiselect",
                "label": "Frameworks you'd like to adopt",
                "options": FRAMEWORKS,
            },
        ],
    },
    {
        "key": "compliance",
        "title": "What you must comply with",
        "unlock_line": "Answers ‘which risks…’ and adds controls to your model",
        "fields": [
            {
                "key": "standards",
                "type": "status_list",
                "label": "Where you stand on each standard",
                "options": STANDARDS,
                "statuses": STANDARD_STATUS_OPTIONS,
            },
        ],
    },
    {
        "key": "whats_changing",
        "title": "What you're changing",
        "unlock_line": "Adds projects and the capabilities they need; unlocks Programme",
        "fields": [
            {
                "key": "transformation_templates",
                "type": "multiselect",
                "label": "What are you changing right now?",
                "options": TRANSFORMATION_TEMPLATES,
            },
            {
                "key": "transformation_other",
                "type": "text",
                "label": "Anything else (optional)",
            },
        ],
    },
    {
        "key": "how_you_build",
        "title": "How you build",
        "unlock_line": "Sets what a company like yours is expected to have in engineering",
        "fields": [
            {
                "key": "implementation_type",
                "type": "select",
                "label": "How do you mostly build?",
                "options": IMPLEMENTATION_TYPES,
            },
            {
                "key": "has_dev_team",
                "type": "boolean",
                "label": "Do you have a development team?",
            },
            {
                "key": "stack",
                "type": "text",
                "label": "Your stack (optional)",
            },
            {
                "key": "deployment_target",
                "type": "select",
                "label": "Where do you deploy?",
                "options": DEPLOYMENT_TARGETS,
            },
            {
                "key": "integrations",
                "type": "text",
                "label": "Integrations (optional)",
            },
        ],
    },
    {
        "key": "team",
        "title": "Who's on the team",
        "unlock_line": "Removes ‘no one owns…’ gaps; unlocks Accountability",
        "fields": [
            {
                "key": "people",
                "type": "people_list",
                "label": "Who's on the team, and what do they do",
            },
        ],
    },
]

_SECTION_BY_KEY = {s["key"]: s for s in SECTIONS}


def section(section_key: str) -> dict | None:
    return _SECTION_BY_KEY.get(section_key)


def _now() -> str:
    return datetime.datetime.utcnow().isoformat()


def read_progress(org: Organization) -> dict:
    """The whole tell-us-more subtree: {"sections": {key: {status, at}},
    "answers": {key: {...}}}. Never a fact by itself — a resumable draft."""
    onboarding = profile.read(org)
    data = dict(onboarding.get(_KEY, {}))
    data.setdefault("sections", {})
    data.setdefault("answers", {})
    return data


def section_statuses(org: Organization) -> dict:
    """{section_key: "saved" | "skipped" | "not_started"} for every section,
    in section order, so a resumed hub always lists all five."""
    progress = read_progress(org)
    saved = progress.get("sections", {})
    return {
        s["key"]: saved.get(s["key"], {}).get("status", "not_started")
        for s in SECTIONS
    }


def answers_for(org: Organization, section_key: str) -> dict:
    progress = read_progress(org)
    return dict(progress.get("answers", {}).get(section_key, {}))


def _clean_multiselect(raw, options: list[dict]) -> list[str]:
    valid = {o["key"] for o in options}
    if not isinstance(raw, list):
        return []
    seen = []
    for v in raw:
        if isinstance(v, str) and v in valid and v not in seen:
            seen.append(v)
    return seen


def _clean_select(raw, options: list[dict]):
    valid = {o["key"] for o in options}
    if isinstance(raw, str) and raw in valid:
        return raw
    return None


def _clean_text(raw):
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()[:_MAX_TEXT]
    return cleaned or None


def _clean_boolean(raw):
    if isinstance(raw, bool):
        return raw
    return None


def _clean_status_list(raw, field: dict):
    valid_keys = {o["key"] for o in field["options"]}
    valid_statuses = {o["key"] for o in field["statuses"]}
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for key, status in raw.items():
        if key in valid_keys and status in valid_statuses:
            cleaned[key] = status
    return cleaned


def _clean_people_list(raw):
    if not isinstance(raw, list):
        return []
    cleaned = []
    for entry in raw[:_MAX_PEOPLE]:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").strip()[:_MAX_NAME]
        does = (entry.get("does") or "").strip()[:_MAX_NAME]
        if name or does:
            cleaned.append({"name": name, "does": does})
    return cleaned


def clean_answers(section_key: str, raw_answers: dict) -> dict:
    """Validate *raw_answers* against *section_key*'s field definitions.
    Unknown fields and invalid option values are dropped rather than
    rejected outright, so a partially-filled optional section still saves
    what is valid."""
    sec = section(section_key)
    if sec is None or not isinstance(raw_answers, dict):
        return {}
    cleaned = {}
    for field in sec["fields"]:
        raw = raw_answers.get(field["key"])
        if raw is None:
            continue
        if field["type"] == "multiselect":
            value = _clean_multiselect(raw, field["options"])
            if value:
                cleaned[field["key"]] = value
        elif field["type"] == "select":
            value = _clean_select(raw, field["options"])
            if value is not None:
                cleaned[field["key"]] = value
        elif field["type"] == "text":
            value = _clean_text(raw)
            if value is not None:
                cleaned[field["key"]] = value
        elif field["type"] == "boolean":
            value = _clean_boolean(raw)
            if value is not None:
                cleaned[field["key"]] = value
        elif field["type"] == "status_list":
            value = _clean_status_list(raw, field)
            if value:
                cleaned[field["key"]] = value
        elif field["type"] == "people_list":
            value = _clean_people_list(raw)
            if value:
                cleaned[field["key"]] = value
    return cleaned


def save_section(org: Organization, section_key: str, raw_answers: dict) -> dict:
    """Save and continue: persist the cleaned answers and mark the section
    saved. A save always counts as this section's final state (never
    downgraded back to skipped)."""
    progress = read_progress(org)
    sections = dict(progress.get("sections", {}))
    answers = dict(progress.get("answers", {}))
    sections[section_key] = {"status": "saved", "at": _now()}
    answers[section_key] = clean_answers(section_key, raw_answers)
    progress["sections"] = sections
    progress["answers"] = answers
    profile.write(org, **{_KEY: progress})
    return progress


def skip_section(org: Organization, section_key: str) -> dict:
    """Skip: advances without saving new answers. If the section was already
    saved, skip is a no-op on its status (a real save is never downgraded)."""
    progress = read_progress(org)
    sections = dict(progress.get("sections", {}))
    if sections.get(section_key, {}).get("status") != "saved":
        sections[section_key] = {"status": "skipped", "at": _now()}
    progress["sections"] = sections
    profile.write(org, **{_KEY: progress})
    return progress


def next_section_key(section_key: str) -> str | None:
    keys = [s["key"] for s in SECTIONS]
    try:
        idx = keys.index(section_key)
    except ValueError:
        return keys[0] if keys else None
    return keys[idx + 1] if idx + 1 < len(keys) else None
