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
rule asks to avoid when an existing store already fits.

The five sections' option lists (frameworks, standards, transformation
templates, implementation types) are no longer this module's own constants.
They are Archiet's own organisation-setup reference data, lifted unchanged
into ``app/seed_data/onboarding/`` and read here through ``reference_data.py``
— see that module's docstring and ``SOURCES.md`` beside the data files. This
is row 6 ("Lift") of the Archiet-onboarding-reuse study: the same lists,
Archiet's own five-step maturity scale, and Archiet's own recommended /
common / other grouping by region and industry.

Saving a section no longer only stores the answer here: ``workspace_setup.py``
turns it into the organisation's own records in the model (a Risk per chosen
compliance standard, a capability per framework or build answer, a programme
per transformation template) so answering sets up the workspace rather than
only recording a preference. See that module's docstring for what each answer
becomes and where it is idempotent.
"""
from __future__ import annotations

import datetime

from app.models.organization import Organization

from . import profile, reference_data, workspace_setup

_KEY = "tell_us_more"

_MAX_TEXT = 300
_MAX_PEOPLE = 30
_MAX_NAME = 200

FRAMEWORKS = reference_data.frameworks()

STANDARD_STATUS_OPTIONS = reference_data.MATURITY_STATUS_OPTIONS

# The flat list, every standard, used to validate a save (a standard chosen
# from any group is equally valid). Grouping into recommended / common /
# other is a presentation concern computed at render time from the org's
# already-answered industry — see ``section_for_org`` below — not part of
# this validation list.
STANDARDS = [
    {"key": s["id"], "label": s["name"]} for s in reference_data.compliance_standards()
]

TRANSFORMATION_TEMPLATES = reference_data.transformation_templates()

IMPLEMENTATION_TYPES = reference_data.implementation_types()

DEPLOYMENT_TARGETS = reference_data.DEPLOYMENT_TARGETS

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


def section_for_org(org: Organization, section_key: str) -> dict | None:
    """*section_key*'s definition, with the compliance section's "standards"
    field grouped into recommended / common / other for this organisation --
    Archiet's own grouping (``reference_data.grouped_compliance_standards``),
    read from the region and industry signals Screen 2 already collected
    (``region_europe_or_eu_customers``, the free-text ``industry``). Every
    other section is returned unchanged; validation (``clean_answers``) never
    uses this — it always validates against the flat, ungrouped list, so a
    standard chosen before an industry/region was ever answered stays valid.
    """
    sec = section(section_key)
    if sec is None:
        return None
    if section_key != "compliance":
        return sec

    org_profile = profile.read(org)
    industry = reference_data.industry_value_for_label(org_profile.get("industry") or "")
    region = "europe" if org_profile.get("region_europe_or_eu_customers") else None
    grouped = reference_data.grouped_compliance_standards(region, industry)

    sec = dict(sec)
    sec["fields"] = [
        {**f, "options": grouped} if f["key"] == "standards" else f
        for f in sec["fields"]
    ]
    return sec


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
    """Save and continue: persist the cleaned answers, mark the section
    saved, and set up the organisation's workspace from them (see
    ``workspace_setup.py``). A save always counts as this section's final
    state (never downgraded back to skipped). Re-saving updates the same
    workspace records rather than duplicating them -- ``workspace_setup``'s
    own contract, not re-implemented here."""
    progress = read_progress(org)
    sections = dict(progress.get("sections", {}))
    answers = dict(progress.get("answers", {}))
    cleaned = clean_answers(section_key, raw_answers)
    sections[section_key] = {"status": "saved", "at": _now()}
    answers[section_key] = cleaned
    progress["sections"] = sections
    progress["answers"] = answers
    profile.write(org, **{_KEY: progress})
    workspace_setup.apply_section(org, section_key, cleaned)
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
