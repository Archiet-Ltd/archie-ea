"""
ADM Kanban V2 API Routes -- Virtual projection board.

Cards are computed views of Solutions, ARB submissions, consolidation waves,
and gaps. No KanbanCard rows. Moving a card updates the source entity.

Registered at /api/adm-kanban/v2
"""

import logging

from flask import Blueprint, current_app, g, jsonify, request
from flask_login import current_user, login_required

from app import db
from app.decorators import audit_log
from app.middleware.tenant_context import current_org_id
from app.utils.tenant_users import same_user_id, user_in_org
from app.services.kanban_projection_service import (
    COLUMNS,
    ADM_PHASES,
    WIP_LIMITS,
    KanbanProjectionService,
)

logger = logging.getLogger(__name__)


def _push_kanban_card_async(card_id: int) -> None:
    """Fire-and-forget Jira push for a newly created KanbanCard. Never raises."""
    try:
        from app.services.jira_push_service import get_jira_push_service
        get_jira_push_service().push_kanban_card(card_id)
    except Exception as exc:
        logger.warning("Jira push skipped for KanbanCard %s: %s", card_id, exc)


def _update_jira_status_async(card_id: int, to_column: str) -> None:
    """Fire-and-forget Jira status update when a KanbanCard column changes. Never raises."""
    try:
        from app.services.jira_push_service import get_jira_push_service
        get_jira_push_service().update_kanban_card_status(card_id, to_column)
    except Exception as exc:
        logger.warning("Jira status update skipped for KanbanCard %s: %s", card_id, exc)


adm_kanban_v2_bp = Blueprint(
    "adm_kanban_v2", __name__, url_prefix="/api/adm-kanban/v2"
)

_ARB_GATED_COLUMNS = {"review", "approved", "done"}


def check_arb_clearance(card, target_column: str) -> bool:
    """Return True if the card can move to target_column without ARB block.

    Returns False if: card.requires_arb_signoff is True AND target_column is
    in ARB_GATED_COLUMNS AND no ARBReviewItem exists for this card.
    """
    if not getattr(card, "requires_arb_signoff", False):
        return True
    if target_column not in _ARB_GATED_COLUMNS:
        return True
    try:
        from app.models.architecture_review_board import ARBReviewItem
        existing = ARBReviewItem.query.filter(
            ARBReviewItem.title.ilike(f"%{card.title[:50]}%")
        ).first()
        return existing is not None
    except Exception as exc:
        logger.warning("ARB clearance check failed for card %s, failing open: %s", card.id, exc)
        return True


@adm_kanban_v2_bp.route("/cards", methods=["GET"])
@login_required
def get_cards():
    """
    Get all projected cards with phase and column counts.

    Query params:
        phase:     ADM phase code (A-H, PRELIM, REQ) or 'all'
        assignee:  'me' or user_id
        priority:  critical|high|medium|low
        card_type: solution|arb|wave|gap|assessment
    """
    phase = request.args.get("phase", "all")
    assignee = request.args.get("assignee")
    priority = request.args.get("priority")
    card_type = request.args.get("card_type")

    assignee_id = None
    if assignee == "me":
        assignee_id = current_user.id
    elif assignee and assignee.isdigit():
        assignee_id = int(assignee)

    svc = KanbanProjectionService()
    result = svc.get_cards(
        phase=phase,
        assignee=assignee,
        assignee_id=assignee_id,
        priority=priority,
        card_type=card_type,
    )

    return jsonify(
        {
            "success": True,
            "cards": result["cards"],
            "phase_counts": result["phase_counts"],
            "column_counts": result["column_counts"],
            "columns": COLUMNS,
            "phases": ADM_PHASES,
        }
    )


