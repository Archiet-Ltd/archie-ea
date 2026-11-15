"""Service helpers for the Organization module (org chart + enterprise RACI).

Kept in the same shape as app/modules/business_model_canvas/service.py:
thin wrappers around the ORM plus the one non-trivial piece of business
logic this module owns — building an org tree out of BusinessActor rows
linked by composition/aggregation ArchiMateRelationships.
"""

from flask import g

from app import db
from app.models.archimate_core import ArchiMateRelationship
from app.models.business_layer import BusinessActor, BusinessRole
from app.models.organization_model import RACI_VALUES, STAKEHOLDER_TYPES, EnterpriseRaciAssignment
from app.models.unified_capability import UnifiedCapability
from app.models.user import User

# Relationship types (case-insensitive) that represent "whole/part"
# organizational structure per ArchiMate 3.2: Composition and Aggregation.
_HIERARCHY_RELATIONSHIP_TYPES = {"composition", "aggregation"}


# ---------------------------------------------------------------------------
# Org chart
# ---------------------------------------------------------------------------


def build_org_tree():
    """Build an org-chart forest from BusinessActor elements linked by
    Composition/Aggregation ArchiMateRelationships.

    Returns a dict:
        {
            "fallback": False,
            "roots": [ {id, name, actor_type, children: [...]}, ... ],
            "actor_count": int,
            "relationship_count": int,
        }
    or, when there are zero composition/aggregation relationships between
    actors, a flat grouped-by-actor_type fallback:
        {
            "fallback": True,
            "groups": [ {"actor_type": "Department", "actors": [...]}, ... ],
            "actor_count": int,
            "relationship_count": 0,
        }
    """
    actors = BusinessActor.query.all()
    actors_by_id = {a.id: a for a in actors}
    # Map ArchiMateElement.id -> BusinessActor.id for actors that have a
    # linked element (the before_insert listener on BusinessActor guarantees
    # this for every actor created through the ORM).
    element_id_to_actor_id = {
        a.archimate_element_id: a.id for a in actors if a.archimate_element_id is not None
    }

    relationships = []
    if element_id_to_actor_id:
        element_ids = list(element_id_to_actor_id.keys())
        relationships = (
            ArchiMateRelationship.query.filter(
                ArchiMateRelationship.source_id.in_(element_ids),
                ArchiMateRelationship.target_id.in_(element_ids),
            ).all()
        )

    # parent_actor_id -> [child_actor_id, ...], built only from
    # Composition/Aggregation edges where the *source* element is the whole
    # (parent) and the *target* element is the part (child) — standard
    # ArchiMate composition/aggregation direction.
    children_map = {}
    child_ids = set()
    hierarchy_edge_count = 0

    for rel in relationships:
        rel_type = (rel.type or "").strip().lower()
        if rel_type not in _HIERARCHY_RELATIONSHIP_TYPES:
            continue
        parent_actor_id = element_id_to_actor_id.get(rel.source_id)
        child_actor_id = element_id_to_actor_id.get(rel.target_id)
        if parent_actor_id is None or child_actor_id is None:
            continue
        if parent_actor_id == child_actor_id:
            continue
        hierarchy_edge_count += 1
        children_map.setdefault(parent_actor_id, [])
        if child_actor_id not in children_map[parent_actor_id]:
            children_map[parent_actor_id].append(child_actor_id)
        child_ids.add(child_actor_id)

    if hierarchy_edge_count == 0:
        return _build_flat_fallback(actors)

    def _node(actor_id, visited):
        actor = actors_by_id.get(actor_id)
        if actor is None:
            return None
        node = {
            "id": actor.id,
            "name": actor.name,
            "actor_type": actor.actor_type,
            "location": actor.location,
            "headcount": actor.headcount,
            "children": [],
        }
        for child_id in children_map.get(actor_id, []):
            if child_id in visited:
                # Cycle guard: never recurse into an ancestor.
                continue
            child_node = _node(child_id, visited | {child_id})
            if child_node is not None:
                node["children"].append(child_node)
        return node

    # Roots = every actor that is never the *target* of a composition/
    # aggregation edge from another actor (i.e. not anyone's "part").
    root_ids = [a.id for a in actors if a.id not in child_ids]
    roots = [_node(root_id, {root_id}) for root_id in root_ids]
    roots = [r for r in roots if r is not None]

    return {
        "fallback": False,
        "roots": roots,
        "actor_count": len(actors),
        "relationship_count": hierarchy_edge_count,
    }


