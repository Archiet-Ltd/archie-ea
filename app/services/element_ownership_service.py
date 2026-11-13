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


def _tenant_business_process(element_id: int, organization_id: int):
    from app.models.process_data import BusinessProcess

    return BusinessProcess.query.filter_by(
        id=element_id, organization_id=organization_id,
    ).first()


def _tenant_gap(element_id: int, organization_id: int):
    from app.models.implementation_migration import Gap

    return Gap.query.filter_by(id=element_id, organization_id=organization_id).first()


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
    "business_process": (_tenant_business_process, "That business process does not belong to this organisation."),
    "gap": (_tenant_gap, "That gap does not belong to this organisation."),
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
        # A change of owner is one update of the existing record (so the change
        # history shows who it was and who it became), not a delete and a create.
        current = ApplicationOwner.query.filter_by(
            organization_id=organization_id, ownership_type=ownership_type, **key,
        ).order_by(ApplicationOwner.id).all()
        if current:
            record = current[0]
            record.user_id = user_id
            # A named person without an account, replaced by a user, leaves no name behind.
            record.person_name = record.person_email = record.person_role = None
            record.assigned_by = assigned_by
            _delete_other_owners_of_type(key, organization_id, ownership_type, keep=record)
            if commit:
                db.session.commit()
            return record

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


def _match_users_by_name(name: str, organization_id: int):  # reuse-ok: the body of the one name-to-user lookup (resolve_user_by_name), which also reports who a name was ambiguous between
    """(the one user, the people it was ambiguous between): full name first, then email, both
    exact and case-insensitive. Exactly one of the two is set, or neither when nobody matches."""
    from app.models.user import User

    wanted = (name or "").strip().lower()
    if not wanted:
        return None, []
    ambiguous = []
    for match in (
        db.func.lower(db.func.concat(User.first_name, " ", User.last_name)),
        db.func.lower(User.email),
    ):
        users = User.query.filter(User.organization_id == organization_id).filter(match == wanted).all()
        if len(users) == 1:
            return users[0], []
        if len(users) > 1 and not ambiguous:
            ambiguous = users
    return None, ambiguous


def resolve_user_by_name(name: str, organization_id: int):  # reuse-ok: the one name-to-user lookup; the owner backfill's copy was removed for it
    """The one user in *organization_id* whose full name or email is *name*.

    Full name first, then email, both exact and case-insensitive. A first name
    alone, a last name alone or an ambiguous match resolves to nothing: an owner
    is never guessed. ``users_named_alike`` tells "nobody" from "more than one".
    """
    return _match_users_by_name(name, organization_id)[0]


def users_named_alike(name: str, organization_id: int) -> list:
    """The two or more users of *organization_id* a name matches when it names no single one,
    else an empty list: how "nobody has that name" is told from "more than one person has it"."""
    return _match_users_by_name(name, organization_id)[1]


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
        db.select(
            ApplicationOwner.application_id, User.first_name, User.last_name, User.email,
            ApplicationOwner.person_name,
        )
        .outerjoin(
            User, db.and_(User.id == ApplicationOwner.user_id, User.organization_id == organization_id),
        )
        .where(ApplicationOwner.application_id.in_(application_ids))
        .where(ApplicationOwner.organization_id == organization_id)
        .where(_names_someone_here())
        .where(ApplicationOwner.ownership_type == ownership_type)
        .order_by(ApplicationOwner.id)
    ).all()
    labels: dict = {}
    for application_id, first_name, last_name, email, person_name in rows:
        labels.setdefault(application_id, person_name or owner_label(first_name, last_name, email))
    return labels


def _names_someone_here():
    """SQL: the owner record names a user of its organisation, or a person by name.

    Used with an outer join to ``User`` limited to the record's organisation, so
    a record whose user is elsewhere names nobody here, while a named person
    without an account (no user, a name) is a real owner.
    """
    from app.models.user import User

    return db.or_(
        db.and_(ApplicationOwner.user_id.is_(None), ApplicationOwner.person_name.isnot(None)),
        db.and_(ApplicationOwner.user_id.isnot(None), User.id.isnot(None)),
    )


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
    a user) and never replaces an owner already on record; a person who is to be
    recorded without an account is recorded on purpose, with
    ``set_named_person_owner``. Returns the fields left as text.
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


def application_owner_edit_problem(application, field: str, text: Optional[str], organization_id: int) -> Optional[str]:
    """Why typing *text* into an owner field would change nothing, or ``None``.

    A name is always recordable: a user of the organisation, or else a named
    person without an account. Only clearing the field of an owner already on
    record changes nothing (an owner is replaced, not removed, here), so the
    caller refuses that in words instead of reporting a save that did not
    happen.
    """
    text = (text or "").strip()
    if text == (getattr(application, field) or ""):
        return None
    ownership_type = APPLICATION_OWNER_FIELDS[field]
    if text or application.id is None or not _owner_labels_for(
        [application.id], organization_id, ownership_type, application=application,
    ):
        return None
    return (
        "has an owner on record, so it cannot be cleared here. "
        "Type another person's full name or email to change it."
    )


PERSON_NAME_LENGTH = 200
PERSON_EMAIL_LENGTH = 255
PERSON_ROLE_LENGTH = 100