@adm_kanban_v2_bp.route("/cards", methods=["POST"])
@login_required
@audit_log("kanban_task_create")
def create_task():
    """
    Create a new KanbanCard (manual governance task) and return it as a projected card.

    Body: { "title": str, "phase": str, "priority": str, "card_type": str, "description": str }
    Returns: { "success": True, "card": {...} }
    """
    from app.models.adm_kanban import ADMPhase, KanbanBoard, KanbanCard

    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"success": False, "error": "Title is required"}), 400

    phase_code = (data.get("phase") or "A").strip().upper()
    priority = data.get("priority", "medium")
    description = (data.get("description") or "").strip()

    arch_element_type = data.get("arch_element_type", "WorkPackage")
    arch_domain = data.get("arch_domain", "Business")
    togaf_deliverable = data.get("togaf_deliverable") or None
    closes_gap_id = data.get("closes_gap_id") or None
    requires_arb_signoff = bool(data.get("requires_arb_signoff", False))
    target_plateau_id = data.get("target_plateau_id") or None
    # backward-compat: card_type mirrors arch_element_type
    card_type_val = arch_element_type

    phase = ADMPhase.query.filter_by(code=phase_code).first()
    if not phase:
        return jsonify(
            {"success": False, "error": f"Phase '{phase_code}' not found. Run /adm-kanban/init-phases first."}
        ), 400

    # Checked before anything is added to the session: the audit decorator
    # commits after this view returns, even on a refusal, so an auto-created
    # board added above a 400 would still be saved.
    assignee = data.get('assignee')
    if assignee and not user_in_org(assignee, current_org_id()):
        return jsonify({"success": False, "error": "Invalid assignee"}), 400

    # Find or auto-create a default board for this user
    board = (
        KanbanBoard.query
        .filter_by(created_by_id=current_user.id)
        .order_by(KanbanBoard.created_at.desc())
        .first()
    )
    if not board:
        board = KanbanBoard(
            name="Architecture Work Queue",
            description="Auto-created board for governance tasks",
            created_by_id=current_user.id,
        )
        db.session.add(board)
        db.session.flush()

    card = KanbanCard(
        title=title,
        description=description,
        adm_phase_id=phase.id,
        board_id=board.id,
        card_type=card_type_val,
        priority=priority,
        status="todo",
        created_by_id=current_user.id,
        arch_element_type=arch_element_type,
        arch_domain=arch_domain,
        togaf_deliverable=togaf_deliverable,
        closes_gap_id=closes_gap_id,
        requires_arb_signoff=requires_arb_signoff,
        target_plateau_id=target_plateau_id,
    )
    card.requirement_ids = data.get('requirement_ids', [])
    card.goal_ids = data.get('goal_ids', [])
    card.driver_ids = data.get('driver_ids', [])
    card.principle_ids = data.get('principle_ids', [])
    card.issue_type = data.get('issue_type', 'Task')
    card.assignee = assignee
    card.story_points = data.get('story_points')
    card.labels = data.get('labels', [])
    card.arch_layer = data.get('arch_layer')
    card.acceptance_criteria = data.get('acceptance_criteria')
    card.progress_pct = data.get('progress_pct', 0)
    db.session.add(card)
    db.session.commit()

    # Phase G: auto-push to Jira if enabled
    if current_app.config.get("JIRA_AUTO_PUSH"):
        try:
            from app.services.jira_push_service import get_jira_push_service
            push_result = get_jira_push_service().push_kanban_card(card.id)
            current_app.logger.info("Jira auto-push card %s: %s", card.id, push_result)
        except Exception as _jira_exc:
            current_app.logger.warning("Jira auto-push failed for card %s: %s", card.id, _jira_exc)

    svc = KanbanProjectionService()
    return jsonify({"success": True, "card": svc._project_one_kanban_card(card)}), 201


@adm_kanban_v2_bp.route("/cards/<card_ref>/move", methods=["POST"])
@login_required
@audit_log("kanban_card_move")
def move_card(card_ref):
    """
    Move a card to a different column by updating the source entity's status.

    card_ref format: "{type}:{id}" e.g. "solution:42"
    Body: { "to_column": "review" }
    """
    if ":" not in card_ref:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid card reference format. Expected 'type:id'",
                }
            ),
            400,
        )

    parts = card_ref.split(":", 1)
    card_type = parts[0]
    try:
        entity_id = int(parts[1])
    except (ValueError, IndexError):
        return (
            jsonify({"success": False, "error": "Invalid entity ID in card reference"}),
            400,
        )

    data = request.get_json() or {}
    to_column = data.get("to_column")
    if not to_column:
        return jsonify({"success": False, "error": "to_column is required"}), 400

    svc = KanbanProjectionService()

    # ARB governance gate: load task card and check clearance before moving
    _arb_card = None
    if card_type == "task":
        from app.models.adm_kanban import KanbanCard
        _arb_card = db.session.get(KanbanCard, entity_id)
        if _arb_card and not check_arb_clearance(_arb_card, to_column):
            return jsonify({
                "error": "ARB sign-off required before moving to this column",
                "requires_arb": True,
                "card_id": entity_id,
            }), 409

    result = svc.move_card(card_type, entity_id, to_column)

    if not result.get("success"):
        status = 409 if result.get("wip_exceeded") else 400
        return jsonify(result), status

    if card_type == "task" and current_app.config.get("JIRA_AUTO_PUSH"):
        if _arb_card is None or check_arb_clearance(_arb_card, to_column):
            try:
                from app.services.jira_push_service import get_jira_push_service
                push_result = get_jira_push_service().update_kanban_card_status(entity_id, to_column)
                current_app.logger.info("Jira auto-push card %s: %s", entity_id, push_result)
            except Exception as _jira_exc:
                current_app.logger.warning("Jira auto-push failed for card %s: %s", entity_id, _jira_exc)

    return jsonify(result)


