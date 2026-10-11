"""Solution ArchiMate Footprint API (SA-002), Solution Elements API (SA-001),
and Solution Relationships API (ENT-089).

Provides:
- GET /api/solutions/<id>/archimate-elements — ArchiMate element footprint grouped by layer
- POST /api/solutions/<id>/elements — link an ArchiMate element to a solution
- DELETE /api/solutions/<id>/elements/<eid> — unlink an element
- GET /api/solutions/<id>/elements — list linked elements
- GET /api/solutions/<id>/relationships — list relationships grouped by category
- POST /api/solutions/<id>/relationships — create a validated relationship
- PUT /api/solutions/<id>/relationships/<rid> — update relationship type
- DELETE /api/solutions/<id>/relationships/<rid> — delete a relationship
"""

import logging

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app import db
from app.models.solution_models import Solution
from app.utils.pagination import safe_int_arg
from app.utils.route_guards import require_entity

logger = logging.getLogger(__name__)

solution_archimate_bp = Blueprint(
    "solution_archimate", __name__, url_prefix="/api/solutions"
)


@solution_archimate_bp.route(
    "/<int:solution_id>/archimate-elements", methods=["GET"]
)
@login_required
def get_solution_archimate_elements(solution_id):
    """Return ArchiMate elements linked to a solution, grouped by layer.

    Response schema::

        {
          "solution_id": 1,
          "elements_by_layer": {
            "business": [{"id":1,"name":"...","type":"...","layer":"business",
                          "plateau":null,"building_block_type":null,
                          "element_role":"primary"}, ...]
          },
          "layer_summary": {"business": 2, "application": 1},
          "total_count": 3
        }
    """
    require_entity(Solution, solution_id, description="Solution not found")

    from app.services.solution_archimate_service import SolutionArchiMateService

    svc = SolutionArchiMateService()
    elements = svc.get_elements_for_solution(solution_id)

    grouped: dict[str, list] = {}
    for el in elements:
        layer = el.get("layer") or "unknown"
        grouped.setdefault(layer, []).append(el)

    layer_summary = {layer: len(items) for layer, items in grouped.items()}

    return jsonify(
        {
            "solution_id": solution_id,
            "elements_by_layer": grouped,
            "layer_summary": layer_summary,
            "total_count": len(elements),
        }
    )


# ---------------------------------------------------------------------------
# SA-001: /api/solutions/<id>/elements  CRUD
# ---------------------------------------------------------------------------


@solution_archimate_bp.route("/<int:solution_id>/elements", methods=["POST"])
@login_required
def add_solution_element(solution_id):
    """Link an ArchiMate element to a solution.

    Body: ``{"archimate_element_id": <int>, "layer": "<str>"}``
    Returns 201 with the new record on success, 409 if already linked.
    """
    from app.models.solution_element import SolutionElement
    from app.models.solution_models import Solution

    Solution.query.get_or_404(solution_id)

    data = request.get_json(silent=True) or {}
    archimate_element_id = data.get("archimate_element_id")
    if not archimate_element_id:
        return jsonify({"error": "archimate_element_id is required"}), 400

    layer = data.get("layer")

    existing = SolutionElement.query.filter_by(
        solution_id=solution_id, archimate_element_id=archimate_element_id
    ).first()
    if existing:
        return jsonify({"error": "Element already linked to this solution", "id": existing.id}), 409

    se = SolutionElement(
        solution_id=solution_id,
        archimate_element_id=archimate_element_id,
        layer=layer,
    )
    db.session.add(se)
    db.session.commit()

    return jsonify({
        "id": se.id,
        "solution_id": se.solution_id,
        "archimate_element_id": se.archimate_element_id,
        "layer": se.layer,
        "created_at": se.created_at.isoformat() if se.created_at else None,
    }), 201


@solution_archimate_bp.route(
    "/<int:solution_id>/elements/<int:element_id>", methods=["DELETE"]
)
@login_required
def remove_solution_element(solution_id, element_id):
    """Unlink an ArchiMate element from a solution. Returns 204."""
    from app.models.solution_element import SolutionElement

    se = SolutionElement.query.filter_by(
        solution_id=solution_id, id=element_id
    ).first_or_404()
    db.session.delete(se)
    db.session.commit()
    return "", 204


@solution_archimate_bp.route("/<int:solution_id>/elements", methods=["GET"])
@login_required
def list_solution_elements(solution_id):
    """Return linked ArchiMate elements for a solution.

    Response schema::

        [{"id":1, "archimate_element_id":2, "layer":"Business",
          "element_type":"BusinessProcess", "name":"Order Fulfilment"}, ...]
    """
    from app.models.solution_element import SolutionElement
    from app.models.solution_models import Solution

    Solution.query.get_or_404(solution_id)

    rows = SolutionElement.query.filter_by(solution_id=solution_id).all()
    result = []
    for row in rows:
        ae = row.archimate_element
        result.append({
            "id": row.id,
            "archimate_element_id": row.archimate_element_id,
            "layer": row.layer,
            "element_type": ae.type if ae else None,
            "name": ae.name if ae else None,
        })
    return jsonify(result)


# ---------------------------------------------------------------------------
# Manual element creation + linking
# ---------------------------------------------------------------------------


def _match_capability_to_catalog(solution_id: int, element_name: str) -> list:
    """CAP-027: Fuzzy-match a Capability ArchiMate element to BusinessCapability records.

    Creates a SolutionCapability bridge record with match metadata so the blueprint
    can show "Matches X.X — link it?" without manual search. Best-effort — never raises.
    Returns list of up to 3 matching BusinessCapability dicts.
    """
    try:
        from app.models.business_capabilities import BusinessCapability
        from app.models.solution_capability import SolutionCapability

        # Exact match first, then partial ILIKE, then word fragments
        name_lower = element_name.strip().lower()
        caps = BusinessCapability.query.filter(
            db.func.lower(BusinessCapability.name) == name_lower
        ).limit(1).all()

        if not caps:
            caps = BusinessCapability.query.filter(
                BusinessCapability.name.ilike(f"%{element_name}%")
            ).limit(3).all()

        if not caps:
            words = [w for w in name_lower.split() if len(w) > 4]
            for word in words:
                caps = BusinessCapability.query.filter(
                    BusinessCapability.name.ilike(f"%{word}%")
                ).limit(3).all()
                if caps:
                    break

        match_type = "novel"
        match_score = None
        closest_id = None
        if caps:
            top = caps[0]
            match_type = "exact" if top.name.strip().lower() == name_lower else "partial"
            match_score = 0.95 if match_type == "exact" else 0.70
            closest_id = top.id

        # Upsert SolutionCapability bridge record
        existing = SolutionCapability.query.filter_by(
            solution_id=solution_id, name=element_name
        ).first()
        if not existing:
            db.session.add(SolutionCapability(
                solution_id=solution_id,
                name=element_name,
                source="archimate_element",
                match_type=match_type,
                match_score=match_score,
                closest_match_id=closest_id,
            ))
            db.session.commit()

        return [
            {"id": c.id, "name": c.name, "code": c.code or ""}
            for c in caps[:3]
        ]
    except Exception as exc:
        logger.warning("CAP-027: Capability catalog match failed: %s", exc)
        db.session.rollback()
        return []