def set_named_person_owner(
    application,
    name: str,
    ownership_type: str,
    *,
    organization_id: int,
    email: Optional[str] = None,
    role: Optional[str] = None,
    assigned_by: Optional[int] = None,
    replace: bool = True,
) -> ApplicationOwner:
    """Record a person with no account in this workspace as an owner of an application.

    The same ownership record a user owner is written to, holding a name, an
    optional email and an optional role in place of a user. With ``replace`` (the
    default) it replaces the application's existing owner of that type, a person
    or a user, so a change of owner is one update; without it, it is added beside
    them. The application must belong to *organization_id*. The same person
    recorded twice as one type is one row.
    """
    if any(v is not None and not isinstance(v, str) for v in (name, email, role)):
        raise ValueError("The name, email address and role of a named owner must be text.")
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("A named owner needs a name.")
    if ownership_type not in ApplicationOwner.OWNERSHIP_TYPES:
        raise InvalidOwnershipType(f"Invalid ownership type '{ownership_type}'.")
    if application.organization_id not in (None, organization_id):
        raise CrossOrganisationElementOwner("That application does not belong to this organisation.")
    email = (email or "").strip()[:PERSON_EMAIL_LENGTH] or None
    role = (role or "").strip()[:PERSON_ROLE_LENGTH] or None

    values = dict(
        person_name=name[:PERSON_NAME_LENGTH], person_email=email, person_role=role,
        user_id=None, assigned_by=assigned_by,
    )
    # The owner is on record now: the text the field was given is no longer the answer,
    # and what the application read for this field before is stale.
    for field, recorded_type in APPLICATION_OWNER_FIELDS.items():
        if recorded_type == ownership_type:
            setattr(application, "legacy_" + field, None)
    application.__dict__.pop("_owner_text_cache", None)
    if application.id is not None:
        current = ApplicationOwner.query.filter_by(
            application_id=application.id, organization_id=organization_id,
            ownership_type=ownership_type,
        ).order_by(ApplicationOwner.id).all()
        same = next(
            (r for r in current if r.user_id is None and (r.person_name or "").lower() == values["person_name"].lower()),
            None,
        )
        if same is not None:
            # Already recorded: keep the row, fill in what is new.
            same.person_email = email or same.person_email
            same.person_role = role or same.person_role
            record = same
        elif replace and current:
            record = current[0]
            record.person_name, record.person_email, record.person_role = (
                values["person_name"], email, role,
            )
            record.user_id, record.assigned_by = None, assigned_by
        else:
            record = None
        if replace and record is not None:
            for other in current:
                if other.id != record.id:
                    db.session.delete(other)
        if record is not None:
            return record
        record = ApplicationOwner(
            application_id=application.id, ownership_type=ownership_type,
            organization_id=organization_id, **values,
        )
    else:
        record = ApplicationOwner(
            application=application, ownership_type=ownership_type,
            organization_id=organization_id, **values,
        )
    db.session.add(record)
    return record


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
        owners.c.person_name,
        db.func.nullif(db.func.trim(db.func.concat_ws(" ", users.c.first_name, users.c.last_name)), ""),
        users.c.email,
        NAMELESS_OWNER_LABEL,
    )
    recorded = (
        db.select(name)
        .select_from(owners.outerjoin(
            users, db.and_(users.c.id == owners.c.user_id, users.c.organization_id == model.organization_id),
        ))
        .where(owners.c.application_id == model.id)
        .where(owners.c.organization_id == model.organization_id)
        .where(db.or_(
            db.and_(owners.c.user_id.is_(None), owners.c.person_name.isnot(None)),
            db.and_(owners.c.user_id.isnot(None), users.c.id.isnot(None)),
        ))
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
# Text made only of these characters names nobody. SQL trim() strips plain spaces alone, so every rule
# strips this one set explicitly and agrees with Python on what a space is: every Unicode space character.
OWNER_TEXT_BLANKS = "".join(chr(code) for code in range(0x3100) if chr(code).isspace())


def application_is_owned_expression(model=None):
    """SQL boolean: the application row (``model``, default ApplicationComponent) is owned."""
    if model is None:
        from app.models.application_portfolio import ApplicationComponent as model

    has_text = db.or_(*[
        db.and_(getattr(model, column).isnot(None), db.func.btrim(getattr(model, column), OWNER_TEXT_BLANKS) != "")
        for column in ACCOUNTABLE_LEGACY_OWNER_COLUMNS
    ])
    # The named user must belong to the application's organisation: a record
    # whose user is elsewhere names nobody who can be held accountable here. A
    # named person without an account is a recorded owner too.
    from app.models.user import User

    has_record = (
        db.select(ApplicationOwner.id)
        .outerjoin(User, db.and_(User.id == ApplicationOwner.user_id, User.organization_id == model.organization_id))
        .where(ApplicationOwner.application_id == model.id)
        .where(ApplicationOwner.organization_id == model.organization_id)
        .where(_names_someone_here())
        .where(ApplicationOwner.ownership_type.in_(ACCOUNTABLE_OWNERSHIP_TYPES))
        .exists()
    )
    return db.or_(has_text, has_record)


def application_is_owned(application) -> bool:
    """The same rule as ``application_is_owned_expression``, for one loaded application."""
    for column in ACCOUNTABLE_LEGACY_OWNER_COLUMNS:
        if (getattr(application, column, None) or "").strip(OWNER_TEXT_BLANKS):
            return True
    from app.models.user import User

    return db.session.query(
        db.session.query(ApplicationOwner.id)
        .outerjoin(User, db.and_(
            User.id == ApplicationOwner.user_id, User.organization_id == application.organization_id,
        ))
        .filter(
            ApplicationOwner.application_id == application.id,
            ApplicationOwner.organization_id == application.organization_id,
            _names_someone_here(),
            ApplicationOwner.ownership_type.in_(ACCOUNTABLE_OWNERSHIP_TYPES),
        )
        .exists()
    ).scalar()
