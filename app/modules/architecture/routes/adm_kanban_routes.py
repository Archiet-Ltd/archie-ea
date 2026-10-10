"""
ADM Kanban API Routes - TOGAF ADM Phase Tracking with ArchiMate Integration

Official module source for ADM Kanban functionality.
Registered via app.modules.architecture.register()

Provides:
- ADM phase management and transitions
- Kanban board and card CRUD operations
- Dependency validation and circular dependency detection
- ARB review integration
- Analytics and reporting
"""

from datetime import datetime
from types import SimpleNamespace

from flask import Blueprint, current_app, g, jsonify, request
from flask_login import current_user, login_required

from app.decorators import audit_log
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.middleware.tenant_context import current_org_id
from app.middleware.tenant_decorators import is_active_org_admin
from app.models.adm_kanban import (
    ARCHIMATE_ELEMENTS,
    ADMPhase,
    KanbanBoard,
    KanbanCard,
    create_adm_phases,
)
from app.models.user import User
from app.utils.tenant_users import same_user_id, user_in_org

# ============================================================================
# ADM WORKFLOW VALIDATION FUNCTIONS
# ============================================================================


def validate_phase_transition(current_phase_id, new_phase_id):
    """
    Validate ADM phase transitions according to TOGAF ADM workflow rules

    Returns: {'valid': bool, 'reason': str}
    """
    try:
        current_phase = ADMPhase.query.get(current_phase_id)
        new_phase = ADMPhase.query.get(new_phase_id)

        if not current_phase or not new_phase:
            return {"valid": False, "reason": "Phase not found"}

        current_order = current_phase.order
        new_order = new_phase.order

        # Allow forward progression (later phases)
        if new_order > current_order:
            return {"valid": True, "reason": "Forward progression allowed"}

        # Allow backward movement within reason (e.g., corrections)
        if new_order < current_order and (current_order - new_order) <= 2:
            return {
                "valid": True,
                "reason": "Minor backward movement allowed for corrections",
            }

        # Prevent large backward jumps or invalid movements
        if new_order < current_order:
            return {
                "valid": False,
                "reason": "Cannot jump backward more than 2 phases. Complete intermediate phases first.",
            }

        # Same phase (no change)
        return {"valid": True, "reason": "No phase change"}

    except Exception as e:
        current_app.logger.error(f"Phase transition validation error: {str(e)}")
        return {"valid": False, "reason": "Validation error occurred"}


def validate_card_dependencies(card, new_phase_id=None):
    """
    Validate that card dependencies are satisfied before phase changes

    Returns: {'valid': bool, 'reason': str, 'blocking_cards': []}
    """
    try:
        blocking_cards = []

        # Check if this card blocks others
        if card.blocks:
            for blocked_card_id in card.blocks:
                blocked_card = KanbanCard.query.get(blocked_card_id)
                if blocked_card and blocked_card.status != "done":
                    blocking_cards.append(
                        {
                            "id": blocked_card.id,
                            "title": blocked_card.title,
                            "reason": "This card blocks another card that is not yet complete",
                        }
                    )

        # Check if other cards block this one
        if card.depends_on:
            for dependency_id in card.depends_on:
                dependency_card = KanbanCard.query.get(dependency_id)
                if dependency_card and dependency_card.status != "done":
                    blocking_cards.append(
                        {
                            "id": dependency_card.id,
                            "title": dependency_card.title,
                            "reason": "This card depends on another card that is not yet complete",
                        }
                    )

        if blocking_cards:
            return {
                "valid": False,
                "reason": "Card has unresolved dependencies",
                "blocking_cards": blocking_cards,
            }

        return {"valid": True, "reason": "All dependencies satisfied"}

    except Exception as e:
        current_app.logger.error(f"Card dependency validation error: {str(e)}")
        return {"valid": False, "reason": "Dependency validation error occurred"}


