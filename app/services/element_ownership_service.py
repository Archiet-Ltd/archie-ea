"""Element ownership: the one writer of owner records for any element type.

Writes through the one ownership record (``ApplicationOwner``) using its
``element_type`` / ``element_id`` reference. ``set_capability_owner`` (the
capability owner service) and the AI system owner route both call
``set_element_owner``; there is no second implementation of the checks.
"""
from __future__ import annotations

import logging
from typing import Callable, Iterable, Optional

from app import db
from app.models.application_owner import ApplicationOwner

logger = logging.getLogger(__name__)

# The three owner fields an application used to keep as plain text, and the
# ownership type each one is recorded as in the one ownership record.
APPLICATION_OWNER_FIELDS = {
    "application_owner": "primary",
    "business_owner": "business",
    "technical_owner": "technical",
}
# What an owner with neither a name nor an email reads as.
NAMELESS_OWNER_LABEL = "\u2014"


class CrossOrganisationElementOwner(ValueError):
    """The chosen owner, or the element itself, is not in this organisation."""


class DuplicateElementOwner(ValueError):
    """That user is already recorded as that kind of owner of the element."""


class InvalidOwnershipType(ValueError):
    """The ownership type is not one the ownership record allows."""


def _tenant_capability(element_id: int, organization_id: int):
    from app.services.capability_ownership_service import get_tenant_capability

    return get_tenant_capability(element_id, organization_id)


def _tenant_ai_system(element_id: int, organization_id: int):
    from app.models.models import ArchiMateElement
    from app.modules.enterprise_genome.services.ai_system_profile import is_ai_system

    element = ArchiMateElement.query.filter_by(
        id=element_id, organization_id=organization_id,
    ).first()
    return element if element is not None and is_ai_system(element) else None


def _tenant_application(element_id: int, organization_id: int):
    from app.models.application_portfolio import ApplicationComponent

    return ApplicationComponent.query.filter_by(
        id=element_id, organization_id=organization_id,
    ).first()


# element_type -> (lookup, message when the element is not in this organisation)
_ELEMENTS: dict[str, tuple[Callable, str]] = {
    "application": (_tenant_application, "That application does not belong to this organisation."),
    "capability": (_tenant_capability, "That capability does not belong to this organisation."),
    "ai_system": (_tenant_ai_system, "That AI system does not belong to this organisation."),
}


def set_element_owner(
    *,
    element_type: str,
    element_id: int,
    user_id: int,
    organization_id: int,
    ownership_type: str = "primary",
    assigned_by: Optional[int] = None,
    replace_primary: bool = False,
    replace_type: bool = False,
    commit: bool = True,
    refuse_duplicate: bool = False,
) -> ApplicationOwner:
    """Record *user_id* as an owner of the element.

    Refuses (``CrossOrganisationElementOwner``) an element or an owner from
    another organisation, and (``InvalidOwnershipType``) a type the ownership
    record does not allow, rather than store it. With ``replace_primary`` a new
    primary owner replaces the existing primary; with ``replace_type`` it
    replaces every owner of the same type. ``commit=False`` leaves the
    transaction to the caller (the application owner fields write inside the
    flush they were assigned in).

    An application is keyed by ``application_id``; every other element by
    ``element_type`` + ``element_id``.
    """
    from app.models.user import User

    if element_type not in _ELEMENTS:
        raise ValueError(f"Unsupported element type '{element_type}'")
    if ownership_type not in ApplicationOwner.OWNERSHIP_TYPES:
        raise InvalidOwnershipType(
            f"Invalid ownership type '{ownership_type}'. "
            f"Must be one of {', '.join(ApplicationOwner.OWNERSHIP_TYPES)}"
        )

    lookup, missing_message = _ELEMENTS[element_type]
    if lookup(element_id, organization_id) is None:
        raise CrossOrganisationElementOwner(missing_message)

    owner = User.query.filter_by(id=user_id, organization_id=organization_id).first()
    if owner is None:
        raise CrossOrganisationElementOwner("That user does not belong to this organisation.")

    key = _element_key(element_type, element_id)
    existing = ApplicationOwner.query.filter_by(
        user_id=user_id,
        ownership_type=ownership_type,
        organization_id=organization_id,
        **key,
    ).first()
    if existing is not None:
        if refuse_duplicate:
            raise DuplicateElementOwner(f"User already assigned as {ownership_type} owner")
        if replace_type:
            _delete_other_owners_of_type(key, organization_id, ownership_type, keep=existing)
        return existing

    if replace_type or (replace_primary and ownership_type == "primary"):
        _delete_other_owners_of_type(key, organization_id, ownership_type)

    record = ApplicationOwner(
        user_id=user_id,
        ownership_type=ownership_type,
        assigned_by=assigned_by,
        organization_id=organization_id,
        **key,
    )
    db.session.add(record)
    if commit:
        db.session.commit()
    return record


