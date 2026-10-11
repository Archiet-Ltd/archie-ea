"""Service helpers for the Organization module (org chart + enterprise RACI).

Kept in the same shape as app/modules/business_model_canvas/service.py:
thin wrappers around the ORM plus the one non-trivial piece of business
logic this module owns — building an org tree out of BusinessActor rows
linked by composition/aggregation ArchiMateRelationships.
"""

from flask import g

from app import db
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.application_owner import ApplicationOwner
from app.models.business_layer import BusinessActor, BusinessRole
from app.models.organization_model import RACI_VALUES, STAKEHOLDER_TYPES, EnterpriseRaciAssignment
from app.models.unified_capability import UnifiedCapability
from app.models.user import User

# Relationship types (case-insensitive) that represent "whole/part"
# organizational structure per ArchiMate 3.2: Composition and Aggregation.
_HIERARCHY_RELATIONSHIP_TYPES = {"composition", "aggregation"}

# BusinessActor.actor_type values that are organisational units (things a
# person can be assigned to). Excludes the person-shaped types (Individual,
# Contractor) and External Partner. Mirrors the model's own valid set
# minus those three.
ORGANISATIONAL_UNIT_ACTOR_TYPES = ("Department", "Team", "Business Unit")

_DEFAULT_UNIT_ACTOR_TYPE = "Department"


# ---------------------------------------------------------------------------
# Org chart
# ---------------------------------------------------------------------------


def _hierarchy_edges():
    """Read the organisational hierarchy edges for the current tenant.

    Returns ``(children_map, child_ids)`` exactly as ``build_org_tree``
    needs them: ``children_map`` maps parent actor id -> [child actor ids]
    and ``child_ids`` is the set of every actor that is someone's part.
    Built only from Composition/Aggregation ArchiMateRelationships where
    the source element is the whole (parent) and the target element is the
    part (child) — the standard ArchiMate composition/aggregation
    direction. Shared by the tree builder and the chart-edit routes so no
    second implementation of the hierarchy ever exists.
    """
    actors = BusinessActor.query.all()
    element_id_to_actor_id = {
        a.archimate_element_id: a.id for a in actors if a.archimate_element_id is not None
    }

    children_map = {}
    child_ids = set()

    if element_id_to_actor_id:
        element_ids = list(element_id_to_actor_id.keys())
        relationships = (
            ArchiMateRelationship.query.filter(
                ArchiMateRelationship.source_id.in_(element_ids),
                ArchiMateRelationship.target_id.in_(element_ids),
            ).all()
        )
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
            children_map.setdefault(parent_actor_id, [])
            if child_actor_id not in children_map[parent_actor_id]:
                children_map[parent_actor_id].append(child_actor_id)
            child_ids.add(child_actor_id)

    return children_map, child_ids


def _unit_subtree_actor_ids(unit_id, children_map):
    """The unit itself plus every descendant actor id.

    Iterative with a seen set: the generic relationship writer has no cycle
    rule, so a composition loop can already exist in the model and the walk
    must terminate on it.
    """
    seen = set()
    order = []
    stack = [unit_id]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        order.append(current)
        stack.extend(children_map.get(current, []))
    return order


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

    # parent_actor_id -> [child_actor_id, ...], built only from
    # Composition/Aggregation edges where the *source* element is the whole
    # (parent) and the *target* element is the part (child) — standard
    # ArchiMate composition/aggregation direction. The reading is shared
    # with the chart-edit routes via _hierarchy_edges().
    children_map, child_ids = _hierarchy_edges()
    hierarchy_edge_count = sum(len(children) for children in children_map.values())

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