@solution_archimate_bp.route(
    "/<int:solution_id>/elements/create", methods=["POST"]
)
@login_required
def create_and_link_element(solution_id):
    """Create a new ArchiMate element and link it to the solution in one call.

    Body::

        {"name": "API Gateway", "type": "ApplicationComponent", "layer": "application",
         "description": "Central API management platform"}

    Creates the ArchiMateElement, then links via SolutionArchiMateElement.
    Returns the new element with its ID.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_models import Solution, SolutionArchiMateElement

    Solution.query.get_or_404(solution_id)
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    element_type = (data.get("type") or "").strip()
    layer = (data.get("layer") or "").strip()
    description = (data.get("description") or "").strip()

    if not name or not element_type or not layer:
        return jsonify({"error": "name, type, and layer are required"}), 400

    # Check for existing element with same name+type
    existing = ArchiMateElement.query.filter(
        db.func.lower(ArchiMateElement.name) == name.lower(),
        ArchiMateElement.type == element_type,
    ).first()

    if existing:
        ae = existing
    else:
        ae = ArchiMateElement(
            name=name, type=element_type, layer=layer.capitalize(),
            description=description,
        )
        db.session.add(ae)
        db.session.flush()

    # Link to solution
    link = SolutionArchiMateElement.query.filter_by(
        solution_id=solution_id, element_id=ae.id,
    ).first()
    if not link:
        link = SolutionArchiMateElement(
            solution_id=solution_id, element_id=ae.id,
            layer_type=layer.lower(),
            element_table="archimate_elements",
            element_name=name,
        )
        db.session.add(link)

    db.session.commit()

    # CAP-027: If a Capability element, fuzzy-match to the enterprise catalog
    # and create a SolutionCapability bridge record. Returns suggestions for
    # the "Matches X.X — link it?" inline badge.
    cap_suggestions = []
    if element_type == "Capability":
        cap_suggestions = _match_capability_to_catalog(solution_id, name)

    return jsonify({
        "success": True,
        "element": {
            "id": ae.id,
            "name": ae.name,
            "type": ae.type,
            "layer": layer.lower(),
            "description": ae.description or "",
            "element_role": "manual",
            "accepted": True,
            "isNew": True,
        },
        "capability_suggestions": cap_suggestions,
    }), 201


# ---------------------------------------------------------------------------
# Inline edit + Why chain tracing
# ---------------------------------------------------------------------------


@solution_archimate_bp.route(
    "/<int:solution_id>/elements/<int:element_id>/update", methods=["PUT"]
)
@login_required
def update_element_inline(solution_id, element_id):
    """Update an ArchiMate element's name and/or description in-place.

    Body:: ``{"name": "new name", "description": "new desc"}``
    """
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement.query.get_or_404(element_id)
    data = request.get_json(silent=True) or {}

    changed = False
    if "name" in data and data["name"].strip():
        el.name = data["name"].strip()
        changed = True
    if "description" in data:
        el.description = data["description"].strip()
        changed = True

    if changed:
        db.session.commit()

    return jsonify({
        "success": True,
        "id": el.id,
        "name": el.name,
        "description": el.description or "",
    })


@solution_archimate_bp.route(
    "/<int:solution_id>/capability-blueprint", methods=["PUT"]
)
@login_required
def save_capability_blueprint(solution_id):
    """Persist the TCM + ACM capability blueprint to the database.

    Body::

        {
          "technical_capabilities": [...],
          "application_capabilities": [...]
        }

    Stores in SolutionAIReasoningState.suggestions so it survives page refresh
    and is available to the architecture generation prompts.
    """
    from app.models.solution_models import Solution
    from app.models.solution_reasoning import SolutionAIReasoningState

    Solution.query.get_or_404(solution_id)
    data = request.get_json(silent=True) or {}

    state = SolutionAIReasoningState.query.filter_by(
        solution_id=solution_id
    ).order_by(SolutionAIReasoningState.id.desc()).first()

    if not state:
        state = SolutionAIReasoningState(
            solution_id=solution_id,
            adm_phase="B",
            suggestions={},
        )
        db.session.add(state)

    suggestions = state.suggestions or {}
    if not isinstance(suggestions, dict):
        suggestions = {}

    suggestions["technical_capabilities"] = data.get("technical_capabilities", [])
    suggestions["application_capabilities"] = data.get("application_capabilities", [])
    state.suggestions = suggestions
    # Force SQLAlchemy to detect the JSON change
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(state, "suggestions")

    db.session.commit()

    return jsonify({"success": True})


@solution_archimate_bp.route(
    "/<int:solution_id>/capability-blueprint", methods=["GET"]
)
@login_required
def load_capability_blueprint(solution_id):
    """Load the persisted TCM + ACM capability blueprint."""
    require_entity(Solution, solution_id, description="Solution not found")

    from app.models.solution_reasoning import SolutionAIReasoningState

    state = SolutionAIReasoningState.query.filter_by(
        solution_id=solution_id
    ).order_by(SolutionAIReasoningState.id.desc()).first()

    if not state or not state.suggestions:
        return jsonify({
            "technical_capabilities": [],
            "application_capabilities": [],
        })

    suggestions = state.suggestions if isinstance(state.suggestions, dict) else {}
    return jsonify({
        "technical_capabilities": suggestions.get("technical_capabilities", []),
        "application_capabilities": suggestions.get("application_capabilities", []),
    })


@solution_archimate_bp.route(
    "/<int:solution_id>/elements/<int:element_id>/trace", methods=["GET"]
)
@login_required
def trace_element_chain(solution_id, element_id):
    """Trace the reasoning chain for an element back to the motivation layer.

    Walks BACKWARDS through ArchiMateRelationship (target→source) until
    it reaches motivation-layer elements (drivers, goals, requirements).
    Returns the full chain showing WHY this element exists.
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.solution_models import SolutionArchiMateElement

    el = ArchiMateElement.query.get_or_404(element_id)

    # Get solution scope
    linked = SolutionArchiMateElement.query.filter_by(
        solution_id=solution_id
    ).all()
    solution_el_ids = {item.element_id for item in linked}

    # BFS backward: follow relationships where this element is the TARGET
    # (i.e., something realizes/serves/influences this element)
    chain = []
    visited = {element_id}
    frontier = {element_id}

    for hop in range(5):  # max 5 hops back to motivation
        if not frontier:
            break
        # Find relationships where frontier elements are targets
        rels = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.target_id.in_(frontier)
        ).all()
        next_frontier = set()
        for r in rels:
            if r.source_id not in visited and r.source_id in solution_el_ids:
                visited.add(r.source_id)
                next_frontier.add(r.source_id)
                src = db.session.get(ArchiMateElement, r.source_id)
                if src:
                    chain.append({
                        "id": src.id,
                        "name": src.name,
                        "type": src.type,
                        "layer": src.layer,
                        "relationship": r.type,
                        "hop": hop + 1,
                    })
        frontier = next_frontier

    # Build human-readable reasoning string
    reasoning_parts = []
    if chain:
        # Sort by hop (closest first) then layer priority
        layer_priority = {"motivation": 0, "strategy": 1, "business": 2, "application": 3, "technology": 4, "implementation": 5}
        chain.sort(key=lambda c: (c["hop"], layer_priority.get((c["layer"] or "").lower(), 9)))

        for link in chain:
            rel_verb = {
                "realization": "realizes",
                "RealizationRelationship": "realizes",
                "serving": "serves",
                "ServingRelationship": "serves",
                "assignment": "is assigned to",
                "composition": "is part of",
                "access": "accesses",
                "AccessRelationship": "accesses",
                "influence": "influences",
                "InfluenceRelationship": "influences",
                "association": "is associated with",
                "AssociationRelationship": "is associated with",
            }.get(link["relationship"], link["relationship"])
            reasoning_parts.append(
                f"{link['type']} \"{link['name']}\" ({link['layer']}) {rel_verb} this element"
            )

    reasoning_text = ""
    if reasoning_parts:
        reasoning_text = (
            f"This {el.type} exists because:\n" +
            "\n".join(f"  {i+1}. {p}" for i, p in enumerate(reasoning_parts[:8]))
        )
    else:
        reasoning_text = (
            f"This {el.type} was generated by the AI architecture specialist "
            f"based on your selected capabilities and motivation elements."
        )

    return jsonify({
        "element_id": element_id,
        "element_name": el.name,
        "element_type": el.type,
        "element_layer": el.layer,
        "chain": chain,
        "reasoning": reasoning_text,
        "chain_length": len(chain),
    })


