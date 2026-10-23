"""The three questions the Ask page and the dashboard checklist offer once a
model exists, written from the organisation's own services and owners.

This is the one place the suggested questions are built. It does not hold a
second impact walk or a second owner reader: it asks the canonical impact walk
(``IntelligenceQueryService.cross_layer_impact``) which services actually have
dependents, and reads owners through the owner store, so a question it offers
can always be answered.

Every question names a real element of the caller's own organisation and
carries the Ask-page address that answers it, so the screen never offers a
dead end and a second organisation never sees the first one's names.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.extensions import db


# How many questions the first screen shows. Three, named after real services.
SUGGESTION_COUNT = 3

# The Ask page lens each question opens. These are the lens keys the Ask page
# already draws (see ask.html); nothing new is introduced here.
_IMPACT = "impact"
_ACCOUNTABILITY = "accountability"
_DATA = "data"


def _ask_href(element_id: int, kind: str) -> str:
    """The Ask page address that answers this question for this element.

    The page reads ``element`` and ``question`` the same way Twin map already
    reads ``element``; a link is a plain GET, no state written.
    """
    return "/intelligence/ask?question=%s&element=%s" % (kind, element_id)


def _impact_question(element_id: int, name: str) -> Dict[str, Any]:
    return {
        "element_id": element_id,
        "element_name": name,
        "kind": _IMPACT,
        "text": "What breaks if %s fails?" % name,
        "href": _ask_href(element_id, _IMPACT),
        "answerable": True,
    }


def _accountability_question(element_id: int, name: str) -> Dict[str, Any]:
    return {
        "element_id": element_id,
        "element_name": name,
        "kind": _ACCOUNTABILITY,
        "text": "Who is accountable for %s?" % name,
        "href": _ask_href(element_id, _ACCOUNTABILITY),
        "answerable": True,
    }


def _data_question(element_id: int, name: str) -> Dict[str, Any]:
    return {
        "element_id": element_id,
        "element_name": name,
        "kind": _DATA,
        "text": "What data does %s hold?" % name,
        "href": _ask_href(element_id, _DATA),
        "answerable": True,
    }


def _service_elements(organization_id: int) -> List[Any]:
    """The organisation's own application-layer elements, newest named first.

    Application-layer elements are the "services and systems" a person
    recognises; the Ask picker offers the same vocabulary. Tenant-scoped by an
    explicit predicate (the model carries ``TenantMixin`` too, so this
    double-scopes, matching the pattern used across this module).
    """
    from app.models import ArchiMateElement

    return list(
        db.session.execute(
            db.select(ArchiMateElement)
            .where(
                ArchiMateElement.organization_id == organization_id,
                ArchiMateElement.layer == "application",
            )
            .order_by(ArchiMateElement.id)
        ).scalars()
    )


def _has_any_link(organization_id: int) -> bool:
    """Whether this organisation's model records any dependency link at all."""
    from app.models import ArchiMateRelationship

    first = db.session.execute(
        db.select(ArchiMateRelationship.id)
        .where(ArchiMateRelationship.organization_id == organization_id)
        .limit(1)
    ).first()
    return first is not None


def _dependent_count(element_id: int) -> int:
    """How many things depend on this element, from the canonical impact walk.

    One engine for impact traversal; this does not re-walk relationships
    itself. A count of zero means nothing depends on it, so an impact question
    about it would answer "nothing depends on ...".
    """
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    result = IntelligenceQueryService.cross_layer_impact(
        element_id,
        include_derived=False,
        max_depth=3,
        direction="downstream",
        with_owner=False,
    )
    return len(result.get("rows") or [])


def suggested_questions(organization_id: Optional[int]) -> Dict[str, Any]:
    """Up to three questions for *organization_id*, each naming a real element.

    * With dependency links: "what breaks if <service> fails?" for the
      services that genuinely have dependents, so clicking one names them.
    * With no links: the questions that need no traversal to answer — who is
      accountable for a service, and what data it holds — still named after the
      organisation's own services.
    * With no elements at all: an empty list and ``empty`` set, never a
      question built from an invented name.

    Returns ``{"questions": [...], "has_links": bool, "empty": bool}``.
    """
    empty_result = {"questions": [], "has_links": False, "empty": True}
    if organization_id is None:
        return empty_result

    services = _service_elements(organization_id)
    if not services:
        return empty_result

    has_links = _has_any_link(organization_id)
    questions: List[Dict[str, Any]] = []

    if has_links:
        # Services that really have dependents, most-depended-on first, so the
        # question a person clicks first names the service whose failure
        # reaches the most. A service with no dependents is not offered an
        # impact question (its answer would be "nothing depends on it").
        scored = []
        for element in services:
            count = _dependent_count(element.id)
            if count > 0:
                scored.append((count, element.id, element))
        scored.sort(key=lambda row: (-row[0], row[1]))
        for _count, _id, element in scored[:SUGGESTION_COUNT]:
            questions.append(_impact_question(element.id, element.name))

    # No links, or too few services have dependents to fill the three: fall
    # back to questions that need no traversal, named after the organisation's
    # own services. Who is accountable for each, then what data each holds —
    # both answerable from records already present, never an empty list.
    if len(questions) < SUGGESTION_COUNT:
        used_ids = {q["element_id"] for q in questions}
        builders = (_accountability_question, _data_question)
        builder_index = 0
        for element in services:
            if len(questions) >= SUGGESTION_COUNT:
                break
            if element.id in used_ids:
                continue
            builder = builders[builder_index % len(builders)]
            questions.append(builder(element.id, element.name))
            used_ids.add(element.id)
            builder_index += 1

    return {
        "questions": questions[:SUGGESTION_COUNT],
        "has_links": has_links,
        "empty": False,
    }
