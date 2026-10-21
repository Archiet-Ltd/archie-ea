"""A compliance control in the model: one mapping function and the links to it.

A control in the reference library (``ComplianceControl``) has one model-side twin per
organisation: an ArchiMate Requirement element, or a Constraint element when the control
is a prohibition. ``control_to_element`` is the only function that makes it, through the
one element writer (``archimate_backbone``), and the control's id is a property of the
element.

Elements that protect or satisfy the control are bound to it by an ordinary realisation
relationship from the element to the Requirement. That is the whole binding: no column
beside the relationship model, and any core element (application, technology, process,
capability) can be bound, not only an application. The relationship records where it
came from in ``connection_spec["origin"]``.

The older application-only rows (``ApplicationComplianceControl``) stay readable. They
are folded, never deleted: ``fold_legacy_mapping`` records a realisation link for each
and names the old row on it, so the two stores agree and the old evidence still counts.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from app import db
from app.models import ArchiMateElement, ArchiMateRelationship
from app.models.archimate_relationship_sync import _ensure_relationship
from app.models.compliance_models import ComplianceControl
from app.services.archimate_backbone import create_backbone_element

logger = logging.getLogger(__name__)

REQUIREMENT_TYPE = "Requirement"
CONSTRAINT_TYPE = "Constraint"
MOTIVATION_LAYER = "Motivation"
REALIZATION = "realization"

ORIGIN_LINK = "compliance_link"
ORIGIN_FOLD = "folded_application_compliance_control"

# The control id travels on the element under this property name.
CONTROL_PROPERTY = "compliance_control_id"

_PROHIBITION = re.compile(
    r"\b(shall not|must not|may not|is prohibited|are prohibited|prohibit(?:s|ed)?|"
    r"not permitted|forbid(?:s|den)?)\b",
    re.IGNORECASE,
)

REALISED_LIMIT = 200


def is_prohibition(control: ComplianceControl) -> bool:
    """True when the control forbids something rather than asks for something."""
    return bool(_PROHIBITION.search(f"{control.title or ''} {control.description or ''}"))


def _existing_element(control_id: int, organization_id: int) -> Optional[ArchiMateElement]:
    return db.session.execute(
        db.select(ArchiMateElement)
        .where(ArchiMateElement.organization_id == organization_id)
        .where(ArchiMateElement.type.in_((REQUIREMENT_TYPE, CONSTRAINT_TYPE)))
        .where(ArchiMateElement.custom_properties[CONTROL_PROPERTY].as_string() == str(control_id))
        .order_by(ArchiMateElement.id)
    ).scalars().first()


def control_to_element(control: ComplianceControl, organization_id: int) -> ArchiMateElement:
    """The organisation's Requirement (or Constraint) element for *control*: found if it
    exists, made once if it does not. The only mapping from a control to the model."""
    existing = _existing_element(control.id, organization_id)
    if existing is not None:
        return existing

    framework = control.framework
    framework_code = framework.code if framework is not None else None
    element_type = CONSTRAINT_TYPE if is_prohibition(control) else REQUIREMENT_TYPE
    label = f"{framework_code} {control.control_code}" if framework_code else control.control_code
    return create_backbone_element(
        element_type=element_type,
        layer=MOTIVATION_LAYER,
        name=f"{label}: {control.title}",
        description=control.description or control.title,
        organization_id=organization_id,
        provenance={
            CONTROL_PROPERTY: control.id,
            "control_code": control.control_code,
            "framework_code": framework_code,
            "framework_version": framework.standard_version if framework is not None else None,
            "source": "compliance_library",
        },
    )


def _existing_link(element_id: int, requirement_id: int, organization_id: int):
    return db.session.execute(
        db.select(ArchiMateRelationship)
        .where(ArchiMateRelationship.source_id == element_id)
        .where(ArchiMateRelationship.target_id == requirement_id)
        .where(db.func.lower(ArchiMateRelationship.type) == REALIZATION)
        .where(ArchiMateRelationship.organization_id == organization_id)
        .order_by(ArchiMateRelationship.id)
    ).scalars().first()


def link_control_to_element(
    control_id: int,
    element_id: int,
    organization_id: int,
    *,
    origin: str = ORIGIN_LINK,
    spec: Optional[Dict[str, Any]] = None,
) -> ArchiMateRelationship:
    """Bind an element to a control: the element realises the control's Requirement.

    Idempotent. Raises ``ValueError`` when the control does not exist or the element is
    not the organisation's, so a foreign element id reads as a missing one.
    """
    control = db.session.get(ComplianceControl, control_id)
    if control is None:
        raise ValueError(f"Control {control_id} not found")
    element = db.session.execute(
        db.select(ArchiMateElement)
        .where(ArchiMateElement.id == element_id)
        .where(ArchiMateElement.organization_id == organization_id)
    ).scalar_one_or_none()
    if element is None:
        raise ValueError(f"Element {element_id} not found")

    requirement = control_to_element(control, organization_id)
    if requirement.id == element.id:
        raise ValueError("A control's own element cannot realise itself")

    relationship = _existing_link(element.id, requirement.id, organization_id)
    if relationship is None:
        relationship = _ensure_relationship(
            db.session, REALIZATION, element.id, requirement.id,
            expected_organization_id=organization_id,
        )
    existing_spec = dict(relationship.connection_spec or {})
    if "origin" not in existing_spec:
        relationship.connection_spec = {**existing_spec, "origin": origin, **(spec or {})}
    db.session.flush()
    return relationship


def fold_legacy_mapping(mapping) -> bool:
    """Record the realisation link for an old application-only row and name the row on it.

    The old row is left as it is. Returns True when the fold is newly recorded, False when
    it already was or the row cannot be folded (its application has no model element)."""
    from app.models.application_portfolio import ApplicationComponent

    if mapping.application_id is None or mapping.organization_id is None:
        return False
    component = db.session.get(ApplicationComponent, mapping.application_id)
    if component is None or component.archimate_element_id is None:
        return False

    relationship = link_control_to_element(
        mapping.control_id, component.archimate_element_id, mapping.organization_id,
        origin=ORIGIN_FOLD,
    )
    spec = dict(relationship.connection_spec or {})
    if spec.get("legacy_mapping_id") is not None:
        # Already recorded: this row, or an earlier row for the same link, which is kept.
        return False
    relationship.connection_spec = {**spec, "legacy_mapping_id": mapping.id}
    db.session.flush()
    return True


def fold_legacy_mappings(
    organization_id: Optional[int] = None, skipped: Optional[List[Dict[str, Any]]] = None
) -> int:
    """Fold every old application-only row (one organisation's, or all). Returns how many
    folds were newly recorded; running it again records none. A row that cannot be folded is
    skipped, the rest carry on, and each skipped row is appended to *skipped* (when given) as
    ``{"mapping_id", "reason"}``."""
    from app.models.application_compliance import ApplicationComplianceControl

    query = db.select(ApplicationComplianceControl).where(
        ApplicationComplianceControl.application_id.isnot(None)
    )
    if organization_id is not None:
        query = query.where(ApplicationComplianceControl.organization_id == organization_id)
    folded = 0
    mappings = db.session.execute(query.order_by(ApplicationComplianceControl.id)).scalars().all()
    for mapping in mappings:
        mapping_id = mapping.id
        try:
            with db.session.begin_nested():
                if fold_legacy_mapping(mapping):
                    folded += 1
        except Exception as exc:  # one bad row must not stop the rest
            logger.warning("control row %s not folded: %s", mapping_id, exc)
            if skipped is not None:
                skipped.append({"mapping_id": mapping_id, "reason": str(exc) or type(exc).__name__})
    return folded


def controls_realised_by(element_id: int, organization_id: int) -> List[Dict[str, Any]]:
    """The controls an element is bound to: its realisation links to the organisation's
    Requirement and Constraint elements that carry a control id. Tenant predicates are on
    the relationship and on the element reached."""
    rows = db.session.execute(
        db.select(
            ArchiMateRelationship.id, ArchiMateElement.id, ArchiMateElement.custom_properties,
            ArchiMateRelationship.connection_spec,
        )
        .join(ArchiMateElement, ArchiMateElement.id == ArchiMateRelationship.target_id)
        .where(ArchiMateRelationship.source_id == element_id)
        .where(db.func.lower(ArchiMateRelationship.type) == REALIZATION)
        .where(ArchiMateRelationship.organization_id == organization_id)
        .where(ArchiMateElement.organization_id == organization_id)
        .where(db.func.lower(ArchiMateElement.type).in_((REQUIREMENT_TYPE.lower(), CONSTRAINT_TYPE.lower())))
        .order_by(ArchiMateRelationship.id)
        .limit(REALISED_LIMIT)
    ).all()
    found = []
    for relationship_id, requirement_id, properties, spec in rows:
        control_id = (properties or {}).get(CONTROL_PROPERTY)
        if isinstance(control_id, int):
            found.append({
                "control_id": control_id,
                "relationship_id": relationship_id,
                "requirement_element_id": requirement_id,
                "origin": (spec or {}).get("origin"),
            })
    return found
