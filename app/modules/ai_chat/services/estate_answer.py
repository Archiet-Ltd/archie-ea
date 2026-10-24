"""A question about the organisation's own estate, answered as a stored answer.

The chat asks this before it asks the model: a question such as "what breaks if
the payments service fails" that names an element of the organisation is
answered from the recorded connections by the same query service the dashboard's
first answer uses, wrapped and stored by ``answer_envelope``. Nothing here
computes an answer; it only finds which element the question names.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.extensions import db
from app.middleware.tenant_context import current_org_id
from app.models import ArchiMateElement
from app.modules.ai_chat.services.ai_hallucination_detector import candidate_phrases, phrase_key
from app.modules.intelligence.services import answer_envelope as envelope
from app.utils.duplicate_guard import folded_column


def named_element_ids(message: str, organization_id: int) -> list:
    """Ids of the organisation's elements whose whole name appears in *message*,
    the longest names first so "Payments service" wins over "Payments"."""
    phrases = sorted(candidate_phrases(message))
    if not phrases:
        return []
    found = []
    for offset in range(0, len(phrases), 500):
        found.extend(
            db.session.execute(
                db.select(ArchiMateElement.id, ArchiMateElement.name).where(
                    ArchiMateElement.organization_id == organization_id,
                    folded_column(ArchiMateElement.name).in_(phrases[offset:offset + 500]),
                )
            ).all()
        )
    found.sort(key=lambda row: (-len(phrase_key(row.name)), row.id))
    seen, ordered = set(), []
    for element_id, name in found:
        if element_id not in seen:
            seen.add(element_id)
            ordered.append(element_id)
    return ordered


def answer_estate_question(message: str, *, user_id: Optional[int] = None):
    """The stored answer to *message*, or ``None`` when it is not a question
    about an element of the active organisation."""
    organization_id = current_org_id()
    kind = envelope.classify_estate_question(message)
    if kind is None or organization_id is None:
        return None
    element_ids = named_element_ids(message, organization_id)
    if not element_ids:
        return None
    # The dashboard's first answer follows worked-out connections three steps out;
    # the chat asks the same question the same way.
    params = {"include_derived": True, "max_depth": 3}
    if kind == envelope.KIND_SET_IMPACT and len(element_ids) > 1:
        params["element_ids"] = element_ids[:envelope.SET_IMPACT_MAX_SEEDS]
    else:
        kind = envelope.KIND_IMPACT if kind == envelope.KIND_SET_IMPACT else kind
        params["element_id"] = element_ids[0]
    return envelope.ask_and_store(kind, params, user_id=user_id)


def recorded_text(iso: Any) -> Optional[str]:
    """A recorded time as people read it ("1 Sep 2026, 10:30"), or None when there is none."""
    if not iso:
        return None
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = when.astimezone(timezone.utc)
    return "%d %s" % (when.day, when.strftime("%b %Y, %H:%M"))


def explanations_for(answer: Dict[str, Any], limit: int) -> Dict[int, Dict[str, Any]]:
    """For each worked-out connection among the first *limit* rows, the links it was
    worked out from, read by the same explanation the provenance API serves."""
    from app.modules.intelligence.services.derived_facts import get_derived_fact
    from app.modules.intelligence.services.explanation import explain_fact

    organization_id = current_org_id()
    found: Dict[int, Dict[str, Any]] = {}
    for row in answer["rows"][:limit]:
        derived_id = (row.get("relation") or {}).get("derived_id")
        if organization_id is None or not isinstance(derived_id, int) or derived_id in found:
            continue
        fact = get_derived_fact(organization_id, derived_id, include_stale=True)
        if fact is not None:
            found[derived_id] = explain_fact(organization_id, fact)
    return found
