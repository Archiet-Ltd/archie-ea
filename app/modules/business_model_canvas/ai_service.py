"""AI drafting assist for Business Model Canvas blocks.

The 9-box canvas (app/templates/business_model/detail.html) is filled in by
hand, one free-text block at a time. This drafts a single block from the
canvas's *other* already-filled blocks plus cheap app/capability aggregates
— advisory only. The draft is handed to the UI's existing inline textarea/
save-block flow; nothing here is written to the canvas directly.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from app.models.business_model import CANVAS_BLOCKS
from app.services.application_kind import applications_query
from app.modules.ai_chat.services.llm_service_impl import LLMService

logger = logging.getLogger(__name__)

BLOCK_LABELS = {
    "key_partners": "Key Partners",
    "key_activities": "Key Activities",
    "key_resources": "Key Resources",
    "value_propositions": "Value Propositions",
    "customer_relationships": "Customer Relationships",
    "channels": "Channels",
    "customer_segments": "Customer Segments",
    "cost_structure": "Cost Structure",
    "revenue_streams": "Revenue Streams",
}


class BusinessModelAIDraftError(Exception):
    """Raised when the LLM response cannot be parsed into a usable draft.

    Never caught to fabricate a fallback value — per CLAUDE.md, a screen
    that fabricates a plausible value when the real one is missing is worse
    than one that shows nothing.
    """


def _build_context(canvas, block_key: str) -> Dict[str, Any]:
    """Assemble only the context this function actually queries: the
    canvas's other filled-in blocks, plus cheap org-wide app/capability
    counts. Nothing is inferred or invented.
    """
    context: Dict[str, Any] = {
        "canvas_name": canvas.name,
        "operating_model_type": canvas.operating_model_type or None,
        "target_block": block_key,
        "other_blocks": {},
    }
    for key in CANVAS_BLOCKS:
        if key == block_key:
            continue
        value = getattr(canvas, key)
        if value:
            context["other_blocks"][key] = value

    try:

        # ApplicationComponent is TenantMixin, so this count is already
        # scoped to the current org by do_orm_execute.
        context["application_count"] = applications_query().count()
    except Exception:
        logger.exception("Failed to count applications for BMC draft context")

    # No capability count here: UnifiedCapability has no organization_id
    # column at all, so a bare .count() would be the *global* cross-org
    # catalog size, not this org's — and there's no cheap org-scoped
    # derivation for a plain count the way there is for named capabilities
    # (see value_stream_ai_service._org_scoped_capability_names). Omitted
    # rather than sent mislabelled as the org's own count.

    return context


def _build_prompt(context: Dict[str, Any], block_key: str) -> str:
    label = BLOCK_LABELS.get(block_key, block_key)
    return (
        "You are assisting a Business Architect filling in a Business Model "
        "Canvas (Osterwalder/Pigneur). Below is the real, verified context "
        "for this canvas — do not invent facts not present in it.\n\n"
        f"Canvas context (JSON):\n{json.dumps(context, indent=2, default=str)}\n\n"
        f'Draft content for the "{label}" block only. Respond ONLY with a '
        "single JSON object with exactly these keys:\n"
        '- "content": string, the drafted block content (one idea per line)\n'
        '- "based_on": array of strings, which pieces of context this draft '
        "leans on (e.g. names of other filled blocks it is consistent with)\n\n"
        "Respond with raw JSON only, no markdown fences, no extra prose."
    )


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(ln for ln in lines if not ln.startswith("```"))
    return raw.strip()


def _parse_draft(raw: str, block_key: str) -> Dict[str, Any]:
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise BusinessModelAIDraftError(
            f"LLM response was not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise BusinessModelAIDraftError("LLM response was not a JSON object")

    required_keys = {"content", "based_on"}
    missing = required_keys - data.keys()
    if missing:
        raise BusinessModelAIDraftError(
            f"LLM response missing required keys: {sorted(missing)}"
        )

    if not isinstance(data["content"], str):
        raise BusinessModelAIDraftError("LLM response field 'content' was not a string")
    if not isinstance(data["based_on"], list):
        raise BusinessModelAIDraftError("LLM response field 'based_on' was not a list")

    return {
        "block": block_key,
        "content": data["content"],
        "based_on": [str(x) for x in data["based_on"]],
    }


def generate_block_draft(canvas, block_key: str) -> Dict[str, Any]:
    """Generate an AI draft for one Business Model Canvas block.

    Advisory only — the caller must not persist this directly; the UI feeds
    it into the block's existing inline editor and the user saves it via
    the existing save-block path. Raises ValueError for an unknown block
    key (caller maps this to 400), BusinessModelAIDraftError (or lets an
    LLMService exception propagate) rather than fabricating a fallback.
    """
    if block_key not in CANVAS_BLOCKS:
        raise ValueError(f"Unknown canvas block: {block_key}")

    context = _build_context(canvas, block_key)
    prompt = _build_prompt(context, block_key)
    raw = LLMService.generate_from_prompt(prompt, use_cache=False)
    return _parse_draft(raw, block_key)