# ---------------------------------------------------------------------------
# Chart editing (add/rename/move/remove units)
# ---------------------------------------------------------------------------
#
# Every action below writes through the existing actor and relationship
# writers: a new unit is a BusinessActor (whose before_insert listener
# creates the ArchiMateElement) linked by a Composition relationship created
# with ArchiMateRelationshipService.create_relationship — the writer the
# architecture module's own relationship routes use. No relationship row is
# ever inserted by hand, and no second hierarchy or mirror table is written
# (the legacy BusinessActor.parent_actor_id column is deliberately left
# alone — the chart reads relationships only).
#
# All lookups rely on the tenant query filter (g.current_org_id), so a unit
# of another organisation is never found, renamed, moved, removed or shown.


def _hierarchy_edges_from(element_id):
    """Every Composition/Aggregation relationship whose source is the
    element — i.e. the unit's own children links."""
    return (
        ArchiMateRelationship.query.filter(
            ArchiMateRelationship.source_id == element_id,
            db.func.lower(ArchiMateRelationship.type).in_(
                _HIERARCHY_RELATIONSHIP_TYPES
            ),
        ).all()
    )


def get_org_unit(unit_id):
    """The BusinessActor by id within the current tenant, else None."""
    return BusinessActor.query.filter_by(id=unit_id).first()


def _require_org_unit(unit_id):
    unit = get_org_unit(unit_id)
    if unit is None:
        raise ValueError("Unit not found")
    return unit


def add_child_unit(*, parent_id, name, actor_type=_DEFAULT_UNIT_ACTOR_TYPE):
    """Create a new unit under ``parent_id`` and link it with the existing
    relationship writer. Returns the saved BusinessActor."""
    name = (name or "").strip()
    if not name:
        raise ValueError("A unit name is required")
    actor_type = (actor_type or _DEFAULT_UNIT_ACTOR_TYPE).strip()
    if actor_type not in ORGANISATIONAL_UNIT_ACTOR_TYPES:
        raise ValueError(f"Invalid unit type: {actor_type}")

    parent = _require_org_unit(parent_id)
    if parent.archimate_element_id is None:
        raise ValueError("Parent unit has no linked element")

    unit = BusinessActor(name=name[:255], actor_type=actor_type)
    db.session.add(unit)
    db.session.flush()  # the before_insert listener creates the element

    from app.modules.architecture.services.archimate_relationship_service import (
        ArchiMateRelationshipService,
    )

    source = db.session.get(ArchiMateElement, parent.archimate_element_id)
    target = db.session.get(ArchiMateElement, unit.archimate_element_id)
    relationship = ArchiMateRelationshipService.create_relationship(
        source,
        target,
        "composition",
        architecture_id=None,
        organization_id=g.current_org_id,
        rollback=False,
    )
    if relationship is None:
        db.session.rollback()
        raise ValueError("Could not link the new unit to its parent")
    db.session.commit()
    return unit


def rename_unit(*, unit_id, name):
    """Rename a unit. Returns the saved BusinessActor."""
    name = (name or "").strip()
    if not name:
        raise ValueError("A unit name is required")
    unit = _require_org_unit(unit_id)
    unit.name = name[:255]
    db.session.commit()
    return unit