# ---------------------------------------------------------------------------
# ArchiMate element search — used by blueprint picker (GET /api/solutions/archimate/search)
# ---------------------------------------------------------------------------


@solution_archimate_bp.route("/archimate/search", methods=["GET"])
@login_required
def search_archimate_elements():
    """Search ArchiMate elements by text query and optional type filter.

    Used by the blueprint Link Elements picker.

    GET /api/solutions/archimate/search?q=SAP&types=ApplicationComponent,Node&limit=25
    """
    from app.models.archimate_core import ArchiMateElement

    q = request.args.get("q", "").strip()
    types_param = request.args.get("types", "").strip()
    limit = min(safe_int_arg('limit', 25, minimum=1, maximum=500), 100)

    query = ArchiMateElement.query
    if q:
        query = query.filter(ArchiMateElement.name.ilike(f"%{q}%"))
    if types_param:
        type_list = [t.strip() for t in types_param.split(",") if t.strip()]
        if type_list:
            query = query.filter(ArchiMateElement.type.in_(type_list))

    elements = query.order_by(ArchiMateElement.name).limit(limit).all()
    return jsonify({
        "success": True,
        "data": [
            {
                "id": e.id,
                "name": e.name,
                "type": e.type,
                "layer": e.layer,
                "description": e.description or "",
            }
            for e in elements
        ],
        "count": len(elements),
    })


# ---------------------------------------------------------------------------
# Blueprint Link/Unlink — SolutionArchiMateElement junction
# These endpoints target the solution_archimate_elements table which is read
# by BlueprintCompletenessService._get_section_elements() for viewpoint rendering.
# ---------------------------------------------------------------------------


@solution_archimate_bp.route(
    "/<int:solution_id>/archimate-elements/link", methods=["POST"]
)
@login_required
def link_archimate_element(solution_id):
    """Link an existing ArchiMate element to a solution's blueprint viewpoints.

    Creates a SolutionArchiMateElement record (the table that blueprint
    completeness and viewpoint APIs read from).

    Body::

        {
          "archimate_element_id": 42,
          "element_role": "primary"  // optional, default "primary"
        }

    Returns 201 on success, 409 if already linked.
    """
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_archimate_element import SolutionArchiMateElement
    from app.models.solution_models import Solution

    Solution.query.get_or_404(solution_id)
    data = request.get_json(silent=True) or {}

    element_id = data.get("archimate_element_id")
    if not element_id:
        return jsonify({"error": "archimate_element_id is required"}), 400

    ae = ArchiMateElement.query.get(element_id)
    if not ae:
        return jsonify({"error": f"ArchiMate element {element_id} not found"}), 404

    existing = SolutionArchiMateElement.query.filter_by(
        solution_id=solution_id, element_id=element_id
    ).first()
    if existing:
        return jsonify({
            "error": "Element already linked to this solution",
            "id": existing.id,
        }), 409

    role = (data.get("element_role") or "primary").strip()
    link = SolutionArchiMateElement(
        solution_id=solution_id,
        element_id=element_id,
        element_name=ae.name,
        layer_type=(ae.layer or "").lower(),
        element_table="archimate_elements",
        element_role=role,
    )
    db.session.add(link)
    db.session.commit()

    return jsonify({
        "success": True,
        "id": link.id,
        "element": {
            "id": ae.id,
            "name": ae.name,
            "type": ae.type,
            "layer": (ae.layer or "").lower(),
            "description": ae.description or "",
            "element_role": role,
        },
    }), 201


