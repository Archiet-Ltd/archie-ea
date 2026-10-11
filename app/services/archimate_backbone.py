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
}

# Where each model keeps its display name. Checked in order.
NAME_FIELDS = ("name", "title", "risk_name", "goal_name", "driver_name",
               "constraint_name", "requirement_text", "description")

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


def create_backbone_element(*, element_type, layer, name, description=None, organization_id, session=None, provenance=None):
    """Create an ArchiMate element for the backbone.

    Returns the ArchiMateElement. Raises ValueError when name is blank/missing,
    not a string, or when organization_id is missing (None or 0). Postgres
    `serial`/`SERIAL` primary keys in this codebase start at 1, so 0 is never
    a real organization id -- it is rejected explicitly here rather than being
    passed through to a foreign-key constraint failure later.
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
    session.flush()
    return element


def sync_archimate_element(obj: Any, *, session=None, provenance: Optional[Dict] = None):
    """Create and attach the ArchiMate mirror of a motivation row.

    Idempotent: a row that already carries archimate_element_id is left alone,
    so this is safe to call on an update path or twice on the same object.

    Returns the ArchiMateElement, or None when the object's type is not a
    motivation entity. Raises ValueError when it IS one but cannot be
    represented -- silently skipping is what produced a backbone with holes.
    """
    from app import db

    session = session or db.session
    type_name = type(obj).__name__
    mapping = ELEMENT_TYPES.get(type_name)
    if mapping is None:
        return None

    if getattr(obj, "archimate_element_id", None):
        return None  # already on the backbone

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
        provenance=properties
    )
    obj.archimate_element_id = element.id
    return element
