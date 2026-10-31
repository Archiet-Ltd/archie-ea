"""A chat answer that read the genome, checked and drawn on the server.

The model's text has already passed ``genome_post_check`` (every cited id is in
this turn's ledger). Here each ``[ref:<id>]`` becomes a link built from the
node the slice returned (its name, its page), never from anything the model
wrote; the answer carries the AI notice with the provider, the time the facts
were read and, when a slice was cut to its budget, the fixed partial note.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from flask import render_template

from app.modules.ai_chat.services.ai_hallucination_detector import (
    REF_MARKER,
    Grounding,
    genome_post_check,
)
from app.modules.ai_chat.tools.executor import GENOME_NOTICE
from app.modules.genome.slices.serialise import PARTIAL_NOTE

_PROVIDER_NAMES = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "azure": "Azure OpenAI",
    "azure_openai": "Azure OpenAI",
    "openrouter": "OpenRouter",
    "nvidia": "NVIDIA",
    "gemini": "Google Gemini",
    "google": "Google Gemini",
}


def provider_name(provider: Optional[str]) -> str:
    if not provider:
        return ""
    return _PROVIDER_NAMES.get(provider, str(provider).replace("_", " ").title())


def _as_of_text(values: List[str]) -> str:
    stamps = []
    for value in values:
        try:
            stamps.append(datetime.fromisoformat(value))
        except (TypeError, ValueError):
            continue
    if not stamps:
        return ""
    earliest = min(stamps)
    return earliest.strftime("%d %b %Y, %H:%M UTC")


def _segments(line: str, nodes: Dict[str, dict]) -> List[dict]:
    """Plain text and links, in order, for one line of checked text."""
    out, pos = [], 0
    for m in REF_MARKER.finditer(line):
        if m.start() > pos:
            out.append({"text": line[pos:m.start()]})
        node = nodes.get(m.group(1)) or {}
        prov = node.get("provenance") or {}
        out.append({
            "node_id": m.group(1),
            "name": node.get("name") or m.group(1),
            "href": node.get("link"),
            "synthetic": prov.get("origin") == "synthetic",
        })
        pos = m.end()
    if pos < len(line):
        out.append({"text": line[pos:]})
    return out


def build(response_text: str, grounding: Grounding, provider: Optional[str]) -> Dict:
    """``{"text", "html", "removed", "slices"}`` for an answer whose turn read a slice."""
    checked = genome_post_check(response_text, grounding)
    blocks: List[dict] = []
    for line in checked["text"].split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        is_item = stripped[:2] in ("- ", "* ")
        segments = _segments(stripped[2:] if is_item else stripped, grounding.genome_nodes)
        if is_item and blocks and blocks[-1]["kind"] == "list":
            blocks[-1]["items"].append(segments)
        elif is_item:
            blocks.append({"kind": "list", "items": [segments]})
        else:
            blocks.append({"kind": "para", "segments": segments})
    dropped = sum(s["nodes_dropped"] for s in grounding.genome_slices)
    partial = any(not s["complete"] for s in grounding.genome_slices)
    html = render_template(
        "ai_chat/_genome_answer.html",
        blocks=blocks,
        notice=GENOME_NOTICE,
        provider=provider_name(provider),
        as_of=_as_of_text([s.get("as_of") for s in grounding.genome_slices]),
        partial_note=PARTIAL_NOTE.format(n=dropped) if partial else None,
    )
    return {
        "text": checked["text"],
        "html": html,
        "removed": len(checked["removed"]),
        "slices": [
            {"domain": s["domain"], "spec_hash": s["spec_hash"], "complete": s["complete"]}
            for s in grounding.genome_slices
        ],
    }