@solution_archimate_bp.route(
    "/<int:solution_id>/archimate-elements/<int:element_id>", methods=["DELETE"]
)
@login_required
def unlink_archimate_element(solution_id, element_id):
    """Unlink an ArchiMate element from a solution's blueprint.

    ``element_id`` is the ArchiMate element's ID (not the junction row ID).
    Returns 204 on success, 404 if not linked.
    """
    from app.models.solution_archimate_element import SolutionArchiMateElement

    link = SolutionArchiMateElement.query.filter_by(
        solution_id=solution_id, element_id=element_id
    ).first_or_404()
    db.session.delete(link)
    db.session.commit()
    return "", 204


# ---------------------------------------------------------------------------
# Wave 6: Element-level decision logging + junction removal on reject
# ---------------------------------------------------------------------------


@solution_archimate_bp.route(
    "/<int:solution_id>/element-decisions", methods=["POST"]
)
@login_required
def log_element_decision(solution_id):
    """Record an Accept/Reject/Edit decision on a generated ArchiMate element.

    Body::

        {
          "element_id": 42,
          "action": "accept" | "reject" | "edit",
          "element_name": "Order Fulfilment",
          "element_type": "BusinessProcess",
          "element_layer": "business",
          "reason": "optional"
        }

    On ``reject``, the element is unlinked from the solution
    (SolutionArchiMateElement junction row deleted).

    Decisions are persisted in ``SolutionAIReasoningState`` for prompt
    augmentation in subsequent specialist calls.
    """
    from app.models.solution_models import Solution

    Solution.query.get_or_404(solution_id)

    data = request.get_json(silent=True) or {}
    element_id = data.get("element_id")
    action = data.get("action", "accept")
    element_name = data.get("element_name", "")
    element_type = data.get("element_type", "")
    element_layer = data.get("element_layer", "")
    reason = data.get("reason", "")

    # 1. Log decision in SolutionAIReasoningState
    try:
        from app.models.solution_reasoning import SolutionAIReasoningState

        state = SolutionAIReasoningState.query.filter_by(
            solution_id=solution_id
        ).order_by(SolutionAIReasoningState.id.desc()).first()

        if state:
            decisions = state.suggestions or {}
            if not isinstance(decisions, dict):
                decisions = {}
            decision_log = decisions.get("element_decisions", [])
            decision_log.append({
                "element_id": element_id,
                "element_name": element_name,
                "element_type": element_type,
                "element_layer": element_layer,
                "action": action,
                "reason": reason,
                "user_id": current_user.id if current_user else None,
            })
            decisions["element_decisions"] = decision_log
            state.suggestions = decisions
            db.session.add(state)
        else:
            # Create a new reasoning state if none exists
            state = SolutionAIReasoningState(
                solution_id=solution_id,
                adm_phase="C",
                suggestions={"element_decisions": [{
                    "element_id": element_id,
                    "element_name": element_name,
                    "element_type": element_type,
                    "element_layer": element_layer,
                    "action": action,
                    "reason": reason,
                    "user_id": current_user.id if current_user else None,
                }]},
            )
            db.session.add(state)
    except Exception as exc:
        logger.warning("Failed to log element decision: %s", exc)

    # 2. On reject, remove from junction table
    removed = False
    if action == "reject" and element_id:
        try:
            from app.models.solution_models import SolutionArchiMateElement

            link = SolutionArchiMateElement.query.filter_by(
                solution_id=solution_id, element_id=element_id,
            ).first()
            if link:
                db.session.delete(link)
                removed = True
        except Exception as exc:
            logger.warning("Failed to unlink element %s: %s", element_id, exc)

    db.session.commit()

    return jsonify({
        "success": True,
        "action": action,
        "element_id": element_id,
        "removed_from_solution": removed,
    })


# ---------------------------------------------------------------------------
# Wave 7: Snapshots + Change propagation
# ---------------------------------------------------------------------------


@solution_archimate_bp.route(
    "/<int:solution_id>/snapshots", methods=["POST"]
)
@login_required
def create_snapshot(solution_id):
    """Create a point-in-time snapshot of all ArchiMate elements and relationships.

    Body (optional)::

        {"step": 3, "label": "After architecture generation"}

    Stores a ``SolutionVersion`` with ``solution_snapshot`` containing
    all elements, relationships, and junction rows for this solution.
    """
    from app.models.solution_models import Solution
    from app.models.solution_governance import SolutionVersion

    Solution.query.get_or_404(solution_id)
    data = request.get_json(silent=True) or {}
    step = data.get("step", 0)
    label = data.get("label", f"Step {step} snapshot")

    # Build the snapshot payload
    snapshot = _build_solution_snapshot(solution_id)

    # Determine next version number
    last = (
        SolutionVersion.query
        .filter_by(solution_id=solution_id)
        .order_by(SolutionVersion.version_number.desc())
        .first()
    )
    next_ver = (last.version_number + 1) if last else 1

    sv = SolutionVersion(
        solution_id=solution_id,
        version_number=next_ver,
        change_summary=label,
        solution_snapshot=snapshot,
        created_by_id=current_user.id if current_user else None,
        approval_status="snapshot",
    )
    db.session.add(sv)
    db.session.commit()

    return jsonify({
        "success": True,
        "version_id": sv.id,
        "version_number": next_ver,
        "element_count": snapshot.get("element_count", 0),
        "relationship_count": snapshot.get("relationship_count", 0),
    }), 201


@solution_archimate_bp.route(
    "/<int:solution_id>/snapshots", methods=["GET"]
)
@login_required
def list_snapshots(solution_id):
    """List all snapshots for a solution."""
    require_entity(Solution, solution_id, description="Solution not found")

    from app.models.solution_governance import SolutionVersion

    rows = (
        SolutionVersion.query
        .filter_by(solution_id=solution_id)
        .order_by(SolutionVersion.version_number.desc())
        .all()
    )
    return jsonify([{
        "id": r.id,
        "version_number": r.version_number,
        "label": r.change_summary,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "element_count": (r.solution_snapshot or {}).get("element_count", 0),
        "relationship_count": (r.solution_snapshot or {}).get("relationship_count", 0),
    } for r in rows])