def move_unit(*, unit_id, new_parent_id):
    """Move a unit under another parent (or to the root when
    ``new_parent_id`` is None). The move is refused when it would create a
    cycle (the new parent is the unit itself or one of its descendants).
    Returns ``{"id": unit_id, "parent_id": new_parent_id}``."""
    unit = _require_org_unit(unit_id)
    if unit.archimate_element_id is None:
        raise ValueError("This unit has no linked element")

    if new_parent_id is not None:
        new_parent = _require_org_unit(new_parent_id)
        if new_parent.archimate_element_id is None:
            raise ValueError("Parent unit has no linked element")
        if new_parent_id == unit_id:
            raise ValueError("A unit cannot be its own parent")
        children_map, _child_ids = _hierarchy_edges()
        descendants = _unit_subtree_actor_ids(unit_id, children_map)
        if new_parent_id in descendants:
            raise ValueError("That move would create a cycle")

    # Detach from the current parent (if any): after this the unit is a
    # root, so re-linking (or staying detached for new_parent_id=None) can
    # never create a duplicate parent edge.
    current_parent_links = _hierarchy_links_to_unit(unit.archimate_element_id)
    for link in current_parent_links:
        db.session.delete(link)
    db.session.flush()

    if new_parent_id is not None:
        from app.modules.architecture.services.archimate_relationship_service import (
            ArchiMateRelationshipService,
        )

        source = db.session.get(ArchiMateElement, new_parent.archimate_element_id)
        target = db.session.get(ArchiMateElement, unit.archimate_element_id)
        relationship = ArchiMateRelationshipService.create_relationship(
            source,
            target,
            "composition",
            architecture_id=None,
            organization_id=g.current_org_id,
            rollback=False,
        )
        if relationship is None:
            db.session.rollback()
            raise ValueError("Could not link the unit to its new parent")

    db.session.commit()
    return {"id": unit.id, "parent_id": new_parent_id}


def _hierarchy_links_to_unit(element_id):
    """Every Composition/Aggregation relationship that makes ``element_id``
    someone's part (target-side parent links)."""
    return (
        ArchiMateRelationship.query.filter(
            ArchiMateRelationship.target_id == element_id,
            db.func.lower(ArchiMateRelationship.type).in_(
                _HIERARCHY_RELATIONSHIP_TYPES
            ),
        ).all()
    )


def remove_unit(*, unit_id):
    """Remove a unit that has no children and no people assigned to it.
    Refuses (ValueError) with a plain message when either condition holds,
    or when the unit is still referenced anywhere else in the organisation.
    """
    unit = _require_org_unit(unit_id)

    children = _hierarchy_edges_from(unit.archimate_element_id)
    if children:
        raise ValueError("This unit still has child units and cannot be removed")

    assigned_people = (
        User.query.filter(
            User.business_unit_id == unit_id,
            User.organization_id == g.current_org_id,
        ).count()
    )
    if assigned_people:
        raise ValueError("This unit still has people assigned and cannot be removed")

    parent_links = _hierarchy_links_to_unit(unit.archimate_element_id)
    parent_link_ids = {link.id for link in parent_links}
    other_count = ArchiMateRelationship.query.filter(
        db.or_(
            ArchiMateRelationship.source_id == unit.archimate_element_id,
            ArchiMateRelationship.target_id == unit.archimate_element_id,
        ),
        ArchiMateRelationship.id.notin_(parent_link_ids) if parent_link_ids else True,
    ).count()
    if other_count:
        raise ValueError(
            "This unit is still used in {} other relationship{} and cannot be removed".format(
                other_count, "" if other_count == 1 else "s"
            )
        )

    try:
        # Only the unit's parent-hierarchy link goes with it; the actor row
        # and its mirror element follow in the same transaction.
        for link in parent_links:
            db.session.delete(link)
        db.session.delete(unit)
        element = db.session.get(ArchiMateElement, unit.archimate_element_id)
        if element is not None:
            db.session.delete(element)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise ValueError(
            "This unit is still referenced elsewhere and cannot be removed"
        )


# ---------------------------------------------------------------------------
# Capabilities per unit (derived read, nothing stored)
# ---------------------------------------------------------------------------