@adm_kanban_v2_bp.route("/cards/<card_ref>/artifacts", methods=["GET"])
@login_required
def get_card_artifacts(card_ref):
    """
    Return full ArchiMate artifact breakdown for a card.

    card_ref format: "solution:{id}"
    Returns elements grouped by layer with names.
    """
    if ":" not in card_ref:
        return jsonify({"success": False, "error": "Invalid card reference"}), 400

    parts = card_ref.split(":", 1)
    card_type = parts[0]
    try:
        entity_id = int(parts[1])
    except (ValueError, IndexError):
        return jsonify({"success": False, "error": "Invalid entity ID"}), 400

    if card_type != "solution":
        return jsonify({"success": True, "artifacts": {}, "phase_gate": {}})

    from app.models.solution_models import Solution, SolutionArchiMateElement

    solution = Solution.query.get(entity_id)
    if not solution:
        return jsonify({"success": False, "error": "Solution not found"}), 404

    # Get artifacts grouped by layer
    junctions = SolutionArchiMateElement.query.filter_by(
        solution_id=entity_id
    ).order_by(
        SolutionArchiMateElement.layer_type,
        SolutionArchiMateElement.element_name,
    ).all()

    artifacts = {}
    for j in junctions:
        layer = j.layer_type or "unknown"
        if layer not in artifacts:
            artifacts[layer] = []
        artifacts[layer].append({
            "id": j.element_id,
            "table": j.element_table,
            "name": j.element_name,
            "relationship": j.relationship_type,
            "is_new": j.is_new_element,
        })

    phase_gate = solution.validate_phase_gate(solution.adm_phase or "A")

    return jsonify({
        "success": True,
        "artifacts": artifacts,
        "artifact_total": sum(len(v) for v in artifacts.values()),
        "phase_gate": phase_gate,
        "adm_phase": solution.adm_phase or "A",
    })



@adm_kanban_v2_bp.route("/cards/<card_ref>/blockers", methods=["GET"])
@login_required
def get_card_blockers(card_ref):
    """
    Return active blockers for a task card (depends_on entries that are not done).

    card_ref format: "task:{id}"
    Response: { "success": true, "blockers": [{id, title, status, phase}], "blocker_count": N }
    """
    if ":" not in card_ref:
        return jsonify({"success": False, "error": "Invalid card reference"}), 400

    parts = card_ref.split(":", 1)
    card_type = parts[0]
    if card_type != "task":
        return jsonify({"success": True, "blockers": [], "blocker_count": 0})

    try:
        entity_id = int(parts[1])
    except (ValueError, IndexError):
        return jsonify({"success": False, "error": "Invalid entity ID"}), 400

    from app.models.adm_kanban import KanbanCard

    card = db.session.get(KanbanCard, entity_id)
    if not card:
        return jsonify({"success": False, "error": "Card not found"}), 404

    depends_on = card.depends_on or []
    if not depends_on:
        return jsonify({"success": True, "blockers": [], "blocker_count": 0})

    dep_cards = KanbanCard.query.filter(KanbanCard.id.in_(depends_on)).all()  # model-safety-ok
    blockers = [
        {
            "id": c.id,
            "title": c.title,
            "status": c.status,
            "phase": c.adm_phase.code if c.adm_phase else "?",
        }
        for c in dep_cards
        if c.status != "done"
    ]

    return jsonify({"success": True, "blockers": blockers, "blocker_count": len(blockers)})


