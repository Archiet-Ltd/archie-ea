"""What a change event says beyond "something changed".

The outbox hook (``app/models/archimate_outbox_sync.py``) calls this module to
add three things to every watched change: ``changes`` (field, before, after,
for the fields an owner would act on), ``owner_user_ids`` (read through the
one owner reader) and ``source`` (who or what made the change).

Nothing here emits an event or stores one; it only describes a change. No
credential, token or password value ever leaves this module: field names come
from a fixed list per model, and ``scrub_payload`` drops any key that names a
secret before the payload is queued.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy import inspect

logger = logging.getLogger(__name__)

SECRET_FIELD = re.compile(
    r"(password|passwd|secret|token|credential|api[_-]?key|private[_-]?key|authorization)",
    re.IGNORECASE,
)

# The fields an owner would act on, per entity type. A field not listed here
# never appears in a payload.
WATCHED_FIELDS: Dict[str, tuple] = {
    "archimate_element": ("name", "type", "layer", "plateau"),
    "archimate_relationship": ("type",),
    "application_component": (
        "name",
        "lifecycle_status",
        "deployment_status",
        "criticality",
        "business_criticality",
        "go_live_date",
        "planned_retirement_date",
        "end_of_life_date",
        "contract_expiry_date",
        "total_cost_of_ownership",
        "license_cost",
        "license_cost_annual",
        "maintenance_cost",
        "infrastructure_cost",
        "support_cost",
    ),
    "application_owner": ("user_id", "ownership_type", "application_id", "element_type", "element_id"),
    "cost_fact": ("amount", "currency", "kind", "category", "element_type", "element_id"),
    "risk": (
        "title",
        "likelihood",
        "impact",
        "inherent_likelihood",
        "inherent_impact",
        "residual_likelihood",
        "residual_impact",
        "status",
        "owner",
    ),
}


def json_value(value: Any) -> Any:
    """A JSON-safe copy of a column value."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.name
    return str(value)


def changes_for(entity_type: str, target: object, action: str) -> List[dict]:
    """The watched fields that changed in this flush, with before and after.

    Reads SQLAlchemy's attribute history, so no query is made. A created row
    reports every watched field it was given (before ``None``); a deleted row
    reports each current value as the "before".
    """
    state = inspect(target)
    found: List[dict] = []
    for name in WATCHED_FIELDS.get(entity_type, ()):
        if SECRET_FIELD.search(name) or name not in state.attrs:
            continue
        history = state.attrs[name].history
        if action == "deleted":
            current = (history.unchanged or history.deleted or [None])[0]
            if current is None:
                continue
            found.append({"field": name, "before": json_value(current), "after": None})
            continue
        if not history.has_changes():
            continue
        before = json_value(history.deleted[0]) if history.deleted else None
        after = json_value(history.added[0]) if history.added else None
        if before == after:
            continue
        found.append({"field": name, "before": before, "after": after})
    return found


def _application_for_element(element_id: Optional[int], organization_id: int) -> Optional[int]:
    if element_id is None:
        return None
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    return db.session.execute(
        db.select(ApplicationComponent.id)
        .where(ApplicationComponent.archimate_element_id == element_id)
        .where(ApplicationComponent.organization_id == organization_id)
        .limit(1)
    ).scalar()


def _owner_ids_of(element_type: Optional[str], element_id: Optional[int], organization_id: int) -> List[int]:
    from app.models.application_owner import ApplicationOwner

    if element_id is None:
        return []
    if element_type == "application":
        rows = ApplicationOwner.get_display_rows_for_application(element_id, organization_id)
    else:
        rows = ApplicationOwner.get_display_rows_for_element(element_type, element_id, organization_id)
    return sorted({row["user_id"] for row in rows})


def owner_user_ids(entity_type: str, target: object, organization_id: int, action: str) -> List[int]:
    """The users who own the changed thing, through the one owner reader."""
    if entity_type == "application_component":
        return _owner_ids_of("application", getattr(target, "id", None), organization_id)
    if entity_type == "application_owner":
        if getattr(target, "application_id", None) is not None:
            return _owner_ids_of("application", target.application_id, organization_id)
        return _owner_ids_of(getattr(target, "element_type", None), getattr(target, "element_id", None), organization_id)
    if entity_type == "cost_fact":
        return _owner_ids_of(getattr(target, "element_type", None), getattr(target, "element_id", None), organization_id)
    if entity_type == "risk":
        application_id = _application_for_element(getattr(target, "archimate_element_id", None), organization_id)
        return _owner_ids_of("application", application_id, organization_id)
    if entity_type == "archimate_element" and action != "created":
        if str(getattr(target, "type", "")).replace(" ", "").lower() == "applicationcomponent":
            application_id = _application_for_element(getattr(target, "id", None), organization_id)
            return _owner_ids_of("application", application_id, organization_id)
    return []