def _build_flat_fallback(actors):
    """Flat grouped-by-actor_type listing used when there are zero
    composition/aggregation relationships between BusinessActors."""
    groups_by_type = {}
    for actor in actors:
        key = actor.actor_type or "Unclassified"
        groups_by_type.setdefault(key, []).append(
            {
                "id": actor.id,
                "name": actor.name,
                "actor_type": actor.actor_type,
                "location": actor.location,
                "headcount": actor.headcount,
            }
        )
    groups = [
        {"actor_type": actor_type, "actors": sorted(items, key=lambda a: a["name"] or "")}
        for actor_type, items in sorted(groups_by_type.items(), key=lambda kv: kv[0] or "")
    ]
    return {
        "fallback": True,
        "groups": groups,
        "actor_count": len(actors),
        "relationship_count": 0,
    }


# ---------------------------------------------------------------------------
# Stakeholder search (actors / roles / users)
# ---------------------------------------------------------------------------


def search_stakeholders(query, limit=10):
    """Search BusinessActor, BusinessRole and User by name/email for the
    RACI matrix's "add stakeholder" picker. Returns a flat list of
    {type, id, name, sublabel} dicts, ranked actors-then-roles-then-users."""
    q = (query or "").strip()
    results = []
    if not q:
        return results

    like = f"%{q}%"

    actor_matches = (
        BusinessActor.query.filter(BusinessActor.name.ilike(like)).order_by(BusinessActor.name).limit(limit).all()
    )
    for a in actor_matches:
        results.append({"type": "actor", "id": a.id, "name": a.name, "sublabel": a.actor_type or "Actor"})

    role_matches = (
        BusinessRole.query.filter(BusinessRole.name.ilike(like)).order_by(BusinessRole.name).limit(limit).all()
    )
    for r in role_matches:
        results.append({"type": "role", "id": r.id, "name": r.name, "sublabel": r.role_type or "Role"})

    user_matches = (
        User.query.filter(
            User.organization_id == g.current_org_id,
            db.or_(
                User.first_name.ilike(like),
                User.last_name.ilike(like),
                User.email.ilike(like),
            )
        )
        .order_by(User.email)
        .limit(limit)
        .all()
    )
    for u in user_matches:
        full_name = " ".join(part for part in (u.first_name, u.last_name) if part) or u.email
        results.append({"type": "user", "id": u.id, "name": full_name, "sublabel": u.email or "User"})

    return results[: limit * 3]


# ---------------------------------------------------------------------------
# Capabilities (RACI matrix columns)
# ---------------------------------------------------------------------------


def list_matrix_capabilities():
    """Capabilities shown as columns in the RACI matrix. Prefer top-level
    (L1) strategic capabilities; fall back to every capability if the
    tenant hasn't modeled any L1s yet."""
    capabilities = (
        UnifiedCapability.query.filter(UnifiedCapability.level == 1)
        .order_by(UnifiedCapability.name)
        .all()
    )
    if not capabilities:
        capabilities = UnifiedCapability.query.order_by(UnifiedCapability.name).limit(100).all()
    return capabilities


# ---------------------------------------------------------------------------
# RACI assignments (matrix cells)
# ---------------------------------------------------------------------------


def list_raci_assignments():
    return EnterpriseRaciAssignment.query.all()


class CapabilityNotInOrganisation(ValueError):
    """The capability named is not visible to the signed-in organisation."""


