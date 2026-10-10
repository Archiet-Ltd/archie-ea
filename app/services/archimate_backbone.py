"""The one place a motivation entity becomes part of the ArchiMate model.

CLAUDE.md states the rule as the product's spine rather than a convention:

    "ArchiMate is the backbone, not a view. Every backend CREATE for a
     motivation entity (Driver, Goal, Constraint, Requirement, Risk, Metric,
     Plateau, WorkPackage) must call _sync_archimate_element() so a matching
     ArchiMateElement row exists ... A plain textarea is not an acceptable
     substitute -- the field IS the element."

Before 31 Aug 2026 that rule could not be followed, for two reasons the
`archimate-backbone` gate's count of 53 did not distinguish between.

1. There was no single `_sync_archimate_element`. THREE were defined --
   solution_phase_routes.py, solution_ai_orchestrator.py and
   strategic_service.py -- with different signatures and different behaviour.
   "Call the helper" named an ambiguity, not a function.

2. Six of the thirteen motivation models had NOWHERE TO STORE THE LINK. Risk,
   SolutionDriver, SolutionGoal, SolutionConstraint and SolutionRisk had no
   archimate column at all, and SolutionRequirement's `archimate_requirement_id`
   points at the enterprise `requirements` table rather than at an element. So
   roughly half the flagged call sites could not have complied at any price.

Both are fixed: those models now carry a nullable `archimate_element_id`, and
this module is the single implementation. The consequence of the old state was
never cosmetic -- app/services/archimate_backbone_audit.py records that the sync
"swallows failures and returns None, and no call site inspects the return", so
traceability, impact analysis and line of sight "would simply return a quieter
answer than the truth".

This helper therefore does NOT swallow. A caller that cannot produce a valid
element is a bug, and a bug that raises is worth more than a backbone with holes
in it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# The domain -> ArchiMate mapping from DESIGN.md. Solution* variants are the
# journey layer's own motivation entities and map to the same element types.
ELEMENT_TYPES: Dict[str, tuple] = {
    "Driver": ("Driver", "Motivation"),
    "Goal": ("Goal", "Motivation"),
    "Constraint": ("Constraint", "Motivation"),
    "Requirement": ("Requirement", "Motivation"),
    "Risk": ("Assessment", "Motivation"),
    "Metric": ("Assessment", "Motivation"),
    "Plateau": ("Plateau", "Implementation"),
    "WorkPackage": ("WorkPackage", "Implementation"),
    "Gap": ("Gap", "Implementation"),
    "UnifiedWorkPackage": ("WorkPackage", "Implementation"),
    "SolutionDriver": ("Driver", "Motivation"),
    "SolutionGoal": ("Goal", "Motivation"),
    "SolutionConstraint": ("Constraint", "Motivation"),
    "SolutionRequirement": ("Requirement", "Motivation"),
    "SolutionRisk": ("Assessment", "Motivation"),
    "ApplicationComplianceControl": ("BusinessObject", "Business"),
}

# Where each model keeps its display name. Checked in order.
NAME_FIELDS = ("name", "title", "risk_name", "goal_name", "driver_name",
               "constraint_name", "requirement_text", "description", "control_name")

# ArchiMateElement.name is String(100).
MAX_NAME = 100


def _first_attr(obj: Any, fields) -> Optional[str]:
    for field in fields:
        value = getattr(obj, field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _resolve_org_id(obj: Any) -> Optional[int]:
    """The element belongs to the same tenant as the row it mirrors.

    Driver, Goal and Requirement are not TenantMixin models and carry no
    organization_id, so they fall back to the request's tenant. Outside a
    request there is none, and TenantMixin will not fill one in either -- see
    CLAUDE.md on unfiltered CLI/scheduler paths.
    """
    org_id = getattr(obj, "organization_id", None)
    if org_id:
        return org_id
    try:
        from flask import g

        return getattr(g, "current_org_id", None)
    except Exception:
        return None


def create_backbone_element(*, element_type, layer, name, description=None, organization_id, session=None, provenance=None, flush=True):
    """Create an ArchiMate element for the backbone.

    Returns the ArchiMateElement. Raises ValueError when name is blank/missing,
    not a string, or when organization_id is missing (None or 0). Postgres
    `serial`/`SERIAL` primary keys in this codebase start at 1, so 0 is never
    a real organization id -- it is rejected explicitly here rather than being
    passed through to a foreign-key constraint failure later.

    When ``flush=True`` (default) the element is flushed immediately so its
    ``id`` is available.  Pass ``flush=False`` when called from inside a
    session flush listener (e.g. ``before_flush``) where a second flush would
    raise ``InvalidRequestError``; the caller must then set
    ``archimate_element_id`` after the flush completes.
    """
    from app import db
    from app.models.archimate_core import ArchiMateElement

    if not isinstance(name, str) or not name.strip():
        raise ValueError(
            "Cannot create ArchiMate element: name is required"
        )

    if organization_id in (None, 0):
        raise ValueError(
            "Cannot create ArchiMate element: organization_id is required"
        )

    session = session or db.session

    properties = dict(provenance or {})
    properties.setdefault("source_model", element_type)
    stored_name = name.strip() if len(name.strip()) <= MAX_NAME else name.strip()[: MAX_NAME - 1] + "\u2026"
    if stored_name != name.strip():
        properties["source_name"] = name.strip()

    element = ArchiMateElement(
        organization_id=organization_id,
        name=stored_name,
        type=element_type,
        layer=layer,
        description=description,
        scope="enterprise",
        custom_properties=properties,
    )
    session.add(element)
    if flush:
        session.flush()
    return element


def sync_archimate_element(obj: Any, *, session=None, provenance: Optional[Dict] = None, flush=True):
    """Create and attach the ArchiMate mirror of a motivation row.

    Idempotent: a row that already carries archimate_element_id is left alone,
    so this is safe to call on an update path or twice on the same object.

    Returns the ArchiMateElement, or None when the object's type is not a
    motivation entity. Raises ValueError when it IS one but cannot be
    represented -- silently skipping is what produced a backbone with holes.

    When ``flush=True`` (default) the element is flushed immediately.  Pass
    ``flush=False`` when called from inside a session flush listener.
    """
    from app import db

    session = session or db.session
    type_name = type(obj).__name__
    mapping = ELEMENT_TYPES.get(type_name)
    if mapping is None:
        return None

    if getattr(obj, "archimate_element_id", None):
        return None  # already on the backbone

    # Re-entry guard: prevent the before_flush hook from creating a second
    # element when create_backbone_element flushes while this object is
    # still in session.new.
    obj_id = id(obj)
    if obj_id in _SYNCING:
        return None
    _SYNCING.add(obj_id)

    try:
        element_type, layer = mapping
        name = _first_attr(obj, NAME_FIELDS)
        if not name:
            raise ValueError(
                "%s has no name/title to mirror into the ArchiMate model; the "
                "element IS the field, so an unnamed motivation row cannot join "
                "the backbone" % type_name
            )

        org_id = _resolve_org_id(obj)
        if org_id is None:
            raise ValueError(
                "%s(%r) has no organization to attach its ArchiMate element to. "
                "Inside a request this comes from the row or g.current_org_id; in "
                "a CLI/scheduler path it must be passed explicitly."
                % (type_name, name[:60])
            )

        properties = dict(provenance or {})
        properties["source_model"] = type_name

        element = create_backbone_element(
            element_type=element_type,
            layer=layer,
            name=name,
            description=getattr(obj, "description", None),
            organization_id=org_id,
            session=session,
            provenance=properties,
            flush=flush,
        )
        obj.archimate_element_id = element.id
        return element
    finally:
        _SYNCING.discard(obj_id)


# ============================================================================
# Session-level flush listeners — the one place new records join the backbone.
# Registered once at app startup via register_archimate_listeners().
#
# A before_flush/after_flush pair is needed because sync_archimate_element
# calls session.flush() (via create_backbone_element), which cannot be done
# inside a flush listener.  In before_flush we create the element without
# flushing (flush=False); the current flush persists both the source row and
# the element.  In after_flush the element has an id, so we set
# archimate_element_id on the source row.
# ============================================================================

from sqlalchemy import event as _sa_event  # noqa: E402 — late import safe here

# Per-session storage for the before_flush -> after_flush handoff.
# Maps session_id -> {source_obj: element}
_PENDING: dict = {}

# Objects currently being processed by sync_archimate_element.
# Prevents the before_flush hook from creating a second element
# when sync_archimate_element flushes (via create_backbone_element) while
# the source row is still in session.new.
_SYNCING = set()  # type: ignore[var-annotated]

# Types that the flush listener auto-creates elements for.  Every other type
# in ELEMENT_TYPES (Goal, Driver, Requirement, Constraint, Metric, Plateau,
# Gap, Solution* variants) behaves exactly as on main — no automatic element
# creation during flush.
FLUSH_LISTENER_ALLOW_LIST = frozenset({
    "WorkPackage",
    "UnifiedWorkPackage",
    "Risk",
    "ApplicationComplianceControl",
})


def _pending_map(session):
    sid = id(session)
    if sid not in _PENDING:
        _PENDING[sid] = {}
    return _PENDING[sid]


def _on_before_flush(session, flush_context, instances):
    """Create ArchiMate elements for newly inserted domain records.

    Runs before each flush.  Iterates ``session.new`` and calls
    ``sync_archimate_element(..., flush=False)`` for every object whose type
    is listed in ``ELEMENT_TYPES`` and that does not already carry an
    ``archimate_element_id``.

    The element is added to the session without flushing; the current flush
    will persist it.  The mapping is stored for ``_on_after_flush`` to set
    ``archimate_element_id`` after the flush completes.
    """
    pending = _pending_map(session)
    for obj in list(session.new):
        type_name = type(obj).__name__
        if type_name not in FLUSH_LISTENER_ALLOW_LIST:
            continue
        if getattr(obj, "archimate_element_id", None):
            continue
        # Skip objects without an organisation — they cannot own an element.
        org_id = getattr(obj, "organization_id", None)
        if org_id is None:
            continue
        # Skip objects that sync_archimate_element is already handling.
        if id(obj) in _SYNCING:
            continue
        element = sync_archimate_element(obj, session=session, flush=False)
        if element is not None:
            pending[id(obj)] = (obj, element)


def _on_after_flush(session, flush_context):
    """Set archimate_element_id on source rows after the flush completes.

    The element was created in ``_on_before_flush`` without flushing; the
    flush that just completed persisted it, so ``element.id`` is now
    available.  We set ``archimate_element_id`` on the source row using the
    flush connection so the update is part of the same transaction.
    """
    pending = _pending_map(session)
    if not pending:
        return
    conn = session.connection()
    for obj_id, (obj, element) in list(pending.items()):
        if getattr(obj, "archimate_element_id", None) is None:
            table = obj.__table__
            pk_col = next(
                c for c in table.c if c.primary_key
            )
            conn.execute(
                table.update().where(pk_col == obj.id).values(
                    archimate_element_id=element.id
                )
            )
            obj.archimate_element_id = element.id
        del pending[obj_id]


def register_archimate_listeners():
    """Attach the before_flush/after_flush listeners to ``db.session``.

    Call once at app startup (e.g. from ``init_services``).  Idempotent:
    guards against double-registration.
    """
    from app.extensions import db

    if not _sa_event.contains(db.session, "before_flush", _on_before_flush):
        _sa_event.listen(db.session, "before_flush", _on_before_flush)
    if not _sa_event.contains(db.session, "after_flush", _on_after_flush):
        _sa_event.listen(db.session, "after_flush", _on_after_flush)
    if not _sa_event.contains(db.session, "after_rollback", _on_after_rollback):
        _sa_event.listen(db.session, "after_rollback", _on_after_rollback)


def _on_after_rollback(session):
    """Clear the pending map on rollback so stale entries are never reused.

    A flush that fails leaves entries in _PENDING for a session object that
    may be reused; discarding them here prevents stale state.
    """
    sid = id(session)
    _PENDING.pop(sid, None)
    # Also clear _SYNCING entries that might have been left behind.
    _SYNCING.clear()
