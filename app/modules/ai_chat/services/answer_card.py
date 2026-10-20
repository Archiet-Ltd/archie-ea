"""The chat's answer cards: an estate tool result shown as Ask's card.

The chat never writes an estate answer itself. A tool result produced by
``ToolExecutor`` (the stored answer, or a catalogue entry) is reshaped here into
one small card model and drawn by ``ai_chat/_answer_card.html``, which reuses the
Ask page's ``answer_cascade`` and ``answer_envelope`` components. Nothing here
walks the model, resolves an owner or computes a figure.

Also holds the starters (the question chips the Ask page offers that have an
answer tool) and the wording for a chat with no model configured.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# The tools whose result is an answer about the estate.
ANSWER_TOOLS = ("answer", "simulate_impact")

# Starters: the Ask page's question chips that have an answer tool, in the
# chip's own words. The other Ask lenses read other services and have no tool.
STARTER_QUESTIONS = (
    {"key": "impact", "label": "What breaks if it fails?"},
    {"key": "risk", "label": "What could hurt it?"},
)
# A starter's name, and the question the answer tool is asked for it.
STARTER_TOOL_QUESTION = {"impact": "impact", "risk": "risk_propagation"}

NOT_RECORDED_SENTENCE = "Not recorded: the model holds nothing for this question."
NOT_RECORDED_LINE = "Not recorded. The model holds nothing for this question, so no figure is given."

_REASON_WORDS = {
    "element_not_found": "That name is not in your model.",
    "no_seed_elements": "No element was named.",
    "no_tenant_context": "No organisation is selected.",
    "no_risk_recorded": "No open risk is recorded on this or on anything it depends on.",
    "no_data_recorded": "Nothing is recorded as depending on this yet.",
}


def _names(body: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for source in (body.get("elements"), body.get("subject_elements")):
        for key, entry in (source or {}).items():
            if isinstance(entry, dict) and entry.get("name"):
                out.setdefault(str(key), entry["name"])
    return out


def _owner_text(row: Dict[str, Any]) -> Optional[str]:
    owners = row.get("owners")
    if owners:
        text = ", ".join(str(o.get("name")) for o in owners if isinstance(o, dict) and o.get("name"))
        if text:
            return text
    owner = row.get("owner")
    if isinstance(owner, dict) and owner.get("name"):
        return owner["name"]
    if isinstance(owner, str) and owner:
        return owner
    return None


def _answer_nodes(body: Dict[str, Any], computed_at: Optional[str]) -> List[Dict[str, Any]]:
    names = _names(body)
    kind = body.get("question_kind")
    nodes: List[Dict[str, Any]] = []
    for index, row in enumerate(body.get("rows") or []):
        if kind == "risk_propagation":
            derived = row.get("path_kind") == "derived"
            nodes.append({
                "key": f"risk-{row.get('risk_id', index)}",
                "name": row.get("title") or "Risk",
                "owner": _owner_text(row),
                "depth": 0,
                "derived": derived,
                "kindLabel": "worked out from the recorded relationships" if derived else None,
                "source_name": "the risk register",
                "source_timestamp": computed_at,
            })
            continue
        relation = row.get("relation") or {}
        depth = row.get("depth") if kind == "set_impact" else relation.get("depth")
        derived = row.get("truth_class") == "derived"
        nodes.append({
            "key": f"el-{row.get('element_id')}-{index}",
            "name": names.get(str(row.get("element_id"))) or "Unnamed element",
            "owner": _owner_text(row),
            "depth": max(int(depth or 1) - 1, 0),
            "derived": derived,
            "kindLabel": "worked out from the recorded relationships" if derived else None,
            "source_name": "a relationship recorded in the model",
            "source_timestamp": computed_at,
            "source_confidence": relation.get("confidence"),
        })
    return nodes


def _question_line(body: Dict[str, Any]) -> str:
    names = _names(body)
    params = body.get("params") or {}
    kind = body.get("question_kind")
    if kind == "set_impact":
        seeds = [names.get(str(i)) for i in (params.get("element_ids") or [])]
        seeds = [s for s in seeds if s]
        return "What breaks if " + (", ".join(seeds) if seeds else "these") + " fail together?"
    subject = names.get(str(params.get("element_id"))) or "this"
    if kind == "risk_propagation":
        return f"Which risks reach {subject}?"
    return f"What breaks if {subject} fails?"


def card_from_tool_result(tool_name: str, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The card model for one answer-tool result, or None for any other result.

    A refusal that means "the model holds nothing for this" becomes the
    not-recorded card; any other failure (a malformed request) has no card.
    """
    if tool_name not in ANSWER_TOOLS or not isinstance(result, dict):
        return None
    if not result.get("success"):
        if result.get("code") != "NOT_RECORDED":
            return None
        return {
            "state": "not_recorded",
            "question": "",
            "reason": _REASON_WORDS.get(result.get("reason"), ""),
        }
    body = result.get("result") or {}
    rows = body.get("rows") or []
    computed_at = body.get("computed_at")
    card: Dict[str, Any] = {
        "state": "answered" if rows else "not_recorded",
        "question": _question_line(body),
        "address": body.get("address"),
        "page_url": body.get("page_url"),
        "truth_class": body.get("truth_class"),
        "confidence_percent": body.get("confidence_percent"),
        "as_of": (computed_at or "")[:10] or None,
        "total": (body.get("summary") or {}).get("total", len(rows)),
        "shown": len(rows),
        "citations": body.get("citations_total", len(body.get("citations") or [])),
        "root": None,
    }
    if rows:
        card["nodes"] = _answer_nodes(body, computed_at)
        if body.get("question_kind") == "impact":
            card["root"] = _names(body).get(str((body.get("params") or {}).get("element_id")))
    else:
        reasons = [r for r in (body.get("reasons") or []) if r in _REASON_WORDS]
        card["reason"] = _REASON_WORDS[reasons[0]] if reasons else ""
    return card


