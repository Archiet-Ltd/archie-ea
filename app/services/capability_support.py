"""The one place "this application supports this capability" is written and read.

The fact is one ArchiMate relationship, from the application's element to the capability's
element, of type Realization or Serving. ``UnifiedApplicationCapabilityMapping`` is its
organisation-fenced projection: one row per relationship, holding what the relationship cannot
(support level, coverage, maturity, strategic flag, notes). Rows are made, changed and removed only
here. Every function takes the organisation and filters on it explicitly, so it is correct inside a
request, in the chat's worker thread and in a command alike.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import event as _sa_event

from app import db
from app.services.application_model_links import normalised_type

logger = logging.getLogger(__name__)

# One vocabulary for how well an application supports a capability.
SUPPORT_LEVELS = ("primary", "secondary", "partial", "legacy", "planned", "linked")
_LEVEL_ALIASES = {"full": "primary"}

# Both kinds count as support, Realization first: it wins when a pair has both.
SUPPORT_KINDS = ("realization", "serving")
KIND_WORDS = {"realization": "Realizes", "serving": "Serves"}

_FACT_FIELDS = (
    "support_level", "coverage_percentage", "maturity_level", "is_strategic", "notes",
)


SAVE_FAILED = "The link could not be saved, so nothing was changed."


class CapabilitySupportRefused(Exception):
    """A support change that cannot be made. The message is a sentence a person can read."""


@dataclass
class SupportResult:
    created: bool
    relationship_id: Optional[int]
    row_id: Optional[int]
    application_name: str
    capability_name: str
    kind: str


def normalise_level(level: Optional[str]) -> Optional[str]:
    if level is None or str(level).strip() == "":
        return None
    value = _LEVEL_ALIASES.get(str(level).strip().lower(), str(level).strip().lower())
    if value not in SUPPORT_LEVELS:
        raise CapabilitySupportRefused(
            "Support level must be one of: " + ", ".join(SUPPORT_LEVELS) + "."
        )
    return value


def _clean_facts(facts: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    for key, value in (facts or {}).items():
        if key not in _FACT_FIELDS:
            continue
        if key == "support_level":
            value = normalise_level(value)
            if value is None:
                continue
        elif key in ("coverage_percentage", "maturity_level"):
            if value in (None, ""):
                clean[key] = None
                continue
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise CapabilitySupportRefused("Coverage and maturity must be whole numbers.")
            low, high = (0, 100) if key == "coverage_percentage" else (1, 5)
            if not low <= value <= high:
                raise CapabilitySupportRefused(
                    f"{'Coverage' if low == 0 else 'Maturity'} must be between {low} and {high}."
                )
        elif key == "is_strategic":
            value = bool(value)
        elif key == "notes":
            value = (str(value).strip() or None) if value is not None else None
        clean[key] = value
    return clean


def _is_support_type(rel_type: Optional[str]) -> bool:
    return (rel_type or "").strip().lower() in SUPPORT_KINDS


def _models():
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping as Row,
    )
    from app.models.unified_capability import UnifiedCapability

    return ApplicationComponent, ArchiMateElement, ArchiMateRelationship, Row, UnifiedCapability


# ---------------------------------------------------------------------------
# Identity: capability records and application rows
# ---------------------------------------------------------------------------
def capability_for_element(organization_id: int, element, *, flush: bool = True):
    """The capability record for a Capability element, made through the one creator when absent."""
    from app.modules.capabilities.services.capability_service import ensure_capability_record

    _, _, _, _, UnifiedCapability = _models()
    if element is None or element.organization_id != organization_id:
        raise CapabilitySupportRefused("That capability is not in this workspace.")
    found = (
        db.session.query(UnifiedCapability)
        .filter(
            UnifiedCapability.organization_id == organization_id,
            UnifiedCapability.archimate_element_id == element.id,
            UnifiedCapability.retired_into_id.is_(None),
        )
        .order_by(UnifiedCapability.id)
        .first()
    )
    if found is not None:
        return found
    from app.models.business_capabilities import BusinessCapability

    business = (
        db.session.query(BusinessCapability)
        .filter(
            BusinessCapability.organization_id == organization_id,
            BusinessCapability.archimate_element_id == element.id,
        )
        .first()
    )
    if business is not None:
        projected = (
            db.session.query(UnifiedCapability)
            .filter(
                UnifiedCapability.organization_id == organization_id,
                UnifiedCapability.source_table == "business_capability",
                UnifiedCapability.source_id == str(business.id),
                UnifiedCapability.retired_into_id.is_(None),
            )
            .first()
        )
        if projected is not None:
            return projected
    record, _ = ensure_capability_record(
        name=element.name,
        organization_id=organization_id,
        archimate_element_id=element.id,
        flush=flush,
    )
    return record


def _application_row(organization_id: int, element, *, flush: bool = True):
    from app.services.connector_framework import ensure_application_row

    return ensure_application_row(organization_id, element, element.name, flush=flush)


# ---------------------------------------------------------------------------
# The projector
# ---------------------------------------------------------------------------
def _project_relationship(session, rel) -> None:
    """Give a current support relationship its one row; take the row from any other."""
    ApplicationComponent, Element, Relationship, Row, _ = _models()
    existing = session.query(Row).filter(Row.archimate_relationship_id == rel.id).first()
    current = _is_support_type(rel.type) and rel.superseded_at is None
    source = target = None
    if current:
        ends = {
            e.id: e
            for e in session.query(Element).filter(
                Element.id.in_((rel.source_id, rel.target_id)),
                Element.organization_id == rel.organization_id,
            )
        }
        source, target = ends.get(rel.source_id), ends.get(rel.target_id)
        current = (
            source is not None
            and target is not None
            and normalised_type(source.type) == "applicationcomponent"
            and normalised_type(target.type) == "capability"
        )
        if not current and rel.source_id and rel.target_id:
            both = session.query(Element.organization_id).filter(
                Element.id.in_((rel.source_id, rel.target_id))
            ).all()
            if len({o for (o,) in both}) > 1:
                logger.warning(
                    "support relationship %s joins two organisations and was not projected", rel.id
                )
    if not current:
        if existing is not None:
            session.delete(existing)
        return
    if existing is not None:
        return
    capability = capability_for_element(rel.organization_id, target, flush=False)
    application = _application_row(rel.organization_id, source, flush=False)
    if capability.id is not None and application.id is not None:
        # A row written before the relationship existed (a path that has not moved to this module)
        # is the pair's row: take it over rather than making a second.
        orphan = (
            session.query(Row)
            .filter(
                Row.organization_id == rel.organization_id,
                Row.unified_capability_id == capability.id,
                Row.application_component_id == application.id,
                Row.archimate_relationship_id.is_(None),
            )
            .order_by(Row.id)
            .first()
        )
        if orphan is not None:
            orphan.archimate_relationship_id = rel.id
            return
    row = Row(
        organization_id=rel.organization_id,
        archimate_relationship_id=rel.id,
        unified_capability=capability,
        application=application,
        support_level="primary" if (rel.type or "").lower() == "realization" else "linked",
        is_active=True,
    )
    session.add(row)


def _collect(session, flush_context):
    _, _, Relationship, _, _ = _models()
    seen = session.info.setdefault("support_pending", {})
    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, Relationship):
            seen[id(obj)] = obj


def _project(session, flush_context):
    pending = session.info.pop("support_pending", None)
    if not pending:
        return
    with session.no_autoflush:
        for rel in pending.values():
            if rel.id is None or rel in session.deleted:
                continue
            try:
                _project_relationship(session, rel)
            except CapabilitySupportRefused:
                logger.warning("support relationship %s could not be projected", rel.id)


def _forget(session, *args):
    session.info.pop("support_pending", None)


def register_capability_support_listeners() -> None:
    """Attach the projector to ``db.session``. Idempotent; called once at app start."""
    if not _sa_event.contains(db.session, "after_flush", _collect):
        _sa_event.listen(db.session, "after_flush", _collect)
    if not _sa_event.contains(db.session, "after_flush_postexec", _project):
        _sa_event.listen(db.session, "after_flush_postexec", _project)
    if not _sa_event.contains(db.session, "after_soft_rollback", _forget):
        _sa_event.listen(db.session, "after_soft_rollback", _forget)


def project_relationships(session, relationships: Iterable) -> None:
    """Project given relationships now, for callers outside a flush (a command, a test)."""
    with session.no_autoflush:
        for rel in relationships:
            _project_relationship(session, rel)
    session.flush()


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------
def _load_ends(organization_id: int, application_element_id: int, capability_element_id: int):
    _, Element, _, _, _ = _models()
    ends = {
        e.id: e
        for e in db.session.query(Element).filter(
            Element.id.in_((application_element_id, capability_element_id)),
            Element.organization_id == organization_id,
        )
    }
    app_el, cap_el = ends.get(application_element_id), ends.get(capability_element_id)
    if app_el is None or cap_el is None:
        raise CapabilitySupportRefused("That application or capability is not in this workspace.")
    if normalised_type(app_el.type) != "applicationcomponent":
        raise CapabilitySupportRefused(f"{app_el.name} is not an application.")
    if normalised_type(cap_el.type) != "capability":
        raise CapabilitySupportRefused(f"{cap_el.name} is not a capability.")
    return app_el, cap_el


def _support_relationships(organization_id: int, app_el_id: int, cap_el_id: int) -> List:
    _, _, Relationship, _, _ = _models()
    return (
        db.session.query(Relationship)
        .filter(
            Relationship.organization_id == organization_id,
            Relationship.source_id == app_el_id,
            Relationship.target_id == cap_el_id,
            db.func.lower(Relationship.type).in_(SUPPORT_KINDS),
            Relationship.superseded_at.is_(None),
        )
        .order_by(Relationship.id)
        .all()
    )


def record_capability_support(*args, **kwargs) -> SupportResult:
    """Record that an application supports a capability, once, and return what was recorded.

    Any failure that is not a plain refusal is logged and told as one sentence, with nothing
    changed, so database text never reaches the person."""
    try:
        with db.session.begin_nested():
            return _record_capability_support(*args, **kwargs)
    except CapabilitySupportRefused:
        raise
    except Exception:
        logger.exception("could not record capability support")
        raise CapabilitySupportRefused(SAVE_FAILED)


def _record_capability_support(
    organization_id: int,
    application_element_id: int,
    capability_element_id: int,
    kind: str = "realization",
    facts: Optional[Dict[str, Any]] = None,
    actor_id: Optional[int] = None,
) -> SupportResult:
    from app.models.archimate_relationship_sync import _ensure_relationship
    from app.services.archimate_validity_service import ArchimateValidityService

    _, _, _, Row, _ = _models()
    kind = (kind or "realization").strip().lower()
    kind = {"realizes": "realization", "serves": "serving"}.get(kind, kind)
    if kind not in SUPPORT_KINDS:
        raise CapabilitySupportRefused("Support is recorded as Realizes or Serves.")
    clean = _clean_facts(facts)
    app_el, cap_el = _load_ends(organization_id, application_element_id, capability_element_id)
    if not ArchimateValidityService().is_valid(app_el.type or "", cap_el.type or "", kind):
        allowed = ", ".join(KIND_WORDS[k] for k in SUPPORT_KINDS if ArchimateValidityService().is_valid(
            app_el.type or "", cap_el.type or "", k)) or "no kind"
        raise CapabilitySupportRefused(
            f"{KIND_WORDS[kind]} is not allowed from {app_el.name} to {cap_el.name}. Allowed: {allowed}."
        )
    existing = _support_relationships(organization_id, app_el.id, cap_el.id)
    created = not existing
    if existing:
        rel = next((r for r in existing if (r.type or "").lower() == kind), existing[0])
        if (rel.type or "").lower() != kind:
            rel.type = kind
    else:
        try:
            rel = _ensure_relationship(
                db.session, kind, app_el.id, cap_el.id, expected_organization_id=organization_id
            )
        except ValueError:
            raise CapabilitySupportRefused("That application or capability is not in this workspace.")
        if rel is None:
            raise CapabilitySupportRefused(SAVE_FAILED)
        if actor_id and getattr(rel, "created_by_id", None) is None:
            rel.created_by_id = actor_id
    db.session.flush()
    row = db.session.query(Row).filter(
        Row.organization_id == organization_id, Row.archimate_relationship_id == rel.id
    ).first()
    if row is None:
        project_relationships(db.session, [rel])
        row = db.session.query(Row).filter(
            Row.organization_id == organization_id, Row.archimate_relationship_id == rel.id
        ).first()
    if row is None:
        raise CapabilitySupportRefused(SAVE_FAILED)
    _apply_facts(row, clean)
    row.is_active = True
    db.session.flush()
    return SupportResult(created, rel.id, row.id, app_el.name, cap_el.name, kind)


def _apply_facts(row, clean: Dict[str, Any]) -> None:
    """Write each fact that was given, by name. The names are the fixed list in ``_FACT_FIELDS``."""
    if "support_level" in clean:
        row.support_level = clean["support_level"]
    if "coverage_percentage" in clean:
        row.coverage_percentage = clean["coverage_percentage"]
    if "maturity_level" in clean:
        row.maturity_level = clean["maturity_level"]
    if "is_strategic" in clean:
        row.is_strategic = clean["is_strategic"]
    if "notes" in clean:
        row.notes = clean["notes"]


def _row_in_org(organization_id: int, row_id: int):
    _, _, _, Row, _ = _models()
    row = db.session.query(Row).filter(
        Row.id == row_id, Row.organization_id == organization_id
    ).first()
    if row is None:
        raise CapabilitySupportRefused("That support is not in this workspace.")
    return row


def update_support_facts(organization_id: int, row_id: int, **facts):
    row = _row_in_org(organization_id, row_id)
    _apply_facts(row, _clean_facts(facts))
    db.session.flush()
    return row


def remove_capability_support(organization_id: int, row_id: int) -> None:
    """Delete the relationship; the row goes with it."""
    _, _, Relationship, _, _ = _models()
    row = _row_in_org(organization_id, row_id)
    rel = None
    if row.archimate_relationship_id:
        rel = db.session.query(Relationship).filter(
            Relationship.id == row.archimate_relationship_id,
            Relationship.organization_id == organization_id,
        ).first()
    if rel is not None:
        db.session.delete(rel)
    db.session.delete(row)
    db.session.flush()


def remove_support_for_application(organization_id: int, application_id: int) -> int:
    _, _, _, Row, _ = _models()
    ids = [
        r.id for r in db.session.query(Row.id).filter(
            Row.organization_id == organization_id, Row.application_component_id == application_id
        )
    ]
    for row_id in ids:
        remove_capability_support(organization_id, row_id)
    return len(ids)


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------
def support_rows(
    organization_id: int,
    application_ids: Optional[Iterable[int]] = None,
    capability_ids: Optional[Iterable[int]] = None,
) -> List[Dict[str, Any]]:
    """One row per supported pair, the kind read from the relationship. Realizes beats Serves."""
    ApplicationComponent, Element, Relationship, Row, UnifiedCapability = _models()
    query = (
        db.session.query(Row, Relationship, ApplicationComponent, UnifiedCapability)
        .join(Relationship, Relationship.id == Row.archimate_relationship_id)
        .join(ApplicationComponent, ApplicationComponent.id == Row.application_component_id)
        .join(UnifiedCapability, UnifiedCapability.id == Row.unified_capability_id)
        .filter(
            Row.organization_id == organization_id,
            Relationship.organization_id == organization_id,
            ApplicationComponent.organization_id == organization_id,
            UnifiedCapability.organization_id == organization_id,
            UnifiedCapability.retired_into_id.is_(None),
            Row.is_active.isnot(False),
            Relationship.superseded_at.is_(None),
            db.func.lower(Relationship.type).in_(SUPPORT_KINDS),
        )
    )
    if application_ids is not None:
        query = query.filter(Row.application_component_id.in_(list(application_ids)))
    if capability_ids is not None:
        query = query.filter(Row.unified_capability_id.in_(list(capability_ids)))
    best: Dict[tuple, Dict[str, Any]] = {}
    for row, rel, app, cap in query.order_by(Row.id):
        kind = (rel.type or "").lower()
        key = (app.id, cap.id)
        if key in best and (SUPPORT_KINDS.index(best[key]["kind"]) <= SUPPORT_KINDS.index(kind)):
            continue
        best[key] = {
            "row_id": row.id,
            "application_id": app.id,
            "application_name": app.name,
            "capability_id": cap.id,
            "capability_name": cap.name,
            "kind": kind,
            "kind_word": KIND_WORDS[kind],
            "support_level": row.support_level,
            "coverage_percentage": row.coverage_percentage,
            "maturity_level": row.maturity_level,
            "is_strategic": bool(row.is_strategic),
            "notes": row.notes,
            "relationship_id": rel.id,
            "application_element_id": rel.source_id,
            "capability_element_id": rel.target_id,
        }
    return sorted(best.values(), key=lambda r: (r["capability_id"], r["application_id"]))


def capabilities_of_application(organization_id: int, application_id: int) -> List[Dict[str, Any]]:
    return sorted(
        support_rows(organization_id, application_ids=[application_id]),
        key=lambda r: (r["capability_name"] or "").lower(),
    )


def applications_of_capability(organization_id: int, capability_id: int) -> List[Dict[str, Any]]:
    return sorted(
        support_rows(organization_id, capability_ids=[capability_id]),
        key=lambda r: (r["application_name"] or "").lower(),
    )


def unsupported_capabilities(organization_id: int) -> List:
    _, _, _, _, UnifiedCapability = _models()
    supported = {r["capability_id"] for r in support_rows(organization_id)}
    caps = (
        db.session.query(UnifiedCapability)
        .filter(
            UnifiedCapability.organization_id == organization_id,
            UnifiedCapability.retired_into_id.is_(None),
        )
        .order_by(UnifiedCapability.name)
        .all()
    )
    return [c for c in caps if c.id not in supported]


def support_summary(organization_id: int) -> Dict[str, Any]:
    ApplicationComponent, _, _, _, UnifiedCapability = _models()
    rows = support_rows(organization_id)
    capabilities = (
        db.session.query(db.func.count(UnifiedCapability.id))
        .filter(
            UnifiedCapability.organization_id == organization_id,
            UnifiedCapability.retired_into_id.is_(None),
        )
        .scalar()
    )
    applications = (
        db.session.query(db.func.count(ApplicationComponent.id))
        .filter(ApplicationComponent.organization_id == organization_id)
        .scalar()
    )
    return {
        "supports": len(rows),
        "capabilities": int(capabilities or 0),
        "applications": int(applications or 0),
        "generated_at": datetime.now(timezone.utc),
    }


def format_generated_at(moment: datetime) -> str:
    """"14 Nov 2026, 11:20" for the line under the coverage matrix."""
    return f"{moment.day} {moment:%b %Y}, {moment:%H:%M}"


# ---------------------------------------------------------------------------
# Names to elements (the chat)
# ---------------------------------------------------------------------------
def _elements_named(organization_id: int, name: str, element_type: str) -> List:
    from app.utils.duplicate_guard import folded_column, normalize_name

    _, Element, _, _, _ = _models()
    key = normalize_name(str(name or "").translate(str.maketrans("-_", "  ")))
    if not key:
        return []
    rows = (
        db.session.query(Element)
        .filter(
            Element.organization_id == organization_id,
            Element.deleted_at.is_(None),
            folded_column(db.func.translate(Element.name, "-_", "  ")) == key,
        )
        .order_by(Element.id)
        .all()
    )
    return [e for e in rows if normalised_type(e.type) == element_type]


def _page_of(element) -> str:
    from app.models.application_portfolio import ApplicationComponent

    if normalised_type(element.type) == "applicationcomponent":
        row = (
            db.session.query(ApplicationComponent.id)
            .filter(
                ApplicationComponent.organization_id == element.organization_id,
                ApplicationComponent.archimate_element_id == element.id,
            )
            .first()
        )
        if row:
            return f"/applications/{row[0]}"
    from app.modules.architecture.routes.archimate_crud.routes import _element_href

    return _element_href("strategy", "Capability", element.id) or ""


def _one_of(organization_id: int, name: str, element_type: str, noun: str):
    found = _elements_named(organization_id, name, element_type)
    if not found:
        raise CapabilitySupportRefused(f"No {noun} named '{name}' exists in this workspace.")
    if len(found) > 1:
        listed = "; ".join(f"{e.name} ({_page_of(e)})" for e in found)
        raise CapabilitySupportRefused(
            f"More than one {noun} is called '{name}': {listed}. Say which one you mean."
        )
    return found[0]


def resolve_support_ends(organization_id: int, application_name: str, capability_name: str):
    """The one application element and the one capability element two names mean, in this
    organisation. A name that matches nothing, or matches two, is refused in words; a write is
    never resolved by a silent pick."""
    application = _one_of(organization_id, application_name, "applicationcomponent", "application")
    capability = _one_of(organization_id, capability_name, "capability", "capability")
    return application, capability


def application_page(organization_id: int, application_element) -> str:
    return _page_of(application_element)


# ---------------------------------------------------------------------------
# Counts the store-agreement check compares
# ---------------------------------------------------------------------------
def count_support_relationships(organization_id: int) -> int:
    """Supported pairs as the model holds them: distinct application-to-capability pairs joined by a
    current Realization or Serving relationship."""
    _, Element, Relationship, _, _ = _models()
    source, target = db.aliased(Element), db.aliased(Element)
    rows = (
        db.session.query(Relationship.source_id, Relationship.target_id)
        .join(source, source.id == Relationship.source_id)
        .join(target, target.id == Relationship.target_id)
        .filter(
            Relationship.organization_id == organization_id,
            source.organization_id == organization_id,
            target.organization_id == organization_id,
            Relationship.superseded_at.is_(None),
            db.func.lower(Relationship.type).in_(SUPPORT_KINDS),
            db.func.lower(db.func.replace(db.func.replace(source.type, "_", ""), " ", "")) == "applicationcomponent",
            db.func.lower(target.type) == "capability",
        )
        .distinct()
        .all()
    )
    return len(rows)


def count_support_rows(organization_id: int) -> int:
    """Supported pairs as the reader returns them."""
    return len(support_rows(organization_id))


def count_listed_on_application_pages(organization_id: int) -> int:
    """The capabilities each application page lists, added up over the applications."""
    from app.services.application_capabilities import application_capability_links

    ApplicationComponent = _models()[0]
    ids = [i for (i,) in db.session.query(ApplicationComponent.id).filter(
        ApplicationComponent.organization_id == organization_id)]
    return sum(len(application_capability_links(i, organization_id)) for i in ids)