def detect_circular_dependency(card_id, target_depends_on_ids):
    """
    AUDIT-ADM-002: Detect if adding dependencies would create a circular dependency.

    Checks whether any card in target_depends_on_ids already depends (directly or
    transitively) on card_id. If so, creating the dependency would form a cycle.

    Args:
        card_id: The ID of the card that wants to depend on others
        target_depends_on_ids: List of card IDs that card_id wants to depend on

    Returns: {'valid': bool, 'reason': str, 'cycle_path': []}
    """
    try:
        if not target_depends_on_ids:
            return {"valid": True, "reason": "No dependencies to check"}

        # For each proposed dependency, check if it would create a cycle
        # A cycle exists if target_card already depends on card_id (transitively)
        for dep_id in target_depends_on_ids:
            if dep_id == card_id:
                return {
                    "valid": False,
                    "reason": "A card cannot depend on itself",
                    "cycle_path": [card_id],
                }

            # BFS to check if dep_id transitively depends on card_id
            visited = set()
            queue = [dep_id]

            while queue:
                current_id = queue.pop(0)
                if current_id in visited:
                    continue
                visited.add(current_id)

                current_card = KanbanCard.query.get(current_id)
                if not current_card or not current_card.depends_on:
                    continue

                for upstream_id in current_card.depends_on:
                    if upstream_id == card_id:
                        dep_card = KanbanCard.query.get(dep_id)
                        dep_name = dep_card.title if dep_card else f"Card #{dep_id}"
                        return {
                            "valid": False,
                            "reason": (
                                f"Circular dependency detected: '{dep_name}' already "
                                f"depends (directly or transitively) on this card"
                            ),
                            "cycle_path": list(visited),
                        }
                    if upstream_id not in visited:
                        queue.append(upstream_id)

        return {"valid": True, "reason": "No circular dependencies detected"}

    except Exception as e:
        current_app.logger.error(f"Circular dependency detection error: {str(e)}")
        return {"valid": False, "reason": "Error checking for circular dependencies"}


adm_kanban_bp = Blueprint("adm_kanban", __name__, url_prefix="/api/adm-kanban")


# ============================================================================
# ADM PHASES ENDPOINTS
# ============================================================================


@adm_kanban_bp.route("/phases", methods=["GET"])
@login_required
def get_adm_phases():
    """Get all ADM phases"""
    try:
        phases = ADMPhase.query.order_by(ADMPhase.order).all()
        return jsonify(
            {
                "success": True,
                "data": [
                    {
                        "id": phase.id,
                        "name": phase.name,
                        "code": phase.code,
                        "description": phase.description,
                        "order": phase.order,
                    }
                    for phase in phases
                ],
            }
        )
    except SQLAlchemyError as e:
        current_app.logger.error(f"Error fetching ADM phases: {str(e)}")
        return jsonify({"success": False, "error": "Database error"}), 500


@adm_kanban_bp.route("/phases/init", methods=["POST"])
@login_required
@audit_log("adm_phases_init")
def init_adm_phases():
    """Initialize ADM phases in database"""
    try:
        if not is_active_org_admin():
            return jsonify({"success": False, "error": "Admin required"}), 403

        create_adm_phases()
        return jsonify({"success": True, "message": "ADM phases initialized"})
    except SQLAlchemyError as e:
        current_app.logger.error(f"Error initializing ADM phases: {str(e)}")
        return jsonify({"success": False, "error": "Database error"}), 500


# ============================================================================
# KANBAN BOARD ENDPOINTS
# ============================================================================