@adm_kanban_v2_bp.route("/cards/<card_ref>", methods=["PATCH"])
@login_required
@audit_log("kanban_task_update")
def update_task(card_ref):
    """
    Update a task-type KanbanCard (title, description, priority, phase).

    card_ref format: "task:{id}"
    Body: { "title"?, "description"?, "priority"?, "phase"? }
    Returns: { "success": True, "card": {...} }
    """
    if ":" not in card_ref:
        return jsonify({"success": False, "error": "Invalid card reference"}), 400

    card_type, _, entity_id_str = card_ref.partition(":")
    if card_type != "task":
        return jsonify({"success": False, "error": "Only task cards can be edited"}), 403

    try:
        entity_id = int(entity_id_str)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid entity ID"}), 400

    from app.models.adm_kanban import ADMPhase, KanbanCard

    card = db.session.get(KanbanCard, entity_id)
    if not card:
        return jsonify({"success": False, "error": "Card not found"}), 404

    data = request.get_json() or {}

    # Every refusal below runs before any field is changed: the audit
    # decorator commits the session after this view returns, even on a
    # refusal, so a field set before a 400 would still be saved.
    if "title" in data:
        title = (data["title"] or "").strip()
        if not title:
            return jsonify({"success": False, "error": "Title cannot be empty"}), 400

    # The card drawer sends the stored assignee back unchanged on every save.
    # A card written before the organisation check may hold another
    # organisation's user id; an unchanged value is left as it is rather than
    # refusing the whole edit (the projection names it only inside the card's
    # own organisation, so no foreign name is shown).
    assignee_unchanged = "assignee" in data and same_user_id(data["assignee"], card.assignee)
    if (
        "assignee" in data
        and not assignee_unchanged
        and data["assignee"]
        and not user_in_org(data["assignee"], current_org_id())
    ):
        return jsonify({"success": False, "error": "Invalid assignee"}), 400

    if "title" in data:
        card.title = title

    if "description" in data:
        card.description = (data.get("description") or "").strip()

    if "priority" in data and data["priority"] in ("critical", "high", "medium", "low"):
        card.priority = data["priority"]

    if "phase" in data:
        phase = ADMPhase.query.filter_by(code=(data["phase"] or "").upper()).first()  # model-safety-ok
        if phase:
            card.adm_phase_id = phase.id

    _valid_domains = ("Business", "Data", "Application", "Technology", "Cross-cutting")
    if "arch_domain" in data and data["arch_domain"] in _valid_domains:
        card.arch_domain = data["arch_domain"]

    if "requirement_ids" in data:
        card.requirement_ids = data["requirement_ids"]
    if "goal_ids" in data:
        card.goal_ids = data["goal_ids"]
    if "driver_ids" in data:
        card.driver_ids = data["driver_ids"]
    if "principle_ids" in data:
        card.principle_ids = data["principle_ids"]
    if "issue_type" in data:
        card.issue_type = data["issue_type"]
    if "assignee" in data and not assignee_unchanged:
        card.assignee = data["assignee"]
    if "story_points" in data:
        card.story_points = data["story_points"]
    if "labels" in data:
        card.labels = data["labels"]
    if "arch_layer" in data:
        card.arch_layer = data["arch_layer"]
    if "acceptance_criteria" in data:
        card.acceptance_criteria = data["acceptance_criteria"]
    if "progress_pct" in data:
        card.progress_pct = data["progress_pct"]

    db.session.commit()

    svc = KanbanProjectionService()
    return jsonify({"success": True, "card": svc._project_one_kanban_card(card)})


@adm_kanban_v2_bp.route("/cards/<card_ref>", methods=["DELETE"])
@login_required
@audit_log("kanban_task_delete")
def delete_task(card_ref):
    """
    Hard-delete a task-type KanbanCard.

    card_ref format: "task:{id}"
    Returns: { "success": True, "deleted_id": "task:42" }
    """
    if ":" not in card_ref:
        return jsonify({"success": False, "error": "Invalid card reference"}), 400

    card_type, _, entity_id_str = card_ref.partition(":")
    if card_type != "task":
        return jsonify({"success": False, "error": "Only task cards can be deleted"}), 403

    try:
        entity_id = int(entity_id_str)
    except ValueError:
        return jsonify({"success": False, "error": "Invalid entity ID"}), 400

    from app.models.adm_kanban import KanbanCard

    card = db.session.get(KanbanCard, entity_id)
    if not card:
        return jsonify({"success": False, "error": "Card not found"}), 404

    db.session.delete(card)
    db.session.commit()

    return jsonify({"success": True, "deleted_id": card_ref})