@solution_archimate_bp.route(
    "/<int:solution_id>/snapshots/<int:v1>/compare/<int:v2>", methods=["GET"]
)
@login_required
def compare_snapshots(solution_id, v1, v2):
    """Compare two snapshots and return the diff.

    Returns elements added, removed, and changed between versions.
    """
    from app.models.solution_governance import SolutionVersion

    snap_a = SolutionVersion.query.filter_by(
        solution_id=solution_id, version_number=v1
    ).first_or_404()
    snap_b = SolutionVersion.query.filter_by(
        solution_id=solution_id, version_number=v2
    ).first_or_404()

    sa = snap_a.solution_snapshot or {}
    sb = snap_b.solution_snapshot or {}

    # Build element dicts keyed by (type, name) for comparison
    def _el_key(el):
        return f"{el.get('type', '')}::{el.get('name', '')}"

    els_a = {_el_key(e): e for e in sa.get("elements", [])}
    els_b = {_el_key(e): e for e in sb.get("elements", [])}

    added = [els_b[k] for k in els_b if k not in els_a]
    removed = [els_a[k] for k in els_a if k not in els_b]

    return jsonify({
        "version_a": v1,
        "version_b": v2,
        "label_a": snap_a.change_summary,
        "label_b": snap_b.change_summary,
        "summary": {
            "elements_added": len(added),
            "elements_removed": len(removed),
            "elements_a": len(els_a),
            "elements_b": len(els_b),
        },
        "added": added,
        "removed": removed,
    })


@solution_archimate_bp.route(
    "/<int:solution_id>/quality-score", methods=["GET"]
)
@login_required
def get_quality_score(solution_id):
    """Calculate and return a quality score for the solution's ArchiMate model.

    Three components:
    - **completeness**: % of ArchiMate layers with at least 1 element (7 layers)
    - **traceability**: % of elements that have at least 1 relationship (upstream or downstream)
    - **validity**: % of relationships that pass the VALID_RELATIONSHIPS metamodel check

    Returns overall score (weighted average) and breakdown.
    """
    from app.models.solution_models import Solution

    Solution.query.get_or_404(solution_id)
    score = _calculate_quality_score(solution_id)
    return jsonify(score)


def _calculate_quality_score(solution_id):
    """Compute quality metrics for a solution's ArchiMate model."""
    from app.models.archimate_core import (
        ArchiMateElement, ArchiMateRelationship,
        VALID_RELATIONSHIPS, _ELEMENT_TYPE_LAYER,
    )
    from app.models.solution_models import SolutionArchiMateElement
    from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import (
        SolutionAIOrchestrator,
    )

    junctions = SolutionArchiMateElement.query.filter_by(
        solution_id=solution_id
    ).all()
    element_ids = [j.element_id for j in junctions]

    if not element_ids:
        return {
            "completeness": 0, "traceability": 0, "validity": 0,
            "overall": 0, "element_count": 0, "relationship_count": 0,
            "layers_covered": [], "layers_missing": [],
            "type_coverage": {},
        }

    elements = ArchiMateElement.query.filter(
        ArchiMateElement.id.in_(element_ids)
    ).all()

    rels = ArchiMateRelationship.query.filter(
        db.or_(
            ArchiMateRelationship.source_id.in_(element_ids),
            ArchiMateRelationship.target_id.in_(element_ids),
        )
    ).all()

    # --- Completeness: layers covered ---
    all_layers = {"motivation", "strategy", "business", "application", "technology", "implementation"}
    layers_present = set()
    type_counts = {}
    for e in elements:
        layer = (e.layer or "").lower()
        layers_present.add(layer)
        type_counts[e.type] = type_counts.get(e.type, 0) + 1

    layers_covered = sorted(layers_present & all_layers)
    layers_missing = sorted(all_layers - layers_present)
    completeness = round(len(layers_covered) / len(all_layers) * 100)

    # --- Traceability: elements with at least 1 relationship ---
    el_id_set = set(element_ids)
    connected_ids = set()
    for r in rels:
        if r.source_id in el_id_set:
            connected_ids.add(r.source_id)
        if r.target_id in el_id_set:
            connected_ids.add(r.target_id)

    traceability = round(len(connected_ids) / len(elements) * 100) if elements else 0

    # --- Validity: relationships passing metamodel check ---
    orch = SolutionAIOrchestrator()
    el_by_id = {e.id: e for e in elements}
    valid_count = 0
    invalid_count = 0
    invalid_details = []

    for r in rels:
        src = el_by_id.get(r.source_id)
        tgt = el_by_id.get(r.target_id)
        if not src or not tgt:
            continue

        rel_type = r.type.replace("Relationship", "").lower()
        src_snake = orch._pascal_to_snake(src.type)
        tgt_snake = orch._pascal_to_snake(tgt.type)
        src_layer = _ELEMENT_TYPE_LAYER.get(src_snake, "unknown")
        tgt_layer = _ELEMENT_TYPE_LAYER.get(tgt_snake, "unknown")

        if src_layer == "unknown" or tgt_layer == "unknown":
            valid_count += 1  # can't validate, assume valid
        elif (rel_type, src_layer, tgt_layer) in VALID_RELATIONSHIPS:
            valid_count += 1
        else:
            invalid_count += 1
            if len(invalid_details) < 5:
                invalid_details.append(
                    f"{r.type}: {src.type}({src_layer}) -> {tgt.type}({tgt_layer})"
                )

    total_checked = valid_count + invalid_count
    validity = round(valid_count / total_checked * 100) if total_checked else 100

    # --- Overall: weighted average ---
    overall = round(completeness * 0.3 + traceability * 0.3 + validity * 0.4)

    return {
        "overall": overall,
        "completeness": completeness,
        "traceability": traceability,
        "validity": validity,
        "element_count": len(elements),
        "relationship_count": len(rels),
        "layers_covered": layers_covered,
        "layers_missing": layers_missing,
        "type_coverage": type_counts,
        "valid_relationships": valid_count,
        "invalid_relationships": invalid_count,
        "invalid_details": invalid_details,
    }