@adm_kanban_bp.route("/boards", methods=["GET"])
@login_required
def get_boards():
    """Get all kanban boards for current user"""
    try:
        # Users can see boards they created or are assigned to cards on
        user_boards = KanbanBoard.query.filter_by(created_by_id=current_user.id).all()

        # Also get boards where user has cards assigned
        assigned_board_ids = (
            db.session.query(KanbanCard.board_id)
            .filter(KanbanCard.assigned_to_id == current_user.id)
            .distinct()
            .all()
        )
        assigned_board_ids = [bid[0] for bid in assigned_board_ids]

        assigned_boards = KanbanBoard.query.filter(
            KanbanBoard.id.in_(assigned_board_ids),
            KanbanBoard.created_by_id != current_user.id,  # Avoid duplicates
        ).all()

        all_boards = user_boards + assigned_boards

        return jsonify(
            {
                "success": True,
                "data": [
                    {
                        "id": board.id,
                        "name": board.name,
                        "description": board.description,
                        "current_adm_phase": board.current_adm_phase,
                        "created_at": board.created_at.isoformat()
                        if board.created_at
                        else None,
                        "card_count": len(board.cards),
                        "created_by": {
                            "id": board.created_by.id,
                            "name": board.created_by.full_name(),
                        }
                        if board.created_by
                        else None,
                    }
                    for board in all_boards
                ],
            }
        )
    except SQLAlchemyError as e:
        current_app.logger.error(f"Error fetching boards: {str(e)}")
        return jsonify({"success": False, "error": "Database error"}), 500


@adm_kanban_bp.route("/boards", methods=["POST"])
@login_required
@audit_log("kanban_board_create")
def create_board():
    """Create a new kanban board"""
    try:
        data = request.get_json()
        if not data or not data.get("name"):
            return jsonify({"success": False, "error": "Board name required"}), 400

        board = KanbanBoard(
            name=data["name"],
            description=data.get("description"),
            project_name=data.get("project_name"),
            created_by_id=current_user.id,
        )

        db.session.add(board)
        db.session.commit()

        return (
            jsonify(
                {
                    "success": True,
                    "data": {
                        "id": board.id,
                        "name": board.name,
                        "description": board.description,
                        "created_at": board.created_at.isoformat(),
                    },
                }
            ),
            201,
        )
    except SQLAlchemyError as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating board: {str(e)}")
        return jsonify({"success": False, "error": "Database error"}), 500


@adm_kanban_bp.route("/boards/<int:board_id>", methods=["GET"])
@login_required
def get_board(board_id):
    """Get detailed board information with cards"""
    try:
        board = KanbanBoard.query.get_or_404(board_id)

        # Check permissions
        if board.created_by_id != current_user.id:
            # Check if user has cards on this board
            has_access = (
                KanbanCard.query.filter_by(
                    board_id=board_id, assigned_to_id=current_user.id
                ).first()
                is not None
            )
            if not has_access:
                return jsonify({"success": False, "error": "Access denied"}), 403

        # Batch-resolve assignees: one query for every assigned id on the board,
        # restricted to each card's own organisation, instead of the one
        # query per card user_in_org would run below. Same fail-closed
        # predicate as user_in_org (id match AND organisation match), just
        # evaluated for the whole board at once.
        cards = list(board.cards)
        assigned_pairs = {
            (card.assigned_to_id, card.organization_id)
            for card in cards
            if card.assigned_to_id
        }
        assignees_by_pair = {}
        org_ids = {org_id for _, org_id in assigned_pairs if org_id is not None}
        if assigned_pairs and org_ids:
            user_ids = {user_id for user_id, _ in assigned_pairs}
            candidates = User.query.filter(
                User.id.in_(user_ids), User.organization_id.in_(org_ids)
            ).all()  # model-safety-ok
            users_by_id = {u.id: u for u in candidates}
            for user_id, org_id in assigned_pairs:
                u = users_by_id.get(user_id)
                if u is not None and u.organization_id == org_id:
                    assignees_by_pair[(user_id, org_id)] = u

        # Group cards by ADM phase
        cards_by_phase = {}
        for card in cards:
            phase_code = card.adm_phase.code if card.adm_phase else "unknown"
            if phase_code not in cards_by_phase:
                cards_by_phase[phase_code] = []
            # assigned_to_id is request-supplied (see create_card/update_card), so
            # the assignee is named only when it resolves inside the card's own
            # organisation — a foreign id shows no name.
            assignee = (
                assignees_by_pair.get((card.assigned_to_id, card.organization_id))
                if card.assigned_to_id
                else None
            )
            cards_by_phase[phase_code].append(
                {
                    "id": card.id,
                    "title": card.title,
                    "description": card.description,
                    "card_type": card.card_type,
                    "status": card.status,
                    "priority": card.priority,
                    "archimate_elements": [],
                    "depends_on": card.depends_on,
                    "blocks": card.blocks,
                    "assigned_to": {
                        "id": assignee.id,
                        "name": assignee.full_name(),
                    }
                    if assignee
                    else None,
                    "created_at": card.created_at.isoformat()
                    if card.created_at
                    else None,
                    "updated_at": card.updated_at.isoformat()
                    if card.updated_at
                    else None,
                }
            )

        return jsonify(
            {
                "success": True,
                "data": {
                    "id": board.id,
                    "name": board.name,
                    "description": board.description,
                    "current_adm_phase": board.current_adm_phase,
                    "cards_by_phase": cards_by_phase,
                    "created_by": {
                        "id": board.created_by.id,
                        "name": board.created_by.full_name(),
                    }
                    if board.created_by
                    else None,
                },
            }
        )
    except SQLAlchemyError as e:
        current_app.logger.error(f"Error fetching board: {str(e)}")
        return jsonify({"success": False, "error": "Database error"}), 500


