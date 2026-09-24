"""ARB review pre-brief — AI assist for the reviewer side of the workflow.

Entelim already lets a submitter draft an ARB submission with AI. A reviewer
opening a review gets nothing today: no pre-brief, no conflict surfacing, no
draft disposition. This service assembles a strictly-real context (the
review item's own fields, its linked Solution, its linked ADR, and this
org's active Principles) and asks the LLM for a structured pre-brief. It is
advisory only: nothing here is written back to the review, and the caller
(app/modules/architecture/routes/arb_routes.py) must not persist any part of
the response as if it were the reviewer's decision.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from app.modules.ai_chat.services.llm_service_impl import LLMService

logger = logging.getLogger(__name__)

VALID_DISPOSITIONS = {"approved", "approved_with_conditions", "rejected", "deferred"}

MAX_PRINCIPLES = 10


class ARBReviewAIPrebriefError(Exception):
    """Raised when the LLM response cannot be parsed into a usable pre-brief.

    Never caught to fabricate a fallback value — per CLAUDE.md, a screen that
    fabricates a plausible value when the real one is missing is worse than
    one that shows nothing.
    """


def _build_context(review_item) -> Dict[str, Any]:
    """Assemble only the context this function actually queries.

    Every field here is a real value read from the review item or a model
    it directly links to (or, for principles, a cheap org-scoped query).
    Nothing is inferred or invented.
    """
    context: Dict[str, Any] = {
        "review_number": review_item.review_number,
        "title": review_item.title,
        "description": review_item.description or None,
        "review_type": review_item.review_type,
        "togaf_phase": review_item.togaf_phase,
        "priority": review_item.priority,
        "business_impact": review_item.business_impact,
        "estimated_effort": review_item.estimated_effort,
        "status": review_item.status,
        "governance_checklist": review_item.governance_checklist or None,
        "compliance_score": review_item.compliance_score,
        "risk_score": review_item.risk_score,
        "quality_score": review_item.quality_score,
        "overall_score": review_item.overall_score,
    }

    solution = getattr(review_item, "solution", None)
    if solution is not None:
        context["solution"] = {
            "name": solution.name,
            "description": getattr(solution, "description", None) or None,
        }

    adr = getattr(review_item, "adr", None)
    if adr is not None:
        context["linked_adr"] = {
            "title": adr.title,
            "status": adr.status,
        }

    # Cheap, org-scoped: the active Principles for this org. TenantMixin
    # filters this to the current organization inside a request context.
    try:
        from app.models.models import Principle

        principles = (
            Principle.query.filter(Principle.status == "approved")
            .order_by(Principle.category, Principle.name)
            .limit(MAX_PRINCIPLES)
            .all()
        )
        if principles:
            context["active_principles"] = [p.name for p in principles]
    except Exception:
        # Principles table may not yet be populated — degrade gracefully,
        # matching the review_detail route's own handling of this query.
        logger.exception("Failed to load principles for ARB pre-brief context")

    return context


def _build_prompt(context: Dict[str, Any]) -> str:
    return (
        "You are assisting an Architecture Review Board (ARB) reviewer. "
        "Below is the real, verified context for one review item — do not "
        "invent facts not present in it.\n\n"
        f"Review context (JSON):\n{json.dumps(context, indent=2, default=str)}\n\n"
        "Produce a pre-brief for the reviewer. Respond ONLY with a single "
        "JSON object with exactly these keys:\n"
        '- "summary": string, 2-4 sentences describing what is being reviewed\n'
        '- "key_risks": array of strings, the most important risks a reviewer should weigh\n'
        '- "questions_for_submitter": array of strings, open questions to raise before deciding\n'
        '- "conflicts_or_overlaps": array of strings, any conflicts with the linked ADR, '
        "active principles, or governance checklist items visible in the context "
        "(empty array if none are apparent from the given context)\n"
        '- "draft_disposition": one of "approved", "approved_with_conditions", '
        '"rejected", "deferred" — your suggested starting point, not a decision\n'
        '- "draft_conditions": array of strings, only populated when draft_disposition '
        'is "approved_with_conditions" (empty array otherwise)\n'
        '- "rationale": string explaining the draft_disposition\n\n'
        "Respond with raw JSON only, no markdown fences, no extra prose."
    )


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(ln for ln in lines if not ln.startswith("```"))
    return raw.strip()


def _parse_prebrief(raw: str) -> Dict[str, Any]:
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise ARBReviewAIPrebriefError(
            f"LLM response was not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ARBReviewAIPrebriefError("LLM response was not a JSON object")

    required_keys = {
        "summary",
        "key_risks",
        "questions_for_submitter",
        "conflicts_or_overlaps",
        "draft_disposition",
        "draft_conditions",
        "rationale",
    }
    missing = required_keys - data.keys()
    if missing:
        raise ARBReviewAIPrebriefError(
            f"LLM response missing required keys: {sorted(missing)}"
        )

    if data["draft_disposition"] not in VALID_DISPOSITIONS:
        raise ARBReviewAIPrebriefError(
            f"LLM response has an invalid draft_disposition: {data['draft_disposition']!r}"
        )

    for list_key in ("key_risks", "questions_for_submitter", "conflicts_or_overlaps", "draft_conditions"):
        if not isinstance(data[list_key], list):
            raise ARBReviewAIPrebriefError(f"LLM response field {list_key!r} was not a list")

    if not isinstance(data["summary"], str) or not isinstance(data["rationale"], str):
        raise ARBReviewAIPrebriefError("LLM response summary/rationale were not strings")

    return {
        "summary": data["summary"],
        "key_risks": [str(x) for x in data["key_risks"]],
        "questions_for_submitter": [str(x) for x in data["questions_for_submitter"]],
        "conflicts_or_overlaps": [str(x) for x in data["conflicts_or_overlaps"]],
        "draft_disposition": data["draft_disposition"],
        "draft_conditions": [str(x) for x in data["draft_conditions"]],
        "rationale": data["rationale"],
    }


def generate_review_prebrief(review_item) -> Dict[str, Any]:
    """Generate an AI pre-brief for an ARB reviewer.

    Advisory only — the caller must not persist this as the review's
    decision. Raises ARBReviewAIPrebriefError (or lets an LLMService
    exception propagate) rather than fabricating a fallback pre-brief.
    """
    context = _build_context(review_item)
    prompt = _build_prompt(context)
    raw = LLMService.generate_from_prompt(prompt, use_cache=False)
    return _parse_prebrief(raw)
