"""What an application record is: an application, a part of one, or not software.

The one place that answers "is this record an application". The Applications list, the Overview's
Applications table, the application count and owner coverage all read ``is_application_expression``.

The kind is one stored fact, ``ApplicationComponent.application_kind``. When a person has set it, that
is the answer. When it is empty the rule decides: a record composed into or aggregated by another
application component is a part of that application; a record that is a code repository
(``repository_type`` filled, or a component type of "repository" that nobody owns yet, however it was entered) is not software; every
other record is an application.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func

from app import db

APPLICATION = "application"
PART = "part"
NOT_SOFTWARE = "not_software"
KINDS = (APPLICATION, PART, NOT_SOFTWARE)
KIND_LABELS = {APPLICATION: "Application", PART: "Part of an application", NOT_SOFTWARE: "Not software"}



def _parent_relationships():
    from app.services.archimate_import_service import _PARENT_RELATIONSHIPS

    return _PARENT_RELATIONSHIPS


def _has_parent_expression(model):
    """SQL boolean: another application component of the same organisation composes or aggregates this one."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.models import ArchiMateRelationship as R

    whole = db.aliased(ApplicationComponent)
    source = db.aliased(ArchiMateElement)
    return (
        db.select(R.id)
        .join(source, source.id == R.source_id)
        .join(
            whole,
            db.and_(
                whole.archimate_element_id == R.source_id,
                whole.organization_id == model.organization_id,
                whole.id != model.id,
            ),
        )
        .where(
            R.target_id == model.archimate_element_id,
            R.organization_id == model.organization_id,
            source.organization_id == model.organization_id,
            source.deleted_at.is_(None),
            func.lower(R.type).in_(_parent_relationships()),
        )
        .exists()
    )


def _is_repository_expression(model):
    """SQL boolean: the record is a code repository: it names the kind of repository it is, its component type
    is "repository", or the connector brought it in as one and nobody has named an owner for it yet (a person
    naming an owner says it is run)."""
    from app.services.element_ownership_service import application_is_owned_expression

    return db.or_(
        db.and_(model.repository_type.isnot(None), func.btrim(model.repository_type) != ""),
        db.and_(
            func.lower(func.btrim(func.coalesce(model.component_type, ""))) == "repository",
            ~application_is_owned_expression(model),
        ),
    )


def kind_expression(model=None):
    """SQL expression: the kind of each application record, a person's choice first, then the rule."""
    if model is None:
        from app.models.application_portfolio import ApplicationComponent as model
    return case(
        (model.application_kind.in_(KINDS), model.application_kind),
        (_has_parent_expression(model), PART),
        (_is_repository_expression(model), NOT_SOFTWARE),
        else_=APPLICATION,
    )


def is_application_expression(model=None):
    """SQL boolean: the record is an application. Every list and count of applications uses this."""
    return kind_expression(model) == APPLICATION


def applications_query():
    """``ApplicationComponent.query`` narrowed to applications: the starting point for any list or count of
    applications that a person or the chat reads. The organisation filter is applied by the session."""
    from app.models.application_portfolio import ApplicationComponent

    return ApplicationComponent.query.filter(is_application_expression(ApplicationComponent))


def wholes_of(organization_id, element_ids, applications_only=True) -> dict:
    """``{element id: [(application id or None, name), ...]}``: what composes or aggregates each element,
    by name. One query for a whole page. The one reading of composition for application parts.

    With ``applications_only`` a whole must be an application record (the rule for a part); without it any
    model element counts, and ``None`` stands for a whole that has no application record."""
    from app.models.application_portfolio import ApplicationComponent as A
    from app.models.archimate_core import ArchiMateElement as E
    from app.models.models import ArchiMateRelationship as R

    element_ids = [i for i in element_ids if i]
    if not element_ids:
        return {}
    query = (
        db.select(R.target_id, A.id, E.name)
        .select_from(R)
        .join(E, db.and_(E.id == R.source_id, E.organization_id == organization_id, E.deleted_at.is_(None)))
        .join(
            A,
            db.and_(A.archimate_element_id == R.source_id, A.organization_id == organization_id),
            isouter=not applications_only,
        )
        .where(
            R.organization_id == organization_id,
            R.target_id.in_(element_ids),
            func.lower(R.type).in_(_parent_relationships()),
            R.source_id != R.target_id,
        )
        .order_by(E.name)
    )
    found: dict = {}
    for target_id, app_id, name in db.session.execute(query).all():
        if (app_id, name) not in found.setdefault(target_id, []):
            found[target_id].append((app_id, name))
    return found


def parts_of(application) -> list:
    """The application records this one composes or aggregates, as ``(id, name)``, by name."""
    from app.models.application_portfolio import ApplicationComponent as A
    from app.models.models import ArchiMateRelationship as R

    if not application.archimate_element_id:
        return []
    rows = db.session.execute(
        db.select(A.id, A.name)
        .join(R, db.and_(R.target_id == A.archimate_element_id, R.organization_id == application.organization_id))
        .where(
            R.source_id == application.archimate_element_id,
            A.organization_id == application.organization_id,
            A.id != application.id,
            func.lower(R.type).in_(_parent_relationships()),
        )
        .order_by(A.name)
    ).all()
    seen, parts = set(), []
    for app_id, name in rows:
        if app_id not in seen:
            seen.add(app_id)
            parts.append((app_id, name))
    return parts


def kind_of(application) -> dict:
    """The kind of one loaded record and the plain reason for it: ``{kind, label, reason, wholes}``."""
    wholes = wholes_of(application.organization_id, [application.archimate_element_id]).get(
        application.archimate_element_id, []
    )
    names = ", ".join(name for _, name in wholes)
    if application.application_kind in KINDS:
        kind = application.application_kind
        setter = _user_name(application.application_kind_set_by_id)
        when = application.application_kind_set_at
        reason = "Set by %s on %s" % (setter or "a person", _day(when)) if when else "Set by a person"
    elif wholes:
        kind, reason = PART, "Part of %s" % names
    elif _is_repository(application):
        kind, reason = NOT_SOFTWARE, "A code repository: it holds the code of software, it is not software you run"
    else:
        kind, reason = APPLICATION, "Not part of another application and not a code repository"
    return {"kind": kind, "label": KIND_LABELS[kind], "reason": reason, "wholes": wholes}


def _is_repository(application) -> bool:
    from app.services.element_ownership_service import application_is_owned

    return bool((application.repository_type or "").strip()) or (
        (application.component_type or "").strip().lower() == "repository" and not application_is_owned(application)
    )


def _day(moment) -> str:
    return "%d %s" % (moment.day, moment.strftime("%b"))


def _user_name(user_id):
    if not user_id:
        return None
    from app.models.user import User

    user = db.session.get(User, user_id)
    if user is None:
        return None
    full = " ".join(p for p in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if p)
    return full or getattr(user, "email", None)


def set_kind(application, kind, user) -> None:
    """Record a person's choice of kind. The caller has already checked the person may change the model
    and that the record belongs to their organisation, and commits."""
    if kind not in KINDS:
        raise ValueError("kind must be one of %s" % ", ".join(KINDS))
    application.application_kind = kind
    application.application_kind_set_by_id = user.id
    application.application_kind_set_at = datetime.utcnow()