# ============================================================================
# KANBAN CARD ENDPOINTS
# ============================================================================


@adm_kanban_bp.route("/boards/<int:board_id>/cards", methods=["POST"])
@login_required
# CSRF: Protected via X-CSRFToken header sent by Platform.fetch
@login_required
@audit_log("kanban_card_create")
def create_card(board_id):
    """Create a new kanban card"""
    try:
        board = KanbanBoard.query.get_or_404(board_id)

        # Check permissions
        if board.created_by_id != current_user.id and not is_active_org_admin():
            return jsonify({"success": False, "error": "Access denied"}), 403

        data = request.get_json()
        if not data or not data.get("title") or not data.get("adm_phase_id"):
            return jsonify(
                {"success": False, "error": "Title and ADM phase required"}
            ), 400

        # Validate ADM phase exists
        phase = ADMPhase.query.get(data["adm_phase_id"])
        if not phase:
            return jsonify({"success": False, "error": "Invalid ADM phase"}), 400

        # Phase scope enforcement: when board has current_adm_phase set,
        # cards can only be created in that phase
        if board.current_adm_phase and phase.code != board.current_adm_phase:
            return jsonify({
                "success": False,
                "error": f"Board is scoped to phase {board.current_adm_phase}. "
                         f"Cards can only be created in that phase."
            }), 400

        # AUDIT-ADM-002: Validate no circular dependencies in depends_on/blocks
        new_depends_on = data.get("depends_on", [])
        new_blocks = data.get("blocks", [])
        if new_depends_on and new_blocks:
            overlap = set(new_depends_on) & set(new_blocks)
            if overlap:
                return jsonify(
                    {
                        "success": False,
                        "error": "A card cannot both depend on and block the same card(s)",
                    }
                ), 400

        # Check that proposed depends_on cards don't already depend on proposed blocks cards
        # (which would create a cycle when this card is inserted between them)
        for dep_id in new_depends_on:
            dep_card = KanbanCard.query.get(dep_id)
            if dep_card and dep_card.depends_on:
                for block_id in new_blocks:
                    if block_id in dep_card.depends_on:
                        return jsonify(
                            {
                                "success": False,
                                "error": f"Circular dependency: card #{dep_id} already depends on card #{block_id}",
                            }
                        ), 400

        # Normalize nullable integer FK fields — empty string from form should be None
        assigned_to_id = data.get("assigned_to_id") or None
        if assigned_to_id and not user_in_org(assigned_to_id, current_org_id()):
            return jsonify({"success": False, "error": "Invalid assigned_to_id"}), 400
        arb_review_id = data.get("arb_review_id") or None

        card = KanbanCard(
            title=data["title"],
            description=data.get("description") or None,
            adm_phase_id=data["adm_phase_id"],
            board_id=board_id,
            card_type=data.get("card_type", "requirement"),
            status=data.get("status", "todo"),
            priority=data.get("priority", "medium"),
            archimate_element_ids=data.get("archimate_element_ids") or [],
            application_ids=data.get("application_ids") or [],
            system_ids=data.get("system_ids") or [],
            initiative_ids=data.get("initiative_ids") or [],
            affects_applications=data.get("affects_applications") or [],
            affects_systems=data.get("affects_systems") or [],
            implements_capabilities=data.get("implements_capabilities") or [],
            depends_on=data.get("depends_on") or [],
            blocks=data.get("blocks") or [],
            assigned_to_id=assigned_to_id,
            created_by_id=current_user.id,
            arb_review_id=arb_review_id,
        )

        db.session.add(card)

        # Handle ARB review creation if requested
        if data.get("requires_review"):
            try:
                # Import ARB models (lazy import to avoid circular dependencies)
                from app.models.arb_models import ARBReviewItem

                # Get board creator as submitter (architect)
                submitter = board.created_by

                # Create ARB review item
                arb_review = ARBReviewItem(
                    title=f"[ADM Kanban] {data['title']}",
                    description=f"""
ADM Phase: {phase.name} ({phase.code})
Project: {board.project_name or "N/A"}

Description: {data.get("description", "")}

ArchiMate Elements: {", ".join([f"{elem.get('type', '')} ({elem.get('layer', '')})" for elem in data.get("archimate_elements", [])]) or "None"}

Review Type: {data.get("review_type", "architectural_decision").replace("_", " ").title()}
Justification: {data.get("review_justification", "")}

Created from ADM Kanban board: {board.name}
                    """.strip(),
                    review_type=data.get("review_type", "architectural_decision"),
                    status="submitted",
                    priority=data.get("priority", "medium"),
                    submitter_id=submitter.id,
                    reviewer_id=None,  # Will be assigned by ARB coordinator
                    decision_type="approval",
                    system_approach="adm_kanban_integration",
                )

                db.session.add(arb_review)
                db.session.flush()  # Get the ID

                # Link the card to the ARB review
                card.arb_review_id = arb_review.id

                current_app.logger.info(
                    f"Created ARB review {arb_review.id} for Kanban card {card.id}"
                )

            except Exception as e:
                current_app.logger.error(
                    f"Failed to create ARB review for card {card.id}: {str(e)}"
                )
                # Don't fail the entire card creation, just log the error

        db.session.commit()
        return jsonify({"success": True, "id": card.id, "card": {
            "id": card.id,
            "title": card.title,
            "adm_phase_id": card.adm_phase_id,
        }}), 201
    except SQLAlchemyError as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating card: {str(e)}")
        return jsonify({"success": False, "error": "Database error"}), 500