@adm_kanban_v2_bp.route("/cards/<card_ref>/push-to-gantt", methods=["POST"])
@login_required
@audit_log("kanban_push_to_gantt")
def push_to_gantt(card_ref):
    """
    Create or update the work package (the one store) for this KanbanCard.
    Body (JSON): { target_start_date: "YYYY-MM-DD", target_end_date: "YYYY-MM-DD" }
    Returns: { success: True, work_package_id: int, gantt_url: str }
    """
    from app.models.adm_kanban import KanbanCard
    from app.services import work_package_service
    from app.utils.tenant import current_organization_id
    from datetime import datetime

    if not card_ref.startswith("task:"):
        return jsonify({"success": False, "error": "Invalid card ref"}), 400
    try:
        card_id = int(card_ref.split(":")[1])
    except (ValueError, TypeError, IndexError):
        return jsonify({"success": False, "error": "Invalid card ref"}), 400
    card = db.session.get(KanbanCard, card_id)
    if not card:
        return jsonify({"success": False, "error": "Card not found"}), 404

    data = request.get_json() or {}
    phase_code = card.adm_phase.code if card.adm_phase else "A"

    def _parse_date(s):
        if not s:
            return None
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            return None

    target_start = _parse_date(data.get("target_start_date"))
    target_end = _parse_date(data.get("target_end_date"))

    # Store dates on card
    if target_start:
        card.target_start_date = target_start
    if target_end:
        card.target_end_date = target_end

    # Map ADM priority to work package priority
    priority_map = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}
    wp_priority = priority_map.get(card.priority or "medium", "medium")

    # The work package lives in the one store (UnifiedWorkPackage). A card finds
    # it through its own unified_work_package_id; a card pushed before that
    # column existed still holds the retired roadmap store's id, which the merge
    # carried across (source_table / source_id).
    org_id = current_organization_id()
    existing = None
    if card.unified_work_package_id:
        existing = work_package_service.get_work_package(card.unified_work_package_id, org_id)
    if existing is None and card.work_package_id:
        existing = work_package_service.get_by_source(
            "roadmap_work_packages", card.work_package_id, org_id
        )
    if existing is not None:
        fields = {"name": card.title, "description": card.description or "", "priority": wp_priority}
        if target_start:
            fields["start_date"] = target_start
        if target_end:
            fields["end_date"] = target_end
        try:
            work_package_service.update_work_package(
                existing.id, organization_id=org_id, user_id=current_user.id, **fields
            )
        except work_package_service.WorkPackageError as exc:
            db.session.rollback()
            return jsonify({"success": False, "error": str(exc)}), 400
        card.unified_work_package_id = existing.id
        db.session.commit()
        return jsonify({"success": True, "work_package_id": existing.id, "updated": True,
                        "gantt_url": "/roadmap-builder"})

    try:
        wp = work_package_service.create_work_package(
            organization_id=org_id,
            user_id=current_user.id,
            name=card.title,
            description=card.description or "",
            business_capability=f"ADM Phase {phase_code} — {card.arch_domain or 'Business'}",
            start_date=target_start,
            end_date=target_end,
            priority=wp_priority,
            source_type="adm_kanban",
            source_id=card.id,
        )
    except work_package_service.WorkPackageError as exc:
        db.session.rollback()
        return jsonify({"success": False, "error": str(exc)}), 400
    card.unified_work_package_id = wp.id
    db.session.commit()

    return jsonify({"success": True, "work_package_id": wp.id, "updated": False,
                    "gantt_url": "/roadmap-builder"}), 201


@adm_kanban_v2_bp.route("/phases/<phase>/deliverables", methods=["GET"])
@login_required
def get_phase_deliverables(phase):
    """
    List TOGAF standard deliverables for an ADM phase with checked state.

    Query params: board_id (optional int)
    Response: { success, phase, deliverables: [...], total, checked }
    """
    from app.services.adm_deliverable_service import (
        get_phase_deliverables as _get,
        seed_deliverables,
    )

    board_id = request.args.get("board_id", type=int)
    seed_deliverables()  # idempotent — no-op if already seeded
    items = _get(phase.upper(), board_id=board_id)
    checked_count = sum(1 for i in items if i["checked"])
    return jsonify(
        {
            "success": True,
            "phase": phase.upper(),
            "deliverables": items,
            "total": len(items),
            "checked": checked_count,
        }
    )


@adm_kanban_v2_bp.route("/deliverables/<int:deliverable_id>/check", methods=["PATCH"])
@login_required
def check_deliverable(deliverable_id):
    """
    Toggle deliverable checked state for a board.

    Body: { "board_id": int|null, "checked": bool }
    Returns: { "success": true, "check": {...} }
    """
    from app.services.adm_deliverable_service import toggle_deliverable

    data = request.get_json() or {}
    checked = bool(data.get("checked", False))
    board_id = data.get("board_id") or None
    try:
        check = toggle_deliverable(deliverable_id, board_id, checked)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    return jsonify({"success": True, "check": check})


@adm_kanban_v2_bp.route("/metrics", methods=["GET"])
@login_required
def get_metrics():
    """
    Return cycle time metrics and SLA status per ADM phase and per column.

    Response:
        {
            "success": true,
            "phase_metrics": { "A": { avg_cycle_days, throughput, ... }, ... },
            "column_sla": { "review": { sla_days, overdue_count, status }, ... },
            "sla_thresholds": { "review": 5, "under_development": 14 }
        }
    """
    svc = KanbanProjectionService()
    metrics = svc.get_cycle_time_metrics()
    return jsonify({"success": True, **metrics})


