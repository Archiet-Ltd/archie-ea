"""AI health assessment for the Application Manager persona.

An application manager can see health/lifecycle status today but has no
help deciding what those values should be. This service assembles a
strictly-real context (the owned application's own fields, its primary
vendor product and that product's lifecycle) and asks the LLM for a
structured health assessment: a summary, suggested health/lifecycle
status, the signals behind them, recommended actions, and a rationale.

Advisory only. Nothing here is written back to the application — the
caller (app/modules/my_applications/health_ai_routes.py) must not persist
any part of the response. A suggestion only reaches the record if the
owner follows the "Apply suggestion" link into the existing edit form and
saves it themselves.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from app.modules.ai_chat.services.llm_service_impl import LLMService
from app.services.application_cost_accessor import get_annual_cost

logger = logging.getLogger(__name__)

# The real value sets for these two fields, per app/modules/my_applications/
# crud_routes.py (LIFECYCLE_STATUSES, HEALTH_STATUSES) and the ApplicationComponent
# model's own column comments (app/models/application_portfolio.py). The edit
# form only ever writes one of these values, so a suggestion outside this set
# could never be applied even if the LLM returned it - reject it here instead
# of passing it through.
VALID_LIFECYCLE_STATUSES = {
    "planning", "development", "testing", "operational", "deprecated", "retired",
}
VALID_HEALTH_STATUSES = {"healthy", "at_risk", "critical"}


class ApplicationHealthAIError(Exception):
    """Raised when the LLM response cannot be parsed into a usable assessment.

    Never caught to fabricate a fallback value - per CLAUDE.md, a screen that
    fabricates a plausible value when the real one is missing is worse than
    one that shows nothing.
    """


def _annual_cost_or_none(app):
    cost = get_annual_cost(app)
    return None if cost is None else float(cost)


def _build_context(app) -> Dict[str, Any]:
    """Assemble only the context this function actually reads.

    Every field here is a real column on the owned ApplicationComponent or a
    model it directly links to. Nothing is inferred or invented.
    """
    context: Dict[str, Any] = {
        "name": app.name,
        "description": app.description or None,
        "lifecycle_status": app.lifecycle_status or None,
        "health_status": app.health_status or None,
        "business_criticality": app.business_criticality or None,
        "deployment_status": app.deployment_status or None,
        "total_cost_of_ownership": _annual_cost_or_none(app),
    }

    vendor_product = getattr(app, "primary_vendor_product", None)
    if vendor_product is not None:
        context["vendor_product"] = {
            "name": vendor_product.name,
            "status": vendor_product.status or None,
            "end_of_life_date": vendor_product.end_of_life_date.isoformat()
            if vendor_product.end_of_life_date
            else None,
        }

    return context


def _build_prompt(context: Dict[str, Any]) -> str:
    return (
        "You are assisting an Application Manager who owns one application "
        "in an enterprise portfolio. Below is the real, verified context for "
        "that application - do not invent facts not present in it.\n\n"
        f"Application context (JSON):\n{json.dumps(context, indent=2, default=str)}\n\n"
        "Produce a health assessment for the owner. Respond ONLY with a "
        "single JSON object with exactly these keys:\n"
        '- "summary": string, 2-4 sentences describing the application\'s current health\n'
        '- "suggested_health_status": one of "healthy", "at_risk", "critical" - your '
        "suggested value, not a decision\n"
        '- "suggested_lifecycle_status": one of "planning", "development", "testing", '
        '"operational", "deprecated", "retired" - your suggested value, not a decision\n'
        '- "signals": array of strings, the observations from the context that '
        "support your suggestions\n"
        '- "recommended_actions": array of strings, concrete next steps for the owner\n'
        '- "rationale": string explaining the suggested statuses\n\n'
        "Respond with raw JSON only, no markdown fences, no extra prose."
    )


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(ln for ln in lines if not ln.startswith("```"))
    return raw.strip()


def _parse_assessment(raw: str) -> Dict[str, Any]:
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise ApplicationHealthAIError(
            f"LLM response was not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ApplicationHealthAIError("LLM response was not a JSON object")

    required_keys = {
        "summary",
        "suggested_health_status",
        "suggested_lifecycle_status",
        "signals",
        "recommended_actions",
        "rationale",
    }
    missing = required_keys - data.keys()
    if missing:
        raise ApplicationHealthAIError(
            f"LLM response missing required keys: {sorted(missing)}"
        )

    if data["suggested_health_status"] not in VALID_HEALTH_STATUSES:
        raise ApplicationHealthAIError(
            f"LLM response has an invalid suggested_health_status: "
            f"{data['suggested_health_status']!r}"
        )

    if data["suggested_lifecycle_status"] not in VALID_LIFECYCLE_STATUSES:
        raise ApplicationHealthAIError(
            f"LLM response has an invalid suggested_lifecycle_status: "
            f"{data['suggested_lifecycle_status']!r}"
        )

    for list_key in ("signals", "recommended_actions"):
        if not isinstance(data[list_key], list):
            raise ApplicationHealthAIError(f"LLM response field {list_key!r} was not a list")

    if not isinstance(data["summary"], str) or not isinstance(data["rationale"], str):
        raise ApplicationHealthAIError("LLM response summary/rationale were not strings")

    return {
        "summary": data["summary"],
        "suggested_health_status": data["suggested_health_status"],
        "suggested_lifecycle_status": data["suggested_lifecycle_status"],
        "signals": [str(x) for x in data["signals"]],
        "recommended_actions": [str(x) for x in data["recommended_actions"]],
        "rationale": data["rationale"],
    }


def generate_health_assessment(app) -> Dict[str, Any]:
    """Generate an AI health assessment for an owned application.

    Advisory only - the caller must not persist this as the application's
    actual status. Raises ApplicationHealthAIError (or lets an LLMService
    exception propagate) rather than fabricating a fallback assessment.
    """
    context = _build_context(app)
    prompt = _build_prompt(context)
    raw = LLMService.generate_from_prompt(prompt, use_cache=False)
    return _parse_assessment(raw)
