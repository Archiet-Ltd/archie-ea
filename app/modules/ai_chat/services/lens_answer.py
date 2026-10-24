"""The other questions the catalogue answers about one element, asked in the chat.

Strategy, risk, rationalisation, programme, accountability, data and compliance
questions each have a read at ``/api/v1/intelligence/<lens>/<id>``. The chat asks
that same read, by calling the view that serves it, so the redaction of financial
figures for a person without budget authority is the one the API already applies,
and draws what it returns. Nothing here computes an answer; it finds which lens a
question asks and which element it names.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from app.extensions import db
from app.middleware.tenant_context import current_org_id
from app.models import ArchiMateElement
from app.modules.ai_chat.services.estate_answer import named_element_ids

# key -> (words that ask it, the view that serves it)
LENSES = {
    "strategy": (
        re.compile(r"trying to achieve|\b(how is|how's|how are)\b.*\btracking\b|\bstrategy\b|\binitiatives?\b", re.I),
        "strategy_for_element",
    ),
    "risk": (
        re.compile(r"what could hurt|\brisks?\b.*\b(recorded|against|on)\b", re.I),
        "risk_for_element",
    ),
    "portfolio": (re.compile(r"rationali[sz]ation", re.I), "portfolio_component_for_element"),
    "programme": (
        re.compile(r"what(?:'s| is) changing|\bwork packages?\b|\bprogramme\b|\bon (track|time)\b", re.I),
        "programme_for_element",
    ),
    "accountability": (
        re.compile(r"\baccountable\b|who(?:'s| is) responsible", re.I),
        "accountability_for_element",
    ),
    "data": (re.compile(r"\bwhat data\b|\bwho stewards\b|\bdata (flows?|objects?)\b", re.I), "data_for_element"),
    "compliance": (
        re.compile(r"\bregulations?\b|\bcompliance controls?\b|\bcontrols?\b.*\bevidence\b", re.I),
        "compliance_for_element",
    ),
}


def classify_lens_question(message: Any) -> Optional[str]:
    """The lens a message asks, or None for anything else."""
    if not isinstance(message, str) or not message.strip():
        return None
    for key, (pattern, _view) in LENSES.items():
        if pattern.search(message):
            return key
    return None


def lens_payload(lens: str, element_id: int) -> Optional[Dict[str, Any]]:
    """What the lens's API read answers for the element, or None when it cannot."""
    from app.modules.intelligence.routes import api as lens_api

    result = getattr(lens_api, LENSES[lens][1])(element_id)
    response, status = result if isinstance(result, tuple) else (result, 200)
    body = response.get_json(silent=True) or {}
    if status != 200 or not body.get("success"):
        return None
    return body.get("data") or {}


def answer_lens_question(message: str) -> Optional[Dict[str, Any]]:
    """``{lens, element, data}`` for a lens question naming an element of the
    active organisation, or None when it is not one."""
    organization_id = current_org_id()
    lens = classify_lens_question(message)
    if lens is None or organization_id is None:
        return None
    element_ids = named_element_ids(message, organization_id)
    if not element_ids:
        return None
    element = db.session.get(ArchiMateElement, element_ids[0])
    if element is None or element.organization_id != organization_id:
        return None
    data = lens_payload(lens, element.id)
    if data is None:
        return {"lens": lens, "element": element, "data": None}
    return {"lens": lens, "element": element, "data": data}