@adm_kanban_v2_bp.route("/roadmap-timeline", methods=["GET"])
@login_required
def roadmap_timeline_all():
    """
    Return ALL KanbanCards across ALL boards in Platform.gantt canonical format.
    Groups by ADM phase. Used by /adm-kanban/roadmap page.
    Returns: { success, gantt:{groups,tasks,milestones}, config, stats }
    """
    from app.models.adm_kanban import KanbanCard, ADMPhase
    import calendar as _cal

    cards = KanbanCard.query.order_by(KanbanCard.created_at).all()  # model-safety-ok

    _DOMAIN_COLOURS = {
        "Business": "#3b82f6", "Application": "#8b5cf6",
        "Technology": "#06b6d4", "Data": "#f59e0b", "Cross-cutting": "#6b7280",
    }
    _STATUS_MAP = {
        "backlog": "planned", "todo": "planned", "in_progress": "in_progress",
        "review": "in_progress", "done": "completed",
    }
    all_phases = ADMPhase.query.order_by(ADMPhase.order).all()  # model-safety-ok

    tasks = []
    for card in cards:
        phase_code = card.adm_phase.code if card.adm_phase else "A"
        start = card.target_start_date or (card.started_at.date() if card.started_at else None)
        end = card.target_end_date or (card.completed_at.date() if card.completed_at else None)
        if start and not end:
            import datetime
            end = datetime.date(start.year, start.month,
                                _cal.monthrange(start.year, start.month)[1])
        domain = card.arch_domain or "Business"
        tasks.append({
            "id": f"task:{card.id}",
            "name": card.title,
            "group": phase_code,
            "start_date": start.strftime("%Y-%m-%d") if start else None,
            "end_date": end.strftime("%Y-%m-%d") if end else None,
            "status": _STATUS_MAP.get(card.status or "todo", "planned"),
            "priority": card.priority or "medium",
            "description": card.description or "",
            "dependencies": card.depends_on or [],
            "color": _DOMAIN_COLOURS.get(domain, "#6b7280"),
            "metadata": {
                "arch_domain": domain,
                "adm_phase": phase_code,
                "board_id": card.board_id,
                "work_package_id": card.unified_work_package_id,
            },
        })

    used_phases = {t["group"] for t in tasks}
    groups = [
        {"id": p.code, "label": f"Phase {p.code} — {p.name}"}
        for p in all_phases if p.code in used_phases
    ]
    if tasks and not groups:
        groups = [{"id": "A", "label": "Phase A — Architecture Vision"}]

    config = {
        "groupLabel": "ADM Phase",
        "statusColors": {
            "planned":     {"fill": "#6b7280", "bg": "#f3f4f6", "text": "#374151"},
            "in_progress": {"fill": "#3b82f6", "bg": "#eff6ff", "text": "#1d4ed8"},
            "completed":   {"fill": "#10b981", "bg": "#ecfdf5", "text": "#065f46"},
        },
        "features": {"export": ["csv"]},
    }
    return jsonify({
        "success": True,
        "gantt": {"groups": groups, "tasks": tasks, "milestones": []},
        "config": config,
        "stats": [
            {"label": "Total tasks",  "value": len(tasks)},
            {"label": "Scheduled",    "value": sum(1 for t in tasks if t["start_date"])},
            {"label": "Completed",    "value": sum(1 for t in tasks if t["status"] == "completed")},
        ],
    })


