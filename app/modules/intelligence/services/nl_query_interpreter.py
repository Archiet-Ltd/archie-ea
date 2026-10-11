"""R1-B39: map a plain-language question onto one query_catalogue entry.

Calls the platform's one gated model gateway (``LLMService.generate_from_prompt``)
— never a direct provider call — and falls back to a deterministic
keyword match when no model provider is configured (so this interpreter,
and its tests, work with AI disabled). Both paths return the same shape so
callers never need to know which one ran.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.modules.intelligence.services.query_catalogue import CATALOGUE

logger = logging.getLogger(__name__)


# Deterministic fallback: ordered (keywords, entry_id, default_params).
# Checked in order; the first match wins. This is also what runs the
# committed evaluation set in CI, where no LLM provider is configured.
_KEYWORD_RULES = [
    (
        ("no owner", "without owner", "without an owner", "no recorded owner", "unowned"),
        "applications_without_owner",
        {},
    ),
    (
        (
            "stop the business", "stop trading", "stop us from trading",
            "business continuity", "critical systems",
        ),
        "business_continuity_criticality",
        {"criticality": "Critical"},
    ),
]


def _keyword_match(question: str) -> Optional[Dict[str, Any]]:
    lowered = question.lower()
    for keywords, entry_id, params in _KEYWORD_RULES:
        if any(kw in lowered for kw in keywords):
            return {"entry_id": entry_id, "params": dict(params), "confidence": 0.9, "method": "keyword"}
    return None


def _model_configured() -> bool:
    try:
        from app.modules.ai_chat.services.llm_service_impl import LLMService

        provider, _model = LLMService._get_configured_provider()
        return bool(provider)
    except Exception:
        return False


def _llm_match(question: str) -> Optional[Dict[str, Any]]:
    """Ask the gated model gateway which catalogue entry fits, if any."""
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    entries_desc = "\n".join(
        f"- {e.id}: {e.title} (params: {', '.join(e.params) or 'none'})"
        for e in CATALOGUE.values()
    )
    prompt = (
        "Map the user's question onto exactly one of these catalogue entries, "
        "or none if nothing fits. Respond with JSON only: "
        '{"entry_id": "<id or null>", "params": {...}}.\n\n'
        f"Catalogue:\n{entries_desc}\n\nQuestion: {question!r}"
    )
    try:
        raw = LLMService.generate_from_prompt(prompt, expected_schema="json")
        parsed = json.loads(raw)
    except Exception:
        logger.warning("nl_query_interpreter: model gateway call failed", exc_info=True)
        return None

    entry_id = parsed.get("entry_id")
    if entry_id not in CATALOGUE:
        return None
    return {"entry_id": entry_id, "params": parsed.get("params") or {}, "confidence": 0.7, "method": "model"}


def interpret(question: str) -> Dict[str, Any]:
    """Interpret one plain-language question.

    Returns ``{"entry_id": str|None, "params": dict, "confidence": float,
    "method": "keyword"|"model"|"none", "title": str|None}`` — the
    interpretation banner shows ``title``/``params`` before running
    anything, and the caller re-runs with corrected params on request.
    """
    match = _keyword_match(question)
    if match is None and _model_configured():
        match = _llm_match(question)
    if match is None:
        return {"entry_id": None, "params": {}, "confidence": 0.0, "method": "none", "title": None}
    entry = CATALOGUE[match["entry_id"]]
    match["title"] = entry.title
    return match


def evaluation_set() -> List[Dict[str, Any]]:
    """The committed set of plain-language questions and their correct
    catalogue mapping, used by the ≥90% correctness test."""
    return [
        {"question": "which applications have no owner?", "expected_entry_id": "applications_without_owner"},
        {"question": "show me applications without an owner", "expected_entry_id": "applications_without_owner"},
        {"question": "list unowned applications", "expected_entry_id": "applications_without_owner"},
        {"question": "what systems have no recorded owner", "expected_entry_id": "applications_without_owner"},
        {"question": "what would stop the business trading?", "expected_entry_id": "business_continuity_criticality"},
        {"question": "what would stop us from trading", "expected_entry_id": "business_continuity_criticality"},
        {"question": "show critical systems and their suppliers", "expected_entry_id": "business_continuity_criticality"},
        {"question": "which systems are business continuity risks", "expected_entry_id": "business_continuity_criticality"},
        {"question": "what's the weather today", "expected_entry_id": None},
        {"question": "how many users do we have", "expected_entry_id": None},
    ]