def _delete_other_owners_of_type(key: dict, organization_id: int, ownership_type: str, keep=None) -> None:
    for row in ApplicationOwner.query.filter_by(
        organization_id=organization_id, ownership_type=ownership_type, **key,
    ).all():
        if keep is None or row.id != keep.id:
            db.session.delete(row)


def _element_key(element_type: str, element_id: int) -> dict:
    """How the ownership record points at the element."""
    if element_type == "application":
        return {"application_id": element_id}
    return {"element_type": element_type, "element_id": element_id}


def resolve_user_by_name(name: str, organization_id: int):  # reuse-ok: the one name-to-user lookup; the owner backfill's copy was removed for it
    """The one user in *organization_id* whose full name or email is *name*.

    Full name first, then email, both exact and case-insensitive. A first name
    alone, a last name alone or an ambiguous match resolves to nothing: an owner
    is never guessed.
    """
    from app.models.user import User

    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    for match in (
        db.func.lower(db.func.concat(User.first_name, " ", User.last_name)),
        db.func.lower(User.email),
    ):
        users = User.query.filter(User.organization_id == organization_id).filter(match == wanted).all()
        if len(users) == 1:
            return users[0]
    return None


def owner_label(first_name: Optional[str], last_name: Optional[str], email: Optional[str] = None) -> str:
    """The name an owner is shown by; a nameless owner is shown by email.

    A recorded owner must never read as absent, so a user with no name reads as
    the email address rather than a placeholder consumers treat as "no owner".
    """
    return " ".join(part for part in (first_name, last_name) if part).strip() or email or NAMELESS_OWNER_LABEL


def _owner_labels_for(
    application_ids: Iterable[int], organization_id: int, ownership_type: str, application=None,
) -> dict:
    """{application_id: label of its first owner of *ownership_type*}, one query."""
    from app.models.user import User

    application_ids = [i for i in application_ids if i is not None]
    if not application_ids:
        return {}
    from sqlalchemy.orm import object_session

    # Read on the session the application was loaded in, so the query joins its
    # transaction instead of beginning one on the global session.
    session = (object_session(application) if application is not None else None) or db.session
    rows = session.execute(
        db.select(ApplicationOwner.application_id, User.first_name, User.last_name, User.email)
        .join(User, User.id == ApplicationOwner.user_id)
        .where(ApplicationOwner.application_id.in_(application_ids))
        .where(ApplicationOwner.organization_id == organization_id)
        .where(User.organization_id == organization_id)
        .where(ApplicationOwner.ownership_type == ownership_type)
        .order_by(ApplicationOwner.id)
    ).all()
    labels: dict = {}
    for application_id, first_name, last_name, email in rows:
        labels.setdefault(application_id, owner_label(first_name, last_name, email))
    return labels


def preload_application_owner_text(applications: Iterable) -> None:
    """Fill the owner-field reads of *applications* with one query per owner type.

    A list screen calls this once instead of paying a query per row and field.
    """
    by_org: dict = {}
    for application in applications:
        if application.id is not None and application.organization_id is not None:
            by_org.setdefault(application.organization_id, []).append(application)
    for organization_id, apps in by_org.items():
        ids = [a.id for a in apps]
        for field, ownership_type in APPLICATION_OWNER_FIELDS.items():
            labels = _owner_labels_for(ids, organization_id, ownership_type, application=apps[0])
            for application in apps:
                application.__dict__.setdefault("_owner_text_cache", {})[field] = labels.get(application.id)