@adm_kanban_v2_bp.route("/boards/<int:board_id>/roadmap-timeline", methods=["GET"])
@login_required
def roadmap_timeline(board_id):
    """
    Return KanbanCards in Platform.gantt canonical format for the ADM Roadmap Timeline.
    Groups by ADM phase; uses target_start/end dates with started_at/completed_at fallback.
    Returns: { success, gantt:{groups,tasks,milestones}, config, stats }
    """
    from app.models.adm_kanban import KanbanCard, KanbanBoard, ADMPhase

    board = db.session.get(KanbanBoard, board_id)
    if not board:
        return jsonify({"success": False, "error": "Board not found"}), 404

    cards = KanbanCard.query.filter_by(board_id=board_id).order_by(KanbanCard.created_at).all()  # model-safety-ok

    _DOMAIN_COLOURS = {
        "Business": "#3b82f6",
        "Application": "#8b5cf6",
        "Technology": "#06b6d4",
        "Data": "#f59e0b",
        "Cross-cutting": "#6b7280",
    }

    _STATUS_MAP = {
        "backlog": "planned", "todo": "planned", "in_progress": "in_progress",
        "review": "in_progress", "done": "completed",
    }

    # Build groups from phases (only phases that have at least one card)
    all_phases = ADMPhase.query.order_by(ADMPhase.order).all()  # model-safety-ok

    tasks = []
    __import__("datetime").date.today()
    for card in cards:
        phase_code = card.adm_phase.code if card.adm_phase else "A"
        start = card.target_start_date or (card.started_at.date() if card.started_at else None)
        end = card.target_end_date or (card.completed_at.date() if card.completed_at else None)
        if start and not end:
            end = __import__("datetime").date(start.year, start.month, 1)
            import calendar
            end = __import__("datetime").date(start.year, start.month,
                                               calendar.monthrange(start.year, start.month)[1])
        domain = card.arch_domain or "Business"
        tasks.append({
            "id": f"task:{card.id}",
            "name": card.title,
            "group": phase_code,
            "start_date": start.strftime("%Y-%m-%d") if start else None,
            "end_date": end.strftime("%Y-%m-%d") if end else None,
            "status": _STATUS_MAP.get(card.status or "todo", "planned"),
            "priority": card.priority or "medium",
            "description": card.description or "",
            "dependencies": card.depends_on or [],
            "color": _DOMAIN_COLOURS.get(domain, "#6b7280"),
            "metadata": {
                "arch_domain": domain,
                "adm_phase": phase_code,
                "work_package_id": card.unified_work_package_id,
                "ref": card.card_ref if hasattr(card, "card_ref") else str(card.id),
            },
        })

    used_phases = {t["group"] for t in tasks}
    groups = [
        {"id": p.code, "label": f"Phase {p.code} — {p.name}"}
        for p in all_phases if p.code in used_phases
    ]

    # Ungrouped fallback group
    if tasks and not groups:
        groups = [{"id": "A", "label": "Phase A — Architecture Vision"}]

    config = {
        "groupLabel": "ADM Phase",
        "statusColors": {
            "planned": {"fill": "#6b7280", "bg": "#f3f4f6", "text": "#374151"},
            "in_progress": {"fill": "#3b82f6", "bg": "#eff6ff", "text": "#1d4ed8"},
            "completed": {"fill": "#10b981", "bg": "#ecfdf5", "text": "#065f46"},
        },
        "features": {"export": ["csv"]},
    }

    return jsonify({
        "success": True,
        "gantt": {"groups": groups, "tasks": tasks, "milestones": []},
        "config": config,
        "stats": [
            {"label": "Total tasks", "value": len(tasks)},
            {"label": "Scheduled", "value": sum(1 for t in tasks if t["start_date"])},
            {"label": "Completed", "value": sum(1 for t in tasks if t["status"] == "completed")},
        ],
        "board_id": board_id,
    })


# ---------------------------------------------------------------------------
# SA-003 — ADM phase → ArchiMate element guidance
# ---------------------------------------------------------------------------

@adm_kanban_v2_bp.route("/phases/<phase>/suggested-elements", methods=["GET"])
@login_required
def get_suggested_elements(phase):
    """
    Return the canonical ArchiMate element types suggested for an ADM phase.

    Path params:
        phase: ADM phase code (A-H)
    Query params:
        board_id: optional int (reserved for future scoping)
    Returns:
        { "success": true, "phase": "B", "elements": [{"type":…,"layer":…,"description":…}] }
    """
    from app.services.adm_phase_element_guide_service import get_suggested_elements_for_phase

    phase_upper = phase.upper()
    elements = get_suggested_elements_for_phase(phase_upper)
    return jsonify({"success": True, "phase": phase_upper, "elements": elements})


@adm_kanban_v2_bp.route("/phases/<phase>/completion", methods=["GET"])
@login_required
def get_phase_element_completion(phase):
    """
    Return element-creation completion score for an ADM phase.

    Path params:
        phase: ADM phase code (A-H)
    Query params:
        board_id: optional int (reserved for future scoping)
    Returns:
        { "success": true, "phase": "A", "total_suggested": 4, "created": 2, "pct": 50 }
    """
    from app.services.adm_phase_element_guide_service import get_phase_completion_score

    phase_upper = phase.upper()
    board_id = request.args.get("board_id", type=int)
    score = get_phase_completion_score(phase_upper, board_id)
    return jsonify({"success": True, "phase": phase_upper, **score})


@adm_kanban_v2_bp.route("/suggestions/requirements", methods=["GET"])
@login_required
def suggestions_requirements():
    from app.models.archimate_core import ArchiMateElement
    q = request.args.get('q', '').strip()
    try:
        query = ArchiMateElement.query.filter(
            ArchiMateElement.type == "Requirement",
            ArchiMateElement.name.isnot(None),
            ArchiMateElement.name != "",
        )
        if len(q) >= 2:
            query = query.filter(ArchiMateElement.name.ilike(f'%{q}%'))
        items = query.order_by(ArchiMateElement.name).limit(20).all()
        results = [{'id': r.id, 'label': r.name, 'ref': f'REQ-{r.id:03d}'} for r in items]
    except Exception:
        current_app.logger.exception("suggestions_requirements failed")
        return jsonify({'success': False, 'error': 'Could not search requirements'}), 500
    return jsonify({'success': True, 'results': results})