@solution_archimate_bp.route(
    "/<int:solution_id>/elements/<int:element_id>/propagate-stale",
    methods=["POST"],
)
@login_required
def propagate_stale(solution_id, element_id):
    """Find all downstream dependents of an element and return their IDs.

    Traverses ``ArchiMateRelationship`` outward from the given element
    (breadth-first, max 3 hops) to identify elements that may be stale
    because this element was edited or rejected.
    """
    from app.models.archimate_core import ArchiMateRelationship
    from app.models.solution_models import SolutionArchiMateElement

    # Get all element IDs in this solution for scope filtering
    linked = SolutionArchiMateElement.query.filter_by(
        solution_id=solution_id
    ).all()
    solution_element_ids = {item.element_id for item in linked}

    if element_id not in solution_element_ids:
        return jsonify({"stale_ids": [], "message": "Element not in solution"}), 200

    # BFS — follow relationships outward from the edited element
    visited = {element_id}
    frontier = {element_id}
    stale_ids = []

    for _hop in range(3):
        if not frontier:
            break
        rels = ArchiMateRelationship.query.filter(
            ArchiMateRelationship.source_id.in_(frontier)
        ).all()
        next_frontier = set()
        for r in rels:
            if r.target_id not in visited and r.target_id in solution_element_ids:
                visited.add(r.target_id)
                next_frontier.add(r.target_id)
                stale_ids.append(r.target_id)
        frontier = next_frontier

    # Return stale element details
    stale_details = []
    if stale_ids:
        from app.models.archimate_core import ArchiMateElement
        stale_els = ArchiMateElement.query.filter(
            ArchiMateElement.id.in_(stale_ids)
        ).all()
        stale_details = [
            {"id": e.id, "name": e.name, "type": e.type, "layer": e.layer}
            for e in stale_els
        ]

    return jsonify({
        "source_element_id": element_id,
        "stale_count": len(stale_ids),
        "stale_ids": stale_ids,
        "stale_elements": stale_details,
    })


def _build_solution_snapshot(solution_id):
    """Build a JSON-serializable snapshot of all ArchiMate state for a solution."""
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.solution_models import SolutionArchiMateElement

    junctions = SolutionArchiMateElement.query.filter_by(
        solution_id=solution_id
    ).all()
    element_ids = [j.element_id for j in junctions]

    elements = ArchiMateElement.query.filter(
        ArchiMateElement.id.in_(element_ids)
    ).all() if element_ids else []

    rels = ArchiMateRelationship.query.filter(
        db.or_(
            ArchiMateRelationship.source_id.in_(element_ids),
            ArchiMateRelationship.target_id.in_(element_ids),
        )
    ).all() if element_ids else []

    return {
        "element_count": len(elements),
        "relationship_count": len(rels),
        "elements": [
            {"id": e.id, "name": e.name, "type": e.type, "layer": e.layer,
             "description": (e.description or "")[:200]}
            for e in elements
        ],
        "relationships": [
            {"id": r.id, "type": r.type, "source_id": r.source_id,
             "target_id": r.target_id}
            for r in rels
        ],
        "junctions": [
            {"element_id": j.element_id,
             "element_name": getattr(j, 'element_name', None) or ''}
            for j in junctions
        ],
    }


# ---------------------------------------------------------------------------
# Org-level capability reuse
# ---------------------------------------------------------------------------


@solution_archimate_bp.route("/capability-reuse", methods=["GET"])
@login_required
def capability_reuse_map():
    """Show which capabilities are shared across solutions.

    Returns capabilities used by 2+ solutions, with the solution names
    and element types. Enables architects to identify reuse opportunities.

    Query params:
      - min_solutions: minimum number of solutions sharing a capability (default 2)
    """
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_models import Solution, SolutionArchiMateElement
    from sqlalchemy import func

    min_sols = max(int(request.args.get("min_solutions", 2)), 2)

    # Find capabilities (ArchiMate elements of type 'Capability') linked to multiple solutions
    shared = (
        db.session.query(
            SolutionArchiMateElement.element_id,
            func.count(func.distinct(SolutionArchiMateElement.solution_id)).label("sol_count"),
        )
        .join(ArchiMateElement, ArchiMateElement.id == SolutionArchiMateElement.element_id)
        .filter(ArchiMateElement.type == "Capability")
        .group_by(SolutionArchiMateElement.element_id)
        .having(func.count(func.distinct(SolutionArchiMateElement.solution_id)) >= min_sols)
        .all()
    )

    if not shared:
        # Also check for shared element types across layers (not just capabilities)
        shared = (
            db.session.query(
                SolutionArchiMateElement.element_id,
                func.count(func.distinct(SolutionArchiMateElement.solution_id)).label("sol_count"),
            )
            .group_by(SolutionArchiMateElement.element_id)
            .having(func.count(func.distinct(SolutionArchiMateElement.solution_id)) >= min_sols)
            .order_by(func.count(func.distinct(SolutionArchiMateElement.solution_id)).desc())
            .limit(50)
            .all()
        )

    el_ids = [r[0] for r in shared]
    sol_count_map = {r[0]: r[1] for r in shared}

    elements = ArchiMateElement.query.filter(
        ArchiMateElement.id.in_(el_ids)
    ).all() if el_ids else []

    # For each shared element, find which solutions use it
    results = []
    for el in elements:
        links = SolutionArchiMateElement.query.filter_by(element_id=el.id).all()
        sol_ids = list({item.solution_id for item in links})
        solutions = Solution.query.filter(Solution.id.in_(sol_ids)).all() if sol_ids else []

        results.append({
            "element_id": el.id,
            "element_name": el.name,
            "element_type": el.type,
            "element_layer": el.layer,
            "solution_count": sol_count_map.get(el.id, 0),
            "solutions": [
                {"id": s.id, "name": s.name}
                for s in solutions
            ],
        })

    results.sort(key=lambda r: r["solution_count"], reverse=True)

    return jsonify({
        "shared_elements": results,
        "total": len(results),
        "min_solutions": min_sols,
    })


# ---------------------------------------------------------------------------
# ENT-089: /api/solutions/<id>/relationships  CRUD
# ---------------------------------------------------------------------------