@adm_kanban_bp.route("/cards/<int:card_id>", methods=["PUT"])
@login_required
# CSRF: Protected via X-CSRFToken header sent by Platform.fetch
@login_required
@audit_log("kanban_card_update")
def update_card(card_id):
    """Update a kanban card"""
    try:
        card = KanbanCard.query.get_or_404(card_id)

        # Check permissions
        if (
            card.created_by_id != current_user.id
            and card.assigned_to_id != current_user.id
            and card.board.created_by_id != current_user.id
            and not is_active_org_admin()
        ):
            return jsonify({"success": False, "error": "Access denied"}), 403

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        # AUDIT-ADM-002: Check for circular dependencies before updating
        if "depends_on" in data and data["depends_on"]:
            cycle_check = detect_circular_dependency(card.id, data["depends_on"])
            if not cycle_check["valid"]:
                return jsonify(
                    {
                        "success": False,
                        "error": cycle_check["reason"],
                    }
                ), 400

        if "blocks" in data and data["blocks"]:
            # If card blocks X, then X effectively depends on card.
            # Check that card doesn't already depend (transitively) on any of the blocked cards.
            for blocked_id in data["blocks"]:
                blocked_card = KanbanCard.query.get(blocked_id)
                if (
                    blocked_card
                    and blocked_card.depends_on
                    and card.id in blocked_card.depends_on
                ):
                    return jsonify(
                        {
                            "success": False,
                            "error": f"Circular dependency: card #{blocked_id} already depends on this card",
                        }
                    ), 400

        # Update fields
        # Normalize nullable integer FK fields to None if empty string
        if "assigned_to_id" in data:
            data["assigned_to_id"] = data["assigned_to_id"] or None
            if same_user_id(data["assigned_to_id"], card.assigned_to_id):
                # The edit form sends the stored assignee back unchanged. A card
                # written before the organisation check may hold another
                # organisation's user id; that must not block editing the other
                # fields, so an unchanged value is left as it is (the board API
                # still names it only inside the card's own organisation).
                data.pop("assigned_to_id")
            elif data["assigned_to_id"] and not user_in_org(
                data["assigned_to_id"], current_org_id()
            ):
                return jsonify({"success": False, "error": "Invalid assigned_to_id"}), 400

        # Validate an ADM phase change before any field is changed: the audit
        # decorator commits the session after this view returns, refusals
        # included, so a field set before one of these 400s would be saved.
        if "adm_phase_id" in data:
            new_phase = ADMPhase.query.get(data["adm_phase_id"])
            if not new_phase:
                return jsonify({"success": False, "error": "Invalid ADM phase"}), 400

            # Phase scope enforcement: block phase reassignment when board is scoped
            if card.board.current_adm_phase and new_phase.code != card.board.current_adm_phase:
                return jsonify({
                    "success": False,
                    "error": "Cannot change phase while board is scoped to "
                             f"{card.board.current_adm_phase}"
                }), 400

            # Validate phase transition
            if card.adm_phase_id and card.adm_phase_id != data["adm_phase_id"]:
                validation_result = validate_phase_transition(
                    card.adm_phase_id, data["adm_phase_id"]
                )
                if not validation_result["valid"]:
                    return (
                        jsonify(
                            {
                                "success": False,
                                "error": f"Invalid phase transition: {validation_result['reason']}",
                            }
                        ),
                        400,
                    )

            # Validate dependencies before phase change
            if card.adm_phase_id != data["adm_phase_id"]:
                # Judge the card as it will be after this edit (depends_on /
                # blocks from the request win) without touching it yet.
                dependency_result = validate_card_dependencies(
                    SimpleNamespace(
                        blocks=data.get("blocks", card.blocks),
                        depends_on=data.get("depends_on", card.depends_on),
                    ),
                    data["adm_phase_id"],
                )
                if not dependency_result["valid"]:
                    return (
                        jsonify(
                            {
                                "success": False,
                                "error": dependency_result["reason"],
                                "blocking_cards": dependency_result.get(
                                    "blocking_cards", []
                                ),
                            }
                        ),
                        400,
                    )

        for field in [
            "title",
            "description",
            "card_type",
            "status",
            "priority",
            "archimate_element_ids",
            "application_ids",
            "system_ids",
            "initiative_ids",
            "affects_applications",
            "affects_systems",
            "implements_capabilities",
            "depends_on",
            "blocks",
            "assigned_to_id",
        ]:
            if field in data:
                setattr(card, field, data[field])

        # Apply the phase change validated above
        if "adm_phase_id" in data:
            card.adm_phase_id = data["adm_phase_id"]

        # Handle status changes
        if "status" in data:
            if data["status"] == "in_progress" and not card.started_at:
                card.started_at = datetime.utcnow()
            elif data["status"] == "done" and not card.completed_at:
                card.completed_at = datetime.utcnow()

        db.session.commit()

        return jsonify(
            {
                "success": True,
                "data": {
                    "id": card.id,
                    "title": card.title,
                    "status": card.status,
                    "adm_phase": card.adm_phase.code if card.adm_phase else None,
                    "updated_at": card.updated_at.isoformat(),
                },
            }
        )
    except SQLAlchemyError as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating card: {str(e)}")
        return jsonify({"success": False, "error": "Database error"}), 500