@adm_kanban_v2_bp.route("/suggestions/goals", methods=["GET"])
@login_required
def suggestions_goals():
    from app.models.archimate_core import ArchiMateElement
    q = request.args.get('q', '').strip()
    try:
        query = ArchiMateElement.query.filter(
            ArchiMateElement.type.in_(["Goal", "Outcome"]),
            ArchiMateElement.name.isnot(None),
            ArchiMateElement.name != "",
        )
        if len(q) >= 2:
            query = query.filter(ArchiMateElement.name.ilike(f'%{q}%'))
        items = query.order_by(ArchiMateElement.name).limit(20).all()
        results = [{'id': r.id, 'label': r.name, 'ref': f'GOAL-{r.id:03d}'} for r in items]
    except Exception:
        current_app.logger.exception("suggestions_goals failed")
        return jsonify({'success': False, 'error': 'Could not search goals'}), 500
    return jsonify({'success': True, 'results': results})


@adm_kanban_v2_bp.route("/suggestions/drivers", methods=["GET"])
@login_required
def suggestions_drivers():
    from app.models.archimate_core import ArchiMateElement
    q = request.args.get('q', '').strip()
    try:
        query = ArchiMateElement.query.filter(
            ArchiMateElement.type == "Driver",
            ArchiMateElement.name.isnot(None),
            ArchiMateElement.name != "",
        )
        if len(q) >= 2:
            query = query.filter(ArchiMateElement.name.ilike(f'%{q}%'))
        items = query.order_by(ArchiMateElement.name).limit(20).all()
        results = [{'id': r.id, 'label': r.name, 'ref': f'DRV-{r.id:03d}'} for r in items]
    except Exception:
        current_app.logger.exception("suggestions_drivers failed")
        return jsonify({'success': False, 'error': 'Could not search drivers'}), 500
    return jsonify({'success': True, 'results': results})


@adm_kanban_v2_bp.route("/suggestions/principles", methods=["GET"])
@login_required
def suggestions_principles():
    try:
        from app.models.motivation_extended import Principle
    except ImportError:
        try:
            from app.models.motivation import Principle
        except ImportError:
            current_app.logger.exception("suggestions_principles: no Principle model available")
            return jsonify({'success': False, 'error': 'Principle search is unavailable'}), 500
    q = request.args.get('q', '').strip()
    try:
        query = Principle.query
        if len(q) >= 2:
            query = query.filter(Principle.name.ilike(f'%{q}%'))
        items = query.limit(20).all()
        results = [{'id': r.id, 'label': r.name, 'ref': f'PRIN-{r.id:03d}'} for r in items]
    except Exception:
        current_app.logger.exception("suggestions_principles failed")
        return jsonify({'success': False, 'error': 'Could not search principles'}), 500
    return jsonify({'success': True, 'results': results})


@adm_kanban_v2_bp.route("/suggestions/users", methods=["GET"])
@login_required
def suggestions_users():
    from app.models import User
    q = request.args.get('q', '').strip()
    try:
        query = User.query.filter(User.confirmed == True, User.organization_id == g.current_org_id)  # noqa: E712
        if len(q) >= 1:
            query = query.filter(
                db.or_(
                    User.first_name.ilike(f'%{q}%'),
                    User.last_name.ilike(f'%{q}%'),
                    User.email.ilike(f'%{q}%'),
                )
            )
        items = query.order_by(User.first_name, User.last_name).limit(30).all()
        results = []
        for u in items:
            name = ' '.join(filter(None, [u.first_name, u.last_name])).strip() or u.email
            results.append({'id': u.id, 'label': name, 'email': u.email})
    except Exception:
        current_app.logger.exception("suggestions_users failed")
        return jsonify({'success': False, 'error': 'Could not search users'}), 500
    return jsonify({'success': True, 'results': results})


@adm_kanban_v2_bp.route("/config", methods=["GET"])
@login_required
def get_config():
    """Return board configuration (columns, phases, WIP limits)."""
    return jsonify(
        {
            "success": True,
            "columns": COLUMNS,
            "phases": ADM_PHASES,
            "card_types": [
                {"id": "solution", "label": "Solutions"},
                {"id": "deliverable", "label": "Deliverables"},
                {"id": "task", "label": "Tasks"},
            ],
            "wip_limits": WIP_LIMITS,
        }
    )
