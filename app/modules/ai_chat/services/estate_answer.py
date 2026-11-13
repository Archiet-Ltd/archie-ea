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
from app.modules.dashboard.services.overview_figures import element_each_name_means
from app.utils.duplicate_guard import folded_column


# The largest id the database stores: a posted id above it is not an element, and is not asked about.
MAX_ELEMENT_ID = 2**31 - 1


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
    # One element for each name, by the rule the overview and the cards share.
    meant = set(element_each_name_means(organization_id, [(row.id, row.name) for row in found]).values())
    chosen = [row for row in found if row.id in meant]
    chosen.sort(key=lambda row: (-len(phrase_key(row.name)), row.id))
    return [row.id for row in chosen]


def owned_element_id(element_id: Any, organization_id: Optional[int]) -> Optional[int]:
    """*element_id* when it is an element of this organisation, else ``None``: a posted id
    from another organisation, or one that is not a whole number, is ignored."""
    if organization_id is None or isinstance(element_id, bool) or not isinstance(element_id, int):
        return None
    if not 0 < element_id <= MAX_ELEMENT_ID:
        return None
    return db.session.execute(
        db.select(ArchiMateElement.id).where(
            ArchiMateElement.id == element_id, ArchiMateElement.organization_id == organization_id
        )
    ).scalar_one_or_none()


def answer_estate_question(
    message: str,
    *,
    user_id: Optional[int] = None,
    direction: Optional[str] = None,
    element_id: Optional[int] = None,
):
    """The stored answer to *message*, or ``None`` when it is not a question
    about an element of the active organisation. A card that offered the question
    passes the id of the element it named: once that is this organisation's element it
    decides both the element and the kind of answer (what breaks if it fails), whatever
    its name says or shares. The text is classified only when no id is posted.
    *direction* ("upstream" for what the element depends on, "downstream" for what
    breaks) asks the impact walk that way whatever the wording, for a message that
    asks several things at once."""
    organization_id = current_org_id()
    if organization_id is None:
        return None
    card_element = owned_element_id(element_id, organization_id)
    if card_element is not None:
        kind, element_ids = envelope.KIND_IMPACT, [card_element]
    else:
        kind = envelope.KIND_IMPACT if direction else envelope.classify_estate_question(message)
        element_ids = named_element_ids(message, organization_id) if kind is not None else []
        if not element_ids:
            return None
    # The dashboard's first answer follows worked-out connections three steps out;
    # the chat asks the same question the same way.
    params = {"include_derived": True, "max_depth": 3}
    if direction:
        params["direction"] = direction
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