def application_owner_text(application, field: str) -> Optional[str]:
    """What an application's owner field reads as.

    The recorded owner of that type (``ApplicationOwner``), by name. Only when no
    owner of that type is recorded -- a name that matched no user and so could
    not be recorded as one -- does it read the text the field was given, so a
    name imported from outside is never made to disappear.
    """
    staged = application.__dict__.get("_pending_owner_text", {})
    if staged.get(field) is not None:
        return staged[field]
    cache = application.__dict__.get("_owner_text_cache", {})
    if field not in cache:
        label = None
        if application.id is not None and application.organization_id is not None:
            label = _owner_labels_for(
                [application.id], application.organization_id, APPLICATION_OWNER_FIELDS[field],
                application=application,
            ).get(application.id)
        cache = application.__dict__.setdefault("_owner_text_cache", {})
        cache[field] = label
    if cache[field] is not None:
        return cache[field]
    return getattr(application, "legacy_" + field)


def stage_application_owner_text(application, field: str, value) -> None:
    """Assign an owner field. Recorded as an owner at the next flush.

    A blank value records nothing and removes nothing: an import row with an
    empty owner cell never blanks an owner already on record.
    """
    from sqlalchemy import inspect
    from sqlalchemy.orm import object_session
    from sqlalchemy.orm.attributes import flag_modified, set_committed_value

    text = (value or "").strip() or None
    if text is None:
        return
    application.__dict__.setdefault("_pending_owner_text", {})[field] = text
    application.__dict__.pop("_owner_text_cache", None)
    session = object_session(application)
    if session is not None:
        # An assignment changes no column, so the session would not see the
        # application as changed and could skip the flush that records the
        # owner. Marking the column the name falls back to as modified makes it
        # dirty; the flush listener then finds it in the session as usual.
        if inspect(application).persistent:
            column = "legacy_" + field
            set_committed_value(application, column, getattr(application, column))
            flag_modified(application, column)


def apply_staged_application_owners(application, organization_id: Optional[int]) -> list:
    """Record the owner fields assigned on *application* as ownership records.

    A name that matches exactly one user of the organisation becomes that user's
    ``ApplicationOwner`` row, replacing the application's owner of that type. A
    name that matches nobody stays as text on the application (it cannot be made
    a user) and never replaces an owner already on record. Returns the fields
    left as text.
    """
    staged = application.__dict__.pop("_pending_owner_text", {})
    application.__dict__.pop("_owner_text_cache", None)
    unresolved = []
    for field, text in staged.items():
        user = resolve_user_by_name(text, organization_id) if organization_id else None
        if user is None:
            unresolved.append(field)
            recorded = bool(application.id is not None and organization_id is not None and _owner_labels_for(
                [application.id], organization_id, APPLICATION_OWNER_FIELDS[field], application=application))
            if recorded:
                logger.info(
                    "Owner text %r for application %s matched no user; the recorded %s owner is kept",
                    text, application.id, APPLICATION_OWNER_FIELDS[field],
                )
            else:
                setattr(application, "legacy_" + field, text[:100])
            continue
        set_application_owner(
            application, user.id, APPLICATION_OWNER_FIELDS[field],
            organization_id=organization_id, replace_type=True,
        )
        setattr(application, "legacy_" + field, None)
    return unresolved


def set_application_owner(
    application,
    user_id: int,
    ownership_type: str,
    *,
    organization_id: int,
    assigned_by: Optional[int] = None,
    replace_type: bool = False,
) -> ApplicationOwner:
    """Record an owner of an application that may not be flushed yet.

    The same checks as ``set_element_owner`` (organisation of the application,
    organisation of the user, type allowed) without reading the application back
    by id, so an application created in this same flush can be given an owner.
    """
    if application.id is not None:
        return set_element_owner(
            element_type="application", element_id=application.id, user_id=user_id,
            organization_id=organization_id, ownership_type=ownership_type,
            assigned_by=assigned_by, replace_type=replace_type, commit=False,
        )
    from app.models.user import User

    if ownership_type not in ApplicationOwner.OWNERSHIP_TYPES:
        raise InvalidOwnershipType(f"Invalid ownership type '{ownership_type}'.")
    if application.organization_id not in (None, organization_id):
        raise CrossOrganisationElementOwner("That application does not belong to this organisation.")
    if User.query.filter_by(id=user_id, organization_id=organization_id).first() is None:
        raise CrossOrganisationElementOwner("That user does not belong to this organisation.")
    record = ApplicationOwner(
        application=application, user_id=user_id, ownership_type=ownership_type,
        assigned_by=assigned_by, organization_id=organization_id,
    )
    db.session.add(record)
    return record


