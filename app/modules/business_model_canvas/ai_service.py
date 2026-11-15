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

from app.services.application_kind import applications_query
from app.modules.ai_chat.services.llm_service_impl import LLMService

logger = logging.getLogger(__name__)

# Marks a proposal as AI-written. A suggestion answers no question, so it does not use the chat answer's wording.
SUGGESTION_NOTICE = "Suggested by AI from this canvas and your model"

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


CANDIDATE_LIMIT = 40


def _build_context(canvas, block_key: str, show_costs: bool = True) -> Dict[str, Any]:
    """Assemble the context from the one canvas reader and the organisation's own model: what the other
    boxes already hold, the existing elements that could go in this box, and the application count.
    A person the cost rule withholds from gets no cost line. Nothing is inferred or invented.
    """
    from app.modules.business_model_canvas import model_service

    contents = model_service.canvas_contents(canvas.organization_id, canvas.id)
    if not show_costs:
        contents = model_service.withhold_cost_lines(contents)
    context: Dict[str, Any] = {
        "canvas_name": canvas.name,
        "operating_model_type": canvas.operating_model_type or None,
        "target_block": block_key,
        "other_blocks": {},
    }
    for box in contents["boxes"]:
        if box["key"] == block_key:
            continue
        names = [entry["name"] for entry in box["entries"]]
        names += [price["text"] for price in box["prices"]]
        if show_costs and box["costs"]:
            names += [line["text"] for line in box["costs"]["lines"]]
        if names:
            context["other_blocks"][box["key"]] = names

    context["existing_elements"] = [
        {"id": el.id, "name": el.name, "type": el.type}
        for el in model_service.candidates(canvas, block_key)[:CANDIDATE_LIMIT]
    ]

    try:

        # ApplicationComponent is TenantMixin, so this count is already
        # scoped to the current org by do_orm_execute.
        context["application_count"] = applications_query().count()
    except Exception:
        logger.exception("Failed to count applications for BMC draft context")

    return context


def _build_prompt(context: Dict[str, Any], block_key: str) -> str:
    label = BLOCK_LABELS.get(block_key, block_key)
    return (
        "You are assisting a Business Architect filling in a Business Model "
        "Canvas (Osterwalder/Pigneur). Below is the real, verified context "
        "for this canvas — do not invent facts not present in it.\n\n"
        f"Canvas context (JSON):\n{json.dumps(context, indent=2, default=str)}\n\n"
        f'Propose entries for the "{label}" block only. Respond ONLY with a '
        "single JSON object with exactly these keys:\n"
        '- "proposals": array of objects, each with "name" (a short entry name), '
        '"source" (the part of the context it comes from, in plain words) and '
        '"element_id" (the id of one of existing_elements when the entry is that '
        "element, otherwise null)\n\n"
        "Respond with raw JSON only, no markdown fences, no extra prose."
    )


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(ln for ln in lines if not ln.startswith("```"))
    return raw.strip()


def _parse_draft(raw: str, block_key: str, allowed_ids=frozenset()) -> Dict[str, Any]:
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise BusinessModelAIDraftError(
            f"LLM response was not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise BusinessModelAIDraftError("LLM response was not a JSON object")

    if "proposals" in data:
        return _parse_proposals(data["proposals"], block_key, allowed_ids)

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

    sources = [str(x) for x in data["based_on"]]
    proposals = [
        {"name": line.strip(), "source": ", ".join(sources), "element_id": None}
        for line in data["content"].splitlines()
        if line.strip()
    ]
    return {"block": block_key, "content": data["content"], "based_on": sources, "proposals": proposals}


def _parse_proposals(items, block_key, allowed_ids):
    if not isinstance(items, list):
        raise BusinessModelAIDraftError("LLM response field 'proposals' was not a list")
    proposals = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            continue
        source = str(item.get("source") or "").strip()
        if not source:
            # A proposal that cannot say where it came from is not shown.
            continue
        element_id = item.get("element_id")
        proposals.append({
            "name": " ".join(item["name"].split()),
            "source": source,
            "element_id": element_id if element_id in allowed_ids else None,
        })
    return {
        "block": block_key,
        "content": "\n".join(p["name"] for p in proposals),
        "based_on": sorted({p["source"] for p in proposals}),
        "proposals": proposals,
    }


def generate_block_draft(canvas, block_key: str, show_costs: bool = True) -> Dict[str, Any]:
    """Propose entries for one box of a canvas.

    Advisory only: the caller must not persist this; the person accepts a proposal and the canvas's own
    add-entry writer stores it. Raises ValueError for a box that takes no entries (caller maps this to
    400), BusinessModelAIDraftError (or lets an LLMService exception propagate) rather than fabricating
    a fallback.
    """
    from app.modules.business_model_canvas import model_service

    if block_key not in model_service.element_boxes(canvas):
        raise ValueError(f"Unknown canvas block: {block_key}")

    context = _build_context(canvas, block_key, show_costs)
    prompt = _build_prompt(context, block_key)
    raw = LLMService.generate_from_prompt(prompt, use_cache=False)
    return _parse_draft(raw, block_key, {e["id"] for e in context["existing_elements"]})