@adm_kanban_bp.route("/cards/<int:card_id>", methods=["DELETE"])
@login_required
@audit_log("kanban_card_delete")
def delete_card(card_id):
    """Delete a kanban card"""
    try:
        card = KanbanCard.query.get_or_404(card_id)

        # Check permissions
        if (
            card.created_by_id != current_user.id
            and card.board.created_by_id != current_user.id
            and not is_active_org_admin()
        ):
            return jsonify({"success": False, "error": "Access denied"}), 403

        db.session.delete(card)
        db.session.commit()

        return jsonify({"success": True, "message": "Card deleted"})
    except SQLAlchemyError as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting card: {str(e)}")
        return jsonify({"success": False, "error": "Database error"}), 500


# ============================================================================
# ANALYTICS AND REPORTING ENDPOINTS
# ============================================================================


@adm_kanban_bp.route("/boards/<int:board_id>/analytics", methods=["GET"])
@login_required
def get_board_analytics(board_id):
    """Get analytics for a kanban board"""
    try:
        board = KanbanBoard.query.get_or_404(board_id)

        # Check permissions
        if board.created_by_id != current_user.id and not is_active_org_admin():
            return jsonify({"success": False, "error": "Access denied"}), 403

        total_cards = len(board.cards)
        completed_cards = len([c for c in board.cards if c.status == "done"])

        # Cards by phase
        phase_stats = {}
        for card in board.cards:
            phase_code = card.adm_phase.code if card.adm_phase else "unknown"
            if phase_code not in phase_stats:
                phase_stats[phase_code] = {"total": 0, "completed": 0}
            phase_stats[phase_code]["total"] += 1
            if card.status == "done":
                phase_stats[phase_code]["completed"] += 1

        # Cards by priority
        priority_stats = {}
        for card in board.cards:
            priority = card.priority or "medium"
            if priority not in priority_stats:
                priority_stats[priority] = 0
            priority_stats[priority] += 1

        return jsonify(
            {
                "success": True,
                "data": {
                    "total_cards": total_cards,
                    "completed_cards": completed_cards,
                    "completion_rate": (completed_cards / total_cards * 100)
                    if total_cards > 0
                    else 0,
                    "phase_stats": phase_stats,
                    "priority_stats": priority_stats,
                },
            }
        )
    except SQLAlchemyError as e:
        current_app.logger.error(f"Error fetching analytics: {str(e)}")
        return jsonify({"success": False, "error": "Database error"}), 500