def subject_of(entity_type: str, target: object, organization_id: int) -> Dict[str, Any]:
    """The application an owner record or a cost fact is about: its name, for
    the alert's wording, and its ArchiMate id, for the link to its answer."""
    if entity_type == "application_owner":
        application_id = getattr(target, "application_id", None)
    elif entity_type == "cost_fact" and getattr(target, "element_type", None) == "application":
        application_id = getattr(target, "element_id", None)
    else:
        return {}
    if application_id is None:
        return {}
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    row = db.session.execute(
        db.select(ApplicationComponent.name, ApplicationComponent.archimate_element_id)
        .where(ApplicationComponent.id == application_id)
        .where(ApplicationComponent.organization_id == organization_id)
        .limit(1)
    ).first()
    if row is None:
        return {}
    return {"subject_name": row.name, "archimate_element_id": row.archimate_element_id}


def _signed_in_user_id() -> Optional[int]:
    """The signed-in user's id, read without resolving the login user.

    Asking ``current_user`` here would load the user and cache the result on
    ``g`` for the rest of the request. A write made before the request has
    authenticated (a seed, an import that opens its own request context) would
    then pin an anonymous user for every later call. So read the user already
    resolved, and otherwise the id the session carries.
    """
    from flask import g, has_request_context, session as http_session

    if not has_request_context():
        return None
    resolved = g.get("_login_user")
    if resolved is not None:
        if getattr(resolved, "is_authenticated", False):
            return getattr(resolved, "id", None)
        return None
    raw = http_session.get("_user_id")
    return int(raw) if str(raw).isdigit() else None


def source_of(session: Any, target: object) -> dict:
    """Who or what made the change, and the origin the row itself records.

    A connector or import names itself by putting ``change_source`` on the
    session; otherwise the signed-in user, otherwise the system, in both cases
    with the origin field the model carries (``data_source`` or ``source``).
    """
    declared = session.info.get("change_source")
    if isinstance(declared, dict) and declared.get("kind"):
        return {
            key: json_value(value)
            for key, value in declared.items()
            if not SECRET_FIELD.search(str(key))
        }

    found: dict = {"kind": "system"}
    try:
        user_id = _signed_in_user_id()
        if user_id is not None:
            found = {"kind": "user", "user_id": user_id}
    except Exception:  # noqa: BLE001 - a missing user must never block the write
        logger.debug("change feed: could not read the signed-in user", exc_info=True)
    origin = getattr(target, "data_source", None) or getattr(target, "source", None)
    if isinstance(origin, str) and origin:
        found["origin"] = origin
    return found


def scrub_payload(payload: Any) -> Any:
    """Drop every key, and every change entry, that names a secret."""
    if isinstance(payload, dict):
        return {
            key: scrub_payload(value)
            for key, value in payload.items()
            if not SECRET_FIELD.search(str(key))
        }
    if isinstance(payload, list):
        return [
            scrub_payload(item)
            for item in payload
            if not (
                isinstance(item, dict)
                and isinstance(item.get("field"), str)
                and SECRET_FIELD.search(item["field"])
            )
        ]
    return payload


def describe_change(
    session: Any,
    entity_type: str,
    action: str,
    target: object,
    organization_id: int,
) -> Dict[str, Any]:
    """The extra payload keys for one watched change."""
    extra: Dict[str, Any] = {
        "changes": changes_for(entity_type, target, action),
        "owner_user_ids": [],
        "source": source_of(session, target),
    }
    try:
        extra["owner_user_ids"] = owner_user_ids(entity_type, target, organization_id, action)
    except Exception:  # noqa: BLE001 - the write must go through without owners
        logger.exception("change feed: could not read owners for %s", entity_type)
    if entity_type == "application_component":
        extra["archimate_element_id"] = getattr(target, "archimate_element_id", None)
    try:
        extra.update(subject_of(entity_type, target, organization_id))
    except Exception:  # noqa: BLE001 - the write must go through without a subject
        logger.exception("change feed: could not read the subject of %s", entity_type)
    return scrub_payload(extra)


__all__ = [
    "WATCHED_FIELDS",
    "changes_for",
    "describe_change",
    "json_value",
    "owner_user_ids",
    "scrub_payload",
    "source_of",
]