def card_from_catalogue(title: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """The card model for one catalogue-entry result."""
    nodes: List[Dict[str, Any]] = []
    groups = result.get("groups")
    if groups is not None:
        entries = [(g.get("group"), row) for g in groups for row in (g.get("rows") or [])]
    else:
        entries = [(None, row) for row in (result.get("rows") or [])]
    for group, row in entries:
        label = row.get("name") or row.get("application_name") or "Unnamed"
        owners = row.get("owners") or []
        nodes.append({
            "key": f"cat-{len(nodes)}",
            "name": f"{label} ({group})" if group else label,
            "owner": ", ".join(owners) if owners else None,
            "depth": 0,
            "derived": row.get("truth_class") == "derived",
            "source_name": "the application register" if row.get("source_table") else "",
        })
    return {
        "state": "answered" if nodes else "none_found",
        "question": title,
        "address": None,
        "page_url": None,
        "truth_class": "drawn",
        "total": result.get("total", len(nodes)),
        "shown": len(nodes),
        "nodes": nodes,
        "root": None,
        "reason": "" if nodes else (result.get("reason") or ""),
    }


_QUESTION_OPENERS = (
    "what", "which", "who", "where", "when", "why", "how", "is", "are", "do", "does",
    "can", "could", "would", "should", "show", "list", "tell",
)


def looks_like_question(message: Any) -> bool:
    """Whether a typed message is a question (so it is worth reading for a
    catalogue question, which costs a model call when a model is configured)."""
    if not isinstance(message, str) or not message.strip():
        return False
    text = message.strip().lower()
    return text.endswith("?") or text.split(None, 1)[0] in _QUESTION_OPENERS


def answer_typed_question(user_id: Optional[int], message: Any) -> Optional[Dict[str, Any]]:
    """Read a typed question with the plain-language interpreter and, when it
    maps onto a catalogue question, run it through the assistant's executor.

    Returns ``{"result", "card", "sentence"}``, or None when the message is not
    a question or maps onto nothing. With no model configured the interpreter
    reads by keyword only, so this works without one.
    """
    if not looks_like_question(message):
        return None
    from app.modules.ai_chat.tools.executor import ToolExecutor
    from app.modules.intelligence.services.nl_query_interpreter import interpret

    reading = interpret(message)
    if not reading.get("entry_id"):
        return None
    result = ToolExecutor(user_id).run_catalogue_entry(reading["entry_id"], reading.get("params"))
    if result.get("success"):
        card = card_from_catalogue(reading.get("title") or "", result["result"])
    else:
        card = {"state": "not_recorded", "question": reading.get("title") or "",
                "reason": _REASON_WORDS.get(result.get("reason"), "")}
    return {
        "result": result,
        "card": render_card(card),
        "state": card["state"],
        "sentence": card_sentence(card),
    }


_IMPACT_SUBJECT = re.compile(
    r"\b(?:if|when)\s+(?:the\s+)?(?P<name>.+?)\s+"
    r"(?:is|are|was|were|goes|go|fails?|stops?|becomes?|gets?)\b",
    re.I,
)


def answer_estate_question_without_model(user_id: Optional[int], message: Any) -> Optional[Dict[str, Any]]:
    """Answer "what breaks if <name> ..." with no model: the same estate-question
    reading the assistant uses, the executor's own name resolution, and the
    stored answer. None when the message is not that question or the name does
    not resolve to exactly one element of the person's organisation."""
    from app.modules.ai_chat.tools.executor import ToolCall, ToolExecutor
    from app.modules.intelligence.services.answer_envelope import (
        KIND_IMPACT,
        classify_estate_question,
    )

    if classify_estate_question(message) != KIND_IMPACT:
        return None
    found = _IMPACT_SUBJECT.search(message)
    name = found.group("name").strip(" ?.\"'") if found else ""
    if not name:
        return None
    arguments = {"element_name": name}
    result = ToolExecutor(user_id).execute(
        ToolCall(id="typed", name="simulate_impact", arguments=arguments)
    )
    card = card_from_tool_result("simulate_impact", result)
    html = render_card(card)
    if not html:
        return None
    return {
        "tool": "simulate_impact",
        "arguments": arguments,
        "result": result,
        "card": html,
        "state": card["state"],
        "sentence": card_sentence(card),
    }


def render_card(card: Optional[Dict[str, Any]]) -> Optional[str]:
    """The card's markup (Ask's answer card), or None when there is no card."""
    if not card:
        return None
    from flask import render_template

    try:
        return render_template(
            "ai_chat/_answer_card.html", card=card, not_recorded_line=NOT_RECORDED_LINE
        )
    except Exception:
        logger.warning("answer_card: render failed", exc_info=True)
        return None


def card_sentence(card: Dict[str, Any]) -> str:
    """The one short sentence that sits above a card."""
    if card.get("state") == "not_recorded":
        return NOT_RECORDED_SENTENCE
    if card.get("state") == "none_found":
        return "Nothing in the model matches this question."
    total = card.get("total") or 0
    noun = "item answers" if total == 1 else "items answer"
    return f"{total} recorded {noun} this, shown below with their sources."


def without_model_sentence() -> str:
    """What the chat answers when no model is configured, in plain words."""
    from app.modules.intelligence.services.query_catalogue import CATALOGUE

    titles = " ".join(f'"{e.title}"' for e in CATALOGUE.values() if e.id != "overlapping_contracts")
    return (
        "No model is configured, so I can answer only the starter questions "
        "and these, written out in plain words: " + titles
    )
