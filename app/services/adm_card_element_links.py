"""The one place a kanban card is linked to ArchiMate elements.

The junction table ``card_archimate_elements`` (foreign keys, cascade delete) is
the only stored link. ``KanbanCard.archimate_element_ids`` is a mirror of it,
written here and nowhere else, in the same transaction as the junction rows.

Every call is scoped to an organisation passed in by the caller: a card or an
element of another organisation is refused, never silently skipped.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from app import db
from app.models.adm_kanban import KanbanCard
from app.models.adm_kanban_junctions import CardArchiMateElement
from app.models.archimate_core import ArchiMateElement

MODES = ("add", "remove", "replace")


class CardElementLinkError(ValueError):
    """The card or an element does not belong to the caller's organisation, or the input is malformed."""


def _clean_ids(raw) -> List[int]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple, set)):
        raise CardElementLinkError("Element ids must be a list")
    out: List[int] = []
    for value in raw:
        if isinstance(value, bool):
            raise CardElementLinkError("Invalid element id")
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise CardElementLinkError("Invalid element id")
        if number not in out:
            out.append(number)
    return out


def _card_org(card: KanbanCard) -> Optional[int]:
    if card.organization_id is not None:
        return card.organization_id
    board = getattr(card, "board", None)
    return getattr(board, "organization_id", None)


def _elements_in_org(ids: Iterable[int], organization_id: int) -> dict:
    ids = list(ids)
    if not ids:
        return {}
    rows = (
        db.session.query(ArchiMateElement)
        .filter(ArchiMateElement.id.in_(ids), ArchiMateElement.organization_id == organization_id)
        .all()
    )
    return {e.id: e for e in rows}


def check_element_ids(element_ids, organization_id: Optional[int]) -> List[int]:
    """Return the cleaned ids, or raise if any is not an element of the organisation."""
    ids = _clean_ids(element_ids)
    if ids:
        if organization_id is None:
            raise CardElementLinkError("No active organisation")
        if len(_elements_in_org(ids, organization_id)) != len(ids):
            raise CardElementLinkError("Unknown ArchiMate element")
    return ids


def get_card_element_ids(card: KanbanCard, organization_id: Optional[int] = None) -> List[int]:
    """Element ids linked to the card, read from the junction table, in link order."""
    if card.id is None:
        return []
    org = organization_id if organization_id is not None else _card_org(card)
    if org is None or _card_org(card) != org:
        return []
    rows = (
        db.session.query(CardArchiMateElement.archimate_element_id)
        .join(ArchiMateElement, ArchiMateElement.id == CardArchiMateElement.archimate_element_id)
        .filter(CardArchiMateElement.card_id == card.id, ArchiMateElement.organization_id == org)
        .order_by(CardArchiMateElement.id)
        .all()
    )
    return [r[0] for r in rows]


def change_card_elements(
    card: KanbanCard,
    element_ids,
    *,
    mode: str,
    organization_id: Optional[int],
    actor_id: Optional[int] = None,
    relationship_type: str = "association",
) -> List[int]:
    """Add, remove or replace the card's element links and refresh the mirror.

    Nothing is committed here and nothing is changed when the call is refused.
    Ids that exist only in the legacy mirror are adopted into the junction table
    when they name an element of the organisation, and dropped otherwise.
    Returns the card's element ids after the change.
    """
    if mode not in MODES:
        raise CardElementLinkError(f"Unknown mode: {mode}")
    if organization_id is None:
        raise CardElementLinkError("No active organisation")
    if card.id is None:
        db.session.flush()
    if _card_org(card) != organization_id:
        raise CardElementLinkError("Unknown card")

    requested = _clean_ids(element_ids)
    if mode != "remove":
        check_element_ids(requested, organization_id)

    rows = (
        db.session.query(CardArchiMateElement)
        .filter(CardArchiMateElement.card_id == card.id)
        .order_by(CardArchiMateElement.id)
        .all()
    )
    kept_rows: List[CardArchiMateElement] = []
    seen: List[int] = []
    for row in rows:
        if row.archimate_element_id in seen:
            db.session.delete(row)  # duplicate link to the same element
        else:
            seen.append(row.archimate_element_id)
            kept_rows.append(row)

    legacy = [i for i in _clean_ids(card.archimate_element_ids or []) if i not in seen]
    legacy_valid = _elements_in_org(legacy, organization_id)
    adopted = [i for i in legacy if i in legacy_valid]
    current = seen + adopted

    if mode == "add":
        target = current + [i for i in requested if i not in current]
    elif mode == "remove":
        target = [i for i in current if i not in requested]
    else:
        target = requested

    for row in kept_rows:
        if row.archimate_element_id not in target:
            db.session.delete(row)
    final = [r.archimate_element_id for r in kept_rows if r.archimate_element_id in target]

    to_create = [i for i in target if i not in final]
    elements = _elements_in_org(to_create, organization_id)
    for element_id in to_create:
        db.session.add(
            CardArchiMateElement(
                card_id=card.id,
                archimate_element_id=element_id,
                relationship_type=relationship_type,
                archimate_layer=getattr(elements.get(element_id), "layer", None),
                created_by_id=actor_id,
            )
        )
        final.append(element_id)
    db.session.flush()

    card.archimate_element_ids = list(final)
    return list(final)

