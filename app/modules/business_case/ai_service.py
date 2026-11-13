"""AI drafting assist for Business Case document sections.

The structured business-case document (app/templates/business_case/
detail.html) has five narrative sections a Business Architect fills in by
hand — problem statement, options considered, recommendation, expected
benefits, key risks. This drafts one section at a time from the case's
*other* filled sections plus its own linked capability/initiative/solution
and already-entered financial figures — advisory only. The draft is handed
to the UI's existing inline textarea/save-field flow; nothing here is
written to the case directly.

Financial fields (capex, opex_annual, tco_3yr, roi_percentage,
financial_benefit_annual, payback_months) are numeric inputs backed by
aggregate_financials() (app/modules/business_case/service.py), not
free-text sections, so they are intentionally excluded from what this
service can draft.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from app.modules.ai_chat.services.llm_service_impl import LLMService

logger = logging.getLogger(__name__)

SECTION_KEYS = (
    "problem_statement",
    "options_considered",
    "recommended_option",
    "expected_benefits",
    "key_risks",
)

SECTION_LABELS = {
    "problem_statement": "Problem Statement",
    "options_considered": "Options Considered",
    "recommended_option": "Recommendation",
    "expected_benefits": "Expected Benefits",
    "key_risks": "Key Risks",
}

_FINANCIAL_FIELDS = (
    "capex",
    "opex_annual",
    "tco_3yr",
    "roi_percentage",
    "financial_benefit_annual",
    "payback_months",
)


class BusinessCaseAIDraftError(Exception):
    """Raised when the LLM response cannot be parsed into a usable draft.

    Never caught to fabricate a fallback value — per CLAUDE.md, a screen
    that fabricates a plausible value when the real one is missing is worse
    than one that shows nothing.
    """


def _build_context(business_case, section_key: str, include_financials: bool = True) -> Dict[str, Any]:
    """Assemble only the context this function actually queries: the case's
    other filled-in narrative sections, its linked entities' real names,
    and its own already-entered financial figures. Nothing is inferred or
    invented.
    """
    context: Dict[str, Any] = {
        "title": business_case.title,
        "description": business_case.description or None,
        "status": business_case.status,
        "target_section": section_key,
        "other_sections": {},
    }
    for key in SECTION_KEYS:
        if key == section_key:
            continue
        value = getattr(business_case, key)
        if value:
            context["other_sections"][key] = value

    capability = getattr(business_case, "capability", None)
    if capability is not None:
        context["linked_capability"] = capability.name

    initiative = getattr(business_case, "strategic_initiative", None)
    if initiative is not None:
        context["linked_strategic_initiative"] = initiative.name

    solution = getattr(business_case, "solution", None)
    if solution is not None:
        context["linked_solution"] = solution.name

    financials: Dict[str, Any] = {}
    for field in _FINANCIAL_FIELDS if include_financials else ():
        value = getattr(business_case, field)
        if value is not None:
            financials[field] = float(value) if field != "payback_months" else value
    if financials:
        context["financial_summary"] = financials

    return context


def _build_prompt(context: Dict[str, Any], section_key: str) -> str:
    label = SECTION_LABELS.get(section_key, section_key)
    return (
        "You are assisting a Business Architect writing a Business Case "
        "document. Below is the real, verified context for this case — do "
        "not invent facts not present in it.\n\n"
        f"Business case context (JSON):\n{json.dumps(context, indent=2, default=str)}\n\n"
        f'Draft content for the "{label}" section only. Respond ONLY with a '
        "single JSON object with exactly these keys:\n"
        '- "content": string, the drafted section content\n'
        '- "based_on": array of strings, which pieces of context this draft '
        "leans on (e.g. names of other filled sections or linked entities)\n\n"
        "Respond with raw JSON only, no markdown fences, no extra prose."
    )


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(ln for ln in lines if not ln.startswith("```"))
    return raw.strip()


def _parse_draft(raw: str, section_key: str) -> Dict[str, Any]:
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise BusinessCaseAIDraftError(
            f"LLM response was not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise BusinessCaseAIDraftError("LLM response was not a JSON object")

    required_keys = {"content", "based_on"}
    missing = required_keys - data.keys()
    if missing:
        raise BusinessCaseAIDraftError(
            f"LLM response missing required keys: {sorted(missing)}"
        )

    if not isinstance(data["content"], str):
        raise BusinessCaseAIDraftError("LLM response field 'content' was not a string")
    if not isinstance(data["based_on"], list):
        raise BusinessCaseAIDraftError("LLM response field 'based_on' was not a list")

    return {
        "section": section_key,
        "content": data["content"],
        "based_on": [str(x) for x in data["based_on"]],
    }


def generate_case_seed(description: str) -> Dict[str, Any]:
    """Suggest a title and description for a NEW business case, before it
    exists, from a free-text problem statement.

    Advisory only, like generate_section_draft — the "New Business Case"
    modal pre-fills its title/description fields with the response; the user
    reviews and edits before submitting, and nothing is written here. A field
    the model is not confident about comes back null rather than a guessed
    value (CLAUDE.md "Never invent data").
    """
    prompt = (
        "You are helping populate the creation form for a new Business Case "
        "document from a short free-text problem description written by a "
        "Business Architect.\n\n"
        f'Description:\n"""{description}"""\n\n'
        "Extract ONLY what this description actually supports. If something "
        "is not stated or cannot be confidently inferred, respond with null "
        "for that field — never guess or invent a plausible-sounding value.\n\n"
        "Respond ONLY with a JSON object with exactly these keys:\n"
        '- "title": string or null (a short, specific business-case title, '
        "not the whole description)\n"
        '- "description": string or null (one or two sentences of context)\n\n'
        "Respond with raw JSON only, no markdown fences, no extra prose."
    )
    raw = LLMService.generate_from_prompt(prompt, use_cache=False)
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise BusinessCaseAIDraftError(f"LLM response was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BusinessCaseAIDraftError("LLM response was not a JSON object")

    def _clean(value):
        return value.strip() if isinstance(value, str) and value.strip() else None

    return {"title": _clean(data.get("title")), "description": _clean(data.get("description"))}


def generate_section_draft(business_case, section_key: str, include_financials: bool = True) -> Dict[str, Any]:
    """Generate an AI draft for one Business Case document section.

    Advisory only — the caller must not persist this directly; the UI feeds
    it into the section's existing inline editor and the user saves it via
    the existing save-field path. Raises ValueError for an unknown section
    key (caller maps this to 400), BusinessCaseAIDraftError (or lets an
    LLMService exception propagate) rather than fabricating a fallback.
    """
    if section_key not in SECTION_KEYS:
        raise ValueError(f"Unknown business case section: {section_key}")

    context = _build_context(business_case, section_key, include_financials)
    prompt = _build_prompt(context, section_key)
    raw = LLMService.generate_from_prompt(prompt, use_cache=False)
    return _parse_draft(raw, section_key)