def capabilities_for_unit(*, unit_id):
    """The capabilities owned by the people of ``unit_id`` and its
    descendants, plus the people with no unit who own capabilities.

    Derived entirely from existing records — the composition/aggregation
    tree, ``User.business_unit_id`` and the one ownership record
    (``ApplicationOwner`` rows with element_type "capability"). No new
    ownership table is written or read.

    Returns::

        {
            "unit": {"id", "name", "actor_type"},
            "capabilities": [
                {"id", "name", "code", "level",
                 "owners": [{"user_id", "name", "email",
                             "unit_id", "unit_name"}]},
            ],
            "no_unit": [
                {"user_id", "name", "email",
                 "capabilities": [{"id", "name", "code", "level"}]},
            ],
            "empty": bool,   # no capability owned by this unit's people
        }
    """
    unit = _require_org_unit(unit_id)
    org_id = g.current_org_id

    children_map, _child_ids = _hierarchy_edges()
    subtree_ids = _unit_subtree_actor_ids(unit.id, children_map)

    units = {a.id: a for a in BusinessActor.query.all()}
    org_users = {
        u.id: u for u in User.query.filter(User.organization_id == org_id).all()
    }

    owner_rows = (
        ApplicationOwner.query.filter_by(
            element_type="capability",
            organization_id=org_id,
        ).all()
    )
    capability_ids = {row.element_id for row in owner_rows}
    capabilities = {
        c.id: c
        for c in UnifiedCapability.query.filter(
            UnifiedCapability.organization_id == org_id,
            UnifiedCapability.id.in_(capability_ids) if capability_ids else False,
        ).all()
    }

    def _capability_payload(cap):
        return {
            "id": cap.id,
            "name": cap.name,
            "code": getattr(cap, "code", None),
            "level": getattr(cap, "level", 1),
        }

    def _owner_payload(user):
        unit_name = None
        if user.business_unit_id is not None and user.business_unit_id in units:
            unit_name = units[user.business_unit_id].name
        return {
            "user_id": user.id,
            "name": " ".join(p for p in (user.first_name, user.last_name) if p) or user.email,
            "email": user.email,
            "unit_id": user.business_unit_id,
            "unit_name": unit_name,
        }

    capabilities_by_id = {}
    no_unit_people = {}

    for row in owner_rows:
        cap = capabilities.get(row.element_id)
        if cap is None:
            continue
        user = org_users.get(row.user_id)
        if user is None:
            continue

        entry = capabilities_by_id.setdefault(
            cap.id,
            {"id": cap.id, "name": cap.name, "code": cap.code,
             "level": cap.level, "owners": []},
        )
        owner_payload = _owner_payload(user)
        if owner_payload not in entry["owners"]:
            entry["owners"].append(owner_payload)

        if user.business_unit_id is None:
            person = no_unit_people.setdefault(
                user.id,
                {"user_id": user.id, "name": owner_payload["name"],
                 "email": user.email, "capabilities": []},
            )
            cap_payload = _capability_payload(cap)
            if cap_payload not in person["capabilities"]:
                person["capabilities"].append(cap_payload)

    # "exactly the capabilities owned by the unit's people and descendants":
    # a capability appears in the unit's list only when at least one of its
    # owners belongs to the unit subtree.
    unit_capabilities = [
        entry
        for entry in capabilities_by_id.values()
        if any(
            owner["unit_id"] is not None and owner["unit_id"] in subtree_ids
            for owner in entry["owners"]
        )
    ]
    unit_capabilities.sort(key=lambda entry: entry["name"] or "")
    no_unit_people_list = sorted(
        no_unit_people.values(), key=lambda person: person["name"] or ""
    )

    return {
        "unit": {"id": unit.id, "name": unit.name, "actor_type": unit.actor_type},
        "capabilities": unit_capabilities,
        "no_unit": no_unit_people_list,
        "empty": not unit_capabilities,
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


def upsert_raci_cell(stakeholder_type, stakeholder_id, stakeholder_name, capability_id, raci, notes=None):
    """Create or update the RACI designation for one (stakeholder, capability)
    cell. Returns the saved EnterpriseRaciAssignment."""
    if stakeholder_type not in STAKEHOLDER_TYPES:
        raise ValueError(f"Invalid stakeholder_type: {stakeholder_type}")
    if raci not in RACI_VALUES:
        raise ValueError(f"Invalid raci value: {raci}. Must be one of {RACI_VALUES}")
    if not stakeholder_id or not capability_id:
        raise ValueError("stakeholder_id and capability_id are required")

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