def application_owner_expression(model, field: str):
    """SQL for an owner field, for filters and ordering on the application model.

    The same answer ``application_owner_text`` gives: the first owner of that
    type by name, else the text the field was given.
    """
    from app.models.user import User

    owners = ApplicationOwner.__table__.alias("owner_field_ao")
    users = User.__table__.alias("owner_field_user")
    name = db.func.coalesce(
        db.func.nullif(db.func.trim(db.func.concat_ws(" ", users.c.first_name, users.c.last_name)), ""),
        users.c.email,
        NAMELESS_OWNER_LABEL,
    )
    recorded = (
        db.select(name)
        .select_from(owners.join(users, users.c.id == owners.c.user_id))
        .where(owners.c.application_id == model.id)
        .where(owners.c.organization_id == model.organization_id)
        .where(users.c.organization_id == model.organization_id)
        .where(owners.c.ownership_type == APPLICATION_OWNER_FIELDS[field])
        .order_by(owners.c.id)
        .limit(1)
        .scalar_subquery()
    )
    return db.func.coalesce(recorded, getattr(model, "legacy_" + field))


# ── "owned": the one rule every screen reads ─────────────────────────────────
# An application is owned when it has an ACCOUNTABLE owner: a primary or
# business owner record, or a non-empty legacy application/business owner text.
# The named user must belong to the application's organisation. A technical,
# backup or steward owner alone does not make it owned: the owner
# the product's questions ask about ("who is accountable for ...") is the
# accountable one. The dashboard's owner coverage and the Ask question
# "applications with no owner" both call these two functions; do not write a
# second definition.
ACCOUNTABLE_OWNERSHIP_TYPES = ("primary", "business")
ACCOUNTABLE_LEGACY_OWNER_COLUMNS = ("legacy_application_owner", "legacy_business_owner")


def application_is_owned_expression(model=None):
    """SQL boolean: the application row (``model``, default ApplicationComponent) is owned."""
    if model is None:
        from app.models.application_portfolio import ApplicationComponent as model

    has_text = db.or_(*[
        db.and_(getattr(model, column).isnot(None), db.func.trim(getattr(model, column)) != "")
        for column in ACCOUNTABLE_LEGACY_OWNER_COLUMNS
    ])
    # The named user must belong to the application's organisation: a record
    # whose user is elsewhere names nobody who can be held accountable here.
    from app.models.user import User

    has_record = (
        db.select(ApplicationOwner.id)
        .join(User, User.id == ApplicationOwner.user_id)
        .where(ApplicationOwner.application_id == model.id)
        .where(ApplicationOwner.organization_id == model.organization_id)
        .where(User.organization_id == model.organization_id)
        .where(ApplicationOwner.ownership_type.in_(ACCOUNTABLE_OWNERSHIP_TYPES))
        .exists()
    )
    return db.or_(has_text, has_record)


def application_is_owned(application) -> bool:
    """The same rule as ``application_is_owned_expression``, for one loaded application."""
    for column in ACCOUNTABLE_LEGACY_OWNER_COLUMNS:
        if (getattr(application, column, None) or "").strip():
            return True
    from app.models.user import User

    return db.session.query(
        db.session.query(ApplicationOwner.id)
        .join(User, User.id == ApplicationOwner.user_id)
        .filter(
            ApplicationOwner.application_id == application.id,
            ApplicationOwner.organization_id == application.organization_id,
            User.organization_id == application.organization_id,
            ApplicationOwner.ownership_type.in_(ACCOUNTABLE_OWNERSHIP_TYPES),
        )
        .exists()
    ).scalar()