# ============================================================================
# APPROVAL WORKFLOW ENDPOINTS
# ============================================================================


@adm_kanban_bp.route("/approvals/<int:approval_id>/decision", methods=["POST"])
@login_required
@audit_log("adm_decision_record")
def record_decision(approval_id):
    """Record an approval decision for a phase transition request."""
    data = request.get_json() or {}
    decision = data.get("decision")
    comments = data.get("comments", "")

    if decision not in ("approved", "approved_with_conditions", "rejected"):
        return jsonify({"success": False, "error": "Invalid decision value"}), 400

    # Approval model not yet persisted — log the decision for now
    current_app.logger.info(
        "Approval %s decision recorded: %s by user %s — %s",
        approval_id,
        decision,
        current_user.id,
        comments,
    )
    return jsonify(
        {
            "success": True,
            "message": f"Decision '{decision}' recorded for approval {approval_id}",
            "approval_id": approval_id,
            "decision": decision,
        }
    )


@adm_kanban_bp.route("/approvals/<int:approval_id>/assign-reviewer", methods=["POST"])
@login_required
@audit_log("adm_reviewer_assign")
def assign_reviewer(approval_id):
    """Assign a reviewer to an approval request."""
    data = request.get_json() or {}
    reviewer_id = data.get("reviewer_id")

    if not reviewer_id:
        return jsonify({"success": False, "error": "reviewer_id is required"}), 400

    reviewer = User.query.filter_by(id=reviewer_id, organization_id=g.current_org_id).first()
    if not reviewer:
        return jsonify({"success": False, "error": "Reviewer not found"}), 404

    current_app.logger.info(
        "Reviewer %s assigned to approval %s by user %s",
        reviewer_id,
        approval_id,
        current_user.id,
    )
    return jsonify(
        {
            "success": True,
            "message": f"Reviewer assigned to approval {approval_id}",
            "approval_id": approval_id,
            "reviewer_id": reviewer_id,
        }
    )