class StakeholderNotInOrganisation(ValueError):
    """The person named as a stakeholder is not one of the signed-in organisation's own people."""


def require_organisation_user(stakeholder_type, stakeholder_id):
    """A stakeholder of type ``user`` must be a member of the current organisation: the same membership
    rule the stakeholder picker searches by and the team page uses (``User.organization_id``)."""
    if stakeholder_type != "user" or not stakeholder_id:
        return
    member = User.query.filter_by(id=stakeholder_id, organization_id=g.current_org_id).first()
    if member is None:
        raise StakeholderNotInOrganisation("Person not found in your organisation")


def require_organisation_capability(capability_id):
    """The capability a cell points at must be visible to the signed-in organisation (its own, or shared
    reference data). A missing id and another organisation's id raise the same error."""
    capability = UnifiedCapability.query.filter_by(id=capability_id).first()
    if capability is None or capability.organization_id not in (None, g.current_org_id):
        raise CapabilityNotInOrganisation("Capability not found")


def upsert_raci_cell(stakeholder_type, stakeholder_id, stakeholder_name, capability_id, raci, notes=None):
    """Create or update the RACI designation for one (stakeholder, capability)
    cell. Returns the saved EnterpriseRaciAssignment."""
    if stakeholder_type not in STAKEHOLDER_TYPES:
        raise ValueError(f"Invalid stakeholder_type: {stakeholder_type}")
    if raci not in RACI_VALUES:
        raise ValueError(f"Invalid raci value: {raci}. Must be one of {RACI_VALUES}")
    if not stakeholder_id or not capability_id:
        raise ValueError("stakeholder_id and capability_id are required")
    require_organisation_user(stakeholder_type, stakeholder_id)
    require_organisation_capability(capability_id)

    assignment = EnterpriseRaciAssignment.query.filter_by(
        stakeholder_type=stakeholder_type,
        stakeholder_id=stakeholder_id,
        capability_id=capability_id,
    ).first()

    if assignment is None:
        assignment = EnterpriseRaciAssignment(
            stakeholder_type=stakeholder_type,
            stakeholder_id=stakeholder_id,
            stakeholder_name=stakeholder_name,
            capability_id=capability_id,
        )
        db.session.add(assignment)

    assignment.raci = raci
    if stakeholder_name:
        assignment.stakeholder_name = stakeholder_name
    if notes is not None:
        assignment.notes = notes

    db.session.commit()
    return assignment


def keep_raci_stakeholder(stakeholder_type, stakeholder_id, stakeholder_name):
    """Keep a stakeholder on the matrix with no cell set: a row with no capability and no
    designation, which the matrix lists as a stakeholder and never as a cell."""
    if stakeholder_type not in STAKEHOLDER_TYPES:
        raise ValueError(f"Invalid stakeholder_type: {stakeholder_type}")
    if not stakeholder_id:
        raise ValueError("stakeholder_id is required")
    require_organisation_user(stakeholder_type, stakeholder_id)
    assignment = EnterpriseRaciAssignment.query.filter_by(
        stakeholder_type=stakeholder_type, stakeholder_id=stakeholder_id
    ).first()
    if assignment is None:
        assignment = EnterpriseRaciAssignment(
            stakeholder_type=stakeholder_type,
            stakeholder_id=stakeholder_id,
            stakeholder_name=stakeholder_name,
        )
        db.session.add(assignment)
        db.session.commit()
    return assignment


def delete_raci_cell(stakeholder_type, stakeholder_id, capability_id):
    """Clear the RACI designation for one (stakeholder, capability) cell."""
    assignment = EnterpriseRaciAssignment.query.filter_by(
        stakeholder_type=stakeholder_type,
        stakeholder_id=stakeholder_id,
        capability_id=capability_id,
    ).first()
    if assignment is None:
        return False
    db.session.delete(assignment)
    db.session.commit()
    return True