# ArchiMate relationship type categories for grouping in UI
RELATIONSHIP_CATEGORIES = {
    "composition": "structural",
    "aggregation": "structural",
    "assignment": "structural",
    "realization": "structural",
    "serving": "dependency",
    "access": "dependency",
    "influence": "dependency",
    "triggering": "dynamic",
    "flow": "dynamic",
    "specialization": "other",
    "association": "other",
}

VALID_RELATIONSHIP_TYPES = set(RELATIONSHIP_CATEGORIES.keys())


def _get_solution_element_ids(solution_id):
    """Return set of archimate_element_ids linked to this solution."""
    from app.models.solution_element import SolutionElement

    rows = (
        db.session.query(SolutionElement.archimate_element_id)
        .filter_by(solution_id=solution_id)
        .all()
    )
    return {r[0] for r in rows}


@solution_archimate_bp.route("/<int:solution_id>/relationships", methods=["GET"])
@login_required
def list_solution_relationships(solution_id):
    """List ArchiMate relationships scoped to a solution, grouped by category.

    Only returns relationships where BOTH source and target are linked to the
    solution via SolutionElement. Supports optional query parameter ``type``
    to filter by relationship type (e.g. ``?type=access``).

    Response schema::

        {
          "solution_id": 1,
          "relationships": [
            {"id":1, "type":"serving", "category":"dependency",
             "source":{"id":10,"name":"Web Frontend","type":"ApplicationComponent","layer":"application"},
             "target":{"id":20,"name":"Place Order","type":"ApplicationService","layer":"application"}}
          ],
          "by_category": {"structural":[...], "dependency":[...], "dynamic":[...], "other":[...]},
          "total_count": 5
        }
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.solution_models import Solution

    Solution.query.get_or_404(solution_id)

    sol_element_ids = _get_solution_element_ids(solution_id)
    if not sol_element_ids:
        return jsonify({
            "solution_id": solution_id,
            "relationships": [],
            "by_category": {"structural": [], "dependency": [], "dynamic": [], "other": []},
            "total_count": 0,
        })

    query = (
        db.session.query(ArchiMateRelationship)
        .filter(
            ArchiMateRelationship.source_id.in_(sol_element_ids),
            ArchiMateRelationship.target_id.in_(sol_element_ids),
        )
    )

    type_filter = request.args.get("type")
    if type_filter and type_filter in VALID_RELATIONSHIP_TYPES:
        query = query.filter(ArchiMateRelationship.type == type_filter)

    rels = query.all()

    # Build element lookup for names/types
    all_ids = set()
    for r in rels:
        all_ids.add(r.source_id)
        all_ids.add(r.target_id)

    elements_map = {}
    if all_ids:
        for el in ArchiMateElement.query.filter(ArchiMateElement.id.in_(all_ids)).all():
            elements_map[el.id] = {
                "id": el.id,
                "name": el.name,
                "type": el.type,
                "layer": el.layer,
            }

    result = []
    by_category = {"structural": [], "dependency": [], "dynamic": [], "other": []}

    for r in rels:
        cat = RELATIONSHIP_CATEGORIES.get((r.type or "").lower(), "other")
        entry = {
            "id": r.id,
            "type": r.type,
            "category": cat,
            "source": elements_map.get(r.source_id, {"id": r.source_id}),
            "target": elements_map.get(r.target_id, {"id": r.target_id}),
        }
        result.append(entry)
        by_category[cat].append(entry)

    return jsonify({
        "solution_id": solution_id,
        "relationships": result,
        "by_category": by_category,
        "total_count": len(result),
    })


@solution_archimate_bp.route("/<int:solution_id>/relationships", methods=["POST"])
@login_required
def create_solution_relationship(solution_id):
    """Create a validated ArchiMate relationship between two solution elements.

    Body::

        {
          "source_element_id": 10,
          "target_element_id": 20,
          "relationship_type": "serving"
        }

    Validates against ArchiMate 3.2 matrix before persisting.
    Returns 201 on success, 400 on validation failure.
    """
    from app.models.archimate_core import (
        ArchiMateElement,
        ArchiMateRelationship,
        validate_relationship,
    )
    from app.models.solution_models import Solution

    Solution.query.get_or_404(solution_id)

    data = request.get_json(silent=True) or {}
    source_id = data.get("source_element_id")
    target_id = data.get("target_element_id")
    rel_type = data.get("relationship_type", "").lower().strip()

    if not source_id or not target_id or not rel_type:
        return jsonify({"error": "source_element_id, target_element_id, and relationship_type are required"}), 400

    if rel_type not in VALID_RELATIONSHIP_TYPES:
        return jsonify({
            "error": f"Invalid relationship_type '{rel_type}'. Must be one of: {sorted(VALID_RELATIONSHIP_TYPES)}"
        }), 400

    # Verify both elements exist
    source = ArchiMateElement.query.get(source_id)
    target = ArchiMateElement.query.get(target_id)
    if not source:
        return jsonify({"error": f"Source element {source_id} not found"}), 404
    if not target:
        return jsonify({"error": f"Target element {target_id} not found"}), 404

    # Verify both elements are linked to this solution
    sol_element_ids = _get_solution_element_ids(solution_id)
    if source_id not in sol_element_ids:
        return jsonify({"error": f"Source element {source_id} is not linked to solution {solution_id}"}), 400
    if target_id not in sol_element_ids:
        return jsonify({"error": f"Target element {target_id} is not linked to solution {solution_id}"}), 400

    # Validate against ArchiMate 3.2 matrix
    is_valid, message = validate_relationship(rel_type, source.type, target.type)
    if not is_valid:
        return jsonify({"error": message, "validation": "failed"}), 400

    # Check for duplicate
    existing = ArchiMateRelationship.query.filter_by(
        source_id=source_id, target_id=target_id, type=rel_type
    ).first()
    if existing:
        return jsonify({"error": "Relationship already exists", "id": existing.id}), 409

    rel = ArchiMateRelationship(
        source_id=source_id,
        target_id=target_id,
        type=rel_type,
    )
    db.session.add(rel)
    db.session.commit()

    return jsonify({
        "id": rel.id,
        "type": rel.type,
        "category": RELATIONSHIP_CATEGORIES.get(rel_type, "other"),
        "source_element_id": source_id,
        "target_element_id": target_id,
        "validation": message,
    }), 201


@solution_archimate_bp.route(
    "/<int:solution_id>/relationships/<int:relationship_id>", methods=["PUT"]
)
@login_required
def update_solution_relationship(solution_id, relationship_id):
    """Update the type of an existing relationship with re-validation.

    Body: ``{"relationship_type": "realization"}``
    Returns 200 on success, 400 on validation failure.
    """
    from app.models.archimate_core import (
        ArchiMateElement,
        ArchiMateRelationship,
        validate_relationship,
    )
    from app.models.solution_models import Solution

    Solution.query.get_or_404(solution_id)

    rel = ArchiMateRelationship.query.get_or_404(relationship_id)

    # Verify this relationship belongs to the solution scope
    sol_element_ids = _get_solution_element_ids(solution_id)
    if rel.source_id not in sol_element_ids or rel.target_id not in sol_element_ids:
        return jsonify({"error": "Relationship not scoped to this solution"}), 404

    data = request.get_json(silent=True) or {}
    new_type = data.get("relationship_type", "").lower().strip()

    if not new_type:
        return jsonify({"error": "relationship_type is required"}), 400

    if new_type not in VALID_RELATIONSHIP_TYPES:
        return jsonify({
            "error": f"Invalid relationship_type '{new_type}'. Must be one of: {sorted(VALID_RELATIONSHIP_TYPES)}"
        }), 400

    source = ArchiMateElement.query.get(rel.source_id)
    target = ArchiMateElement.query.get(rel.target_id)

    is_valid, message = validate_relationship(new_type, source.type if source else "", target.type if target else "")
    if not is_valid:
        return jsonify({"error": message, "validation": "failed"}), 400

    rel.type = new_type
    db.session.commit()

    return jsonify({
        "id": rel.id,
        "type": rel.type,
        "category": RELATIONSHIP_CATEGORIES.get(new_type, "other"),
        "source_element_id": rel.source_id,
        "target_element_id": rel.target_id,
        "validation": message,
    })


@solution_archimate_bp.route(
    "/<int:solution_id>/relationships/<int:relationship_id>", methods=["DELETE"]
)
@login_required
def delete_solution_relationship(solution_id, relationship_id):
    """Delete a relationship scoped to this solution. Returns 204."""
    from app.models.archimate_core import ArchiMateRelationship
    from app.models.solution_models import Solution

    Solution.query.get_or_404(solution_id)

    rel = ArchiMateRelationship.query.get_or_404(relationship_id)

    # Verify this relationship belongs to the solution scope
    sol_element_ids = _get_solution_element_ids(solution_id)
    if rel.source_id not in sol_element_ids or rel.target_id not in sol_element_ids:
        return jsonify({"error": "Relationship not scoped to this solution"}), 404

    db.session.delete(rel)
    db.session.commit()
    return "", 204


@solution_archimate_bp.route(
    "/<int:solution_id>/relationships/generate", methods=["POST"]
)
@login_required
def generate_relationships(solution_id):
    """Generate AI relationship proposals for a solution (ENT-093)."""
    from app.models.solution_models import Solution
    from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import (
        SolutionAIOrchestrator,
    )

    solution = Solution.query.get(solution_id)
    if not solution:
        return jsonify({"error": "Solution not found"}), 404

    orchestrator = SolutionAIOrchestrator()
    result = orchestrator.generate_solution_relationships(
        solution_id=solution_id,
        user_id=current_user.id,
    )
    return jsonify(result), 200


@solution_archimate_bp.route(
    "/<int:solution_id>/relationships/extract", methods=["POST"]
)
@login_required
def extract_relationships(solution_id):
    """Extract relationship proposals from natural language (ENT-095).

    Body: ``{"message": "The web frontend calls the order service..."}``
    Returns proposals in same format as generate_relationships, plus unresolved list.
    """
    from app.models.solution_models import Solution

    Solution.query.get_or_404(solution_id)

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    return jsonify({
        "error": "Relationship extraction from natural language is not yet available. Use the generate endpoint instead.",
        "proposals": [],
    }), 400


@solution_archimate_bp.route(
    "/<int:solution_id>/relationships/traceability", methods=["GET"]
)
@login_required
def get_traceability_view(solution_id):
    """Return elements grouped by ArchiMate layer with inter-layer relationships.

    Response schema::

        {
          "solution_id": 1,
          "layers": {
            "motivation": [{"id":1,"name":"...","type":"..."}],
            "strategy": [...],
            "business": [...],
            "application": [...],
            "technology": [...]
          },
          "relationships": [
            {"id":1,"source_id":10,"target_id":20,"type":"realization"}
          ],
          "layer_order": ["motivation","strategy","business","application","technology"]
        }
    """
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.solution_models import Solution

    Solution.query.get_or_404(solution_id)

    sol_element_ids = _get_solution_element_ids(solution_id)
    if not sol_element_ids:
        return jsonify({
            "solution_id": solution_id,
            "layers": {},
            "relationships": [],
            "layer_order": ["motivation", "strategy", "business", "application", "technology"],
        })

    elements = ArchiMateElement.query.filter(
        ArchiMateElement.id.in_(sol_element_ids)
    ).all()

    # Group by layer
    layer_order = ["motivation", "strategy", "business", "application", "technology"]
    layers = {item: [] for item in layer_order}

    for el in elements:
        layer = (el.layer or "application").lower()
        if layer not in layers:
            layers[layer] = []
        layers[layer].append({
            "id": el.id,
            "name": el.name,
            "type": el.type,
            "layer": layer,
        })

    # Remove empty layers
    layers = {k: v for k, v in layers.items() if v}

    # Get relationships between solution elements
    rels = (
        db.session.query(ArchiMateRelationship)
        .filter(
            ArchiMateRelationship.source_id.in_(sol_element_ids),
            ArchiMateRelationship.target_id.in_(sol_element_ids),
        )
        .all()
    )

    relationships = [
        {
            "id": r.id,
            "source_id": r.source_id,
            "target_id": r.target_id,
            "type": r.type,
        }
        for r in rels
    ]

    return jsonify({
        "solution_id": solution_id,
        "layers": layers,
        "relationships": relationships,
        "layer_order": [item for item in layer_order if item in layers],
    })