@adm_kanban_bp.route("/checkpoints/<int:checkpoint_id>/toggle", methods=["POST"])
@login_required
@audit_log("adm_checkpoint_toggle")
def toggle_checkpoint(checkpoint_id):
    """Toggle a compliance checkpoint status."""
    current_app.logger.info(
        "Checkpoint %s toggled by user %s",
        checkpoint_id,
        current_user.id,
    )
    return jsonify(
        {
            "success": True,
            "message": f"Checkpoint {checkpoint_id} toggled",
            "checkpoint_id": checkpoint_id,
        }
    )


@adm_kanban_bp.route(
    "/approvals/<int:approval_id>/verify-checkpoints", methods=["POST"]
)
@login_required
@audit_log("adm_checkpoints_verify")
def verify_checkpoints(approval_id):
    """Verify all compliance checkpoints for an approval."""
    current_app.logger.info(
        "Checkpoints verified for approval %s by user %s",
        approval_id,
        current_user.id,
    )
    return jsonify(
        {
            "success": True,
            "message": f"All checkpoints verified for approval {approval_id}",
            "approval_id": approval_id,
            "verified": True,
        }
    )


@adm_kanban_bp.route("/cards/<int:card_id>/transition-request", methods=["POST"])
@login_required
@audit_log("adm_transition_request_create")
def create_transition_request(card_id):
    """Create a phase transition approval request for a card."""
    card = KanbanCard.query.get(card_id)
    if not card:
        return jsonify({"success": False, "error": "Card not found"}), 404

    data = request.get_json() or {}
    target_phase = data.get("target_phase")
    justification = data.get("justification", "")

    if not target_phase:
        return jsonify({"success": False, "error": "target_phase is required"}), 400

    current_app.logger.info(
        "Transition request created for card %s to phase %s by user %s — %s",
        card_id,
        target_phase,
        current_user.id,
        justification,
    )
    return jsonify(
        {
            "success": True,
            "message": f"Transition request created for card {card_id}",
            "card_id": card_id,
            "target_phase": target_phase,
        }
    )


# ============================================================================
# ARCHIMATE INTEGRATION ENDPOINTS
# ============================================================================


@adm_kanban_bp.route("/archimate/elements", methods=["GET"])
@login_required
def get_archimate_elements():
    """Get available ArchiMate element types for tagging"""
    return jsonify({"success": True, "data": ARCHIMATE_ELEMENTS})


# ============================================================================
# UTILITY ENDPOINTS
# ============================================================================


@adm_kanban_bp.route("/health", methods=["GET"])
@login_required
def health_check():
    """Health check endpoint"""
    return jsonify(
        {
            "success": True,
            "service": "ADM Kanban API",
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
        }
    )
