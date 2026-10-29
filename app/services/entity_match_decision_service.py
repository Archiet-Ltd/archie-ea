"""Server-side effect of the Match-entities page's four controls (T-match-01).

The entity matching assistant (``ChatEntityMatchingService``) only ever reads:
it extracts text and compares it to what already exists, but never writes
anything. Clicking Accept, Resolve, Import or Review on a suggestion used to
run a client-only status flip (or nothing at all) -- CLAUDE.md s.22/25 treats
a control that looks like it saves something but doesn't as a defect, not a
style gap.

Scope: wired for the ``archimate_elements`` category only. That is the one
category where "confirm this is the same element" and "create this as a new
one" both land on a single, already-tenant-scoped model
(``ArchiMateElement``) with no merge step invented here -- an accepted or
resolved match only ever fills in a blank field on the existing row, exactly
how ``update_element`` (architecture_crud_routes.py) already edits one.
Applications/vendors/capabilities report an honest "not wired yet" rather
than silently doing nothing.
"""
from __future__ import annotations

from typing import Any

from flask import url_for

from app.extensions import db
from app.models.archimate_core import ArchiMateElement
from app.models.audit_log import AuditLog

#: The one category this file actually saves a decision for.
SUPPORTED_CATEGORY = "archimate_elements"

NOT_WIRED = (
    "Matching only saves a decision for architecture elements today; "
    "{category} isn't wired to one yet."
)

_DESCRIPTION_LIMIT = 2000


def _snapshot(element: ArchiMateElement) -> dict[str, Any]:
    return {
        "name": element.name,
        "type": element.type,
        "layer": element.layer,
        "description": element.description,
    }


def _record_url(element_id: int) -> str:
    return url_for("architecture_crud.view_element", element_id=element_id)


def _record(table_name: str, record_id: int, action: str, before, after, user_id, organization_id, reason: str) -> None:
    """The one history writer (AuditLog) -- never raises into the caller.

    ``change_reason`` goes inside ``new_value``, not ``extra_json``: that is
    the key ``HistoryService.difference()`` actually reads
    (``(after or {}).get("change_reason")``) to show "why" on the changes
    page, so a reason recorded anywhere else would never be shown.
    """
    AuditLog.log(
        table_name=table_name,
        record_id=record_id,
        action=action,
        old_value=before,
        new_value={**after, "change_reason": reason},
        user_id=user_id,
        organization_id=organization_id,
        extra_json={"source": "ai_chat_entity_matching"},
    )


def _apply_extracted(element: ArchiMateElement, extracted: dict | None) -> dict[str, Any]:
    """Fill blanks only -- never overwrite a value a person already set."""
    changed: dict[str, Any] = {}
    if not extracted:
        return changed
    description = extracted.get("description")
    if description and not (element.description or "").strip():
        element.description = str(description)[:_DESCRIPTION_LIMIT]
        changed["description"] = element.description
    return changed


def confirm_match(
    entity_id: int | None,
    extracted: dict | None,
    user_id: int | None,
    organization_id: int | None,
    *,
    reason: str,
) -> dict[str, Any]:
    """Accept or resolve: the extracted text and this element are the same
    real-world thing. Folds in whatever the document had that the record is
    missing, and records the decision -- never creates a second row."""
    if not entity_id:
        return {"success": False, "message": "This suggestion has no existing element to confirm against."}

    element = ArchiMateElement.query.filter_by(id=entity_id).first()
    if element is None:
        return {"success": False, "message": "That element wasn't found in this organisation."}

    before = _snapshot(element)
    changed = _apply_extracted(element, extracted)
    db.session.commit()
    after = _snapshot(element)
    _record("archimate_elements", element.id, "update", before, after, user_id, organization_id, reason)
    db.session.commit()

    if changed:
        message = "Saved: ‘%s’'s description was filled in from the document." % element.name
    else:
        message = "Confirmed: this is the same element as ‘%s’. Nothing needed changing." % element.name
    return {
        "success": True,
        "message": message,
        "record_id": element.id,
        "record_url": _record_url(element.id),
    }


def import_entity(data: dict | None, user_id: int | None, organization_id: int | None) -> dict[str, Any]:
    """Create the new element the document described. Minimal, honest
    defaults -- no field is guessed beyond what the document actually gave."""
    data = data or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return {"success": False, "message": "This suggestion has no name to import."}

    element = ArchiMateElement(
        name=name[:500],
        type=str(data.get("type") or "ApplicationComponent")[:50],
        layer=str(data.get("layer") or "Application")[:30],
        description=(str(data["description"])[:_DESCRIPTION_LIMIT] if data.get("description") else None),
    )
    db.session.add(element)
    db.session.commit()
    _record(
        "archimate_elements", element.id, "create", None, _snapshot(element),
        user_id, organization_id, "imported from a matched document",
    )
    db.session.commit()
    return {
        "success": True,
        "message": "Imported ‘%s’ into the architecture." % element.name,
        "record_id": element.id,
        "record_url": _record_url(element.id),
    }


def review_entity(category: str | None, entity_id: int | None) -> dict[str, Any]:
    """Where to look at this suggestion's real record, or why there isn't one yet."""
    if category != SUPPORTED_CATEGORY or not entity_id:
        return {
            "success": False,
            "message": "Nothing saved yet to review — import it first, then open it from its own page.",
        }
    element = ArchiMateElement.query.filter_by(id=entity_id).first()
    if element is None:
        return {"success": False, "message": "That element wasn't found in this organisation."}
    return {
        "success": True,
        "message": "Opening ‘%s’." % element.name,
        "record_id": element.id,
        "record_url": _record_url(element.id),
    }
