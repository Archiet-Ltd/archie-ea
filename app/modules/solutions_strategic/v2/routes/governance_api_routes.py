"""
Phase 5A & 5B API Routes: Governance, execution tracking, issues, learning.
"""

import logging
import uuid
from datetime import datetime
from flask import Blueprint, g, request, jsonify
from flask_login import current_user, login_required
from functools import wraps

_log = logging.getLogger(__name__)

from app import db
from app.models.solution_models import Solution
from app.models.solution_governance import SolutionNotification
from app.modules.solutions_strategic.v2.services.solution_versioning_service import SolutionVersioningService
from app.modules.solutions_strategic.v2.services.execution_tracking_service import ExecutionTrackingService
from app.modules.solutions_strategic.v2.services.solution_issue_service import SolutionIssueService
from app.modules.solutions_strategic.v2.services.solution_arb_service import SolutionARBService
from app.modules.solutions_strategic.v2.services.solution_learning_service import SolutionLearningService

governance_api_bp = Blueprint('governance_api', __name__, url_prefix='/api')

# Initialize services
versioning_service = SolutionVersioningService()
execution_service = ExecutionTrackingService()
issue_service = SolutionIssueService()
arb_service = SolutionARBService()
learning_service = SolutionLearningService()


def _current_org_id():
    """Tenant of the authenticated session, never a caller-supplied value."""
    org_id = getattr(g, 'current_org_id', None)
    if not isinstance(org_id, int) or org_id <= 0:
        org_id = getattr(current_user, 'organization_id', None)
    return org_id if isinstance(org_id, int) and org_id > 0 else None


def solution_required(f):
    """Resolve the solution with an explicit (id, organization_id) predicate.

    ``Session.get``/``Query.get`` are tenant-filtered only on an identity-map
    miss, so a warm session could hand back another tenant's row.  The explicit
    predicate always emits SQL and always carries the tenant.
    """
    @wraps(f)
    def decorated_function(solution_id, *args, **kwargs):
        org_id = _current_org_id()
        solution = None
        if org_id is not None:
            solution = db.session.execute(
                db.select(Solution).where(
                    Solution.id == solution_id,
                    Solution.organization_id == org_id,
                )
            ).scalar_one_or_none()
        if not solution:
            # Same response for missing and foreign: never confirm existence.
            return jsonify({'error': 'Solution not found'}), 404
        return f(solution_id, *args, **kwargs)
    return decorated_function


def _notify_if_pref(user_id, pref_key, notification_type, message, solution_id=None):
    """PLT-017: Create SolutionNotification only if user has that preference enabled."""
    if not user_id:
        return
    try:
        from app.models.user import User
        target_user = db.session.get(User, user_id)
        if target_user and not target_user.get_notification_preference(pref_key):
            return
    except Exception as exc:
        _log.debug("Could not check notification preference for user %s: %s", user_id, exc)
    try:
        n = SolutionNotification(
            solution_id=solution_id,
            user_id=user_id,
            type=notification_type,
            message=message,
        )
        db.session.add(n)
    except Exception as exc:
        _log.debug("Could not create notification: %s", exc)


# ============================================================================
# VERSIONING ENDPOINTS
# ============================================================================

@governance_api_bp.route('/solutions/<int:solution_id>/versions', methods=['GET'])
@login_required
@solution_required
def get_version_history(solution_id):
    """Get version history for solution."""
    try:
        history = versioning_service.get_version_history(solution_id)
        return jsonify({'versions': history}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/versions/create', methods=['POST'])
@login_required
@solution_required
def create_new_version(solution_id):
    """Create new version of solution."""
    data = request.get_json()
    
    try:
        version = versioning_service.create_new_version(
            solution_id=solution_id,
            created_by_id=data.get('created_by_id'),
            change_summary=data.get('change_summary'),
            change_delta=data.get('change_delta'),
            solution_snapshot=data.get('solution_snapshot')
        )
        return jsonify(version.to_dict()), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/versions/<int:version_id>/diff', methods=['GET'])
@login_required
@solution_required
def get_version_diff(solution_id, version_id):
    """Get diff for specific version."""
    compare_to = request.args.get('vs', type=int)
    
    try:
        diff = versioning_service.get_version_diff(version_id, compare_to)
        return jsonify(diff), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/versions/<int:version_id>/approve', methods=['POST'])
@login_required
@solution_required
def approve_version(solution_id, version_id):
    """Approve a version."""
    data = request.get_json()

    try:
        version = versioning_service.approve_version(
            version_id=version_id,
            approved_by_id=data.get('approved_by_id'),
            approval_notes=data.get('approval_notes'),
            conditions=data.get('conditions')
        )
        # PLT-014: Notify solution owner of version approval
        solution = db.session.execute(db.select(Solution).where(
            Solution.id == solution_id,
            Solution.organization_id == getattr(g, 'current_org_id', None),
        )).scalar_one_or_none()  # explicit predicate: .get() skips the tenant filter on an identity-map hit
        if solution and getattr(solution, 'created_by_id', None):
            _notify_if_pref(
                user_id=solution.created_by_id,
                pref_key='arb_decisions',  # secrets-safety-ok
                notification_type='arb_submission',
                message=f"Version v{getattr(version, 'version_number', version_id)} of solution '{solution.name}' has been approved.",
                solution_id=solution_id,
            )
            db.session.commit()
        return jsonify(version.to_dict()), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/versions/<int:version_id>/reject', methods=['POST'])
@login_required
@solution_required
def reject_version(solution_id, version_id):
    """Reject a version."""
    data = request.get_json()

    try:
        version = versioning_service.reject_version(
            version_id=version_id,
            rejection_reason=data.get('rejection_reason')
        )
        # PLT-014: Notify solution owner of version rejection
        solution = db.session.execute(db.select(Solution).where(
            Solution.id == solution_id,
            Solution.organization_id == getattr(g, 'current_org_id', None),
        )).scalar_one_or_none()  # explicit predicate: .get() skips the tenant filter on an identity-map hit
        if solution and getattr(solution, 'created_by_id', None):
            _notify_if_pref(
                user_id=solution.created_by_id,
                pref_key='arb_decisions',  # secrets-safety-ok
                notification_type='arb_submission',
                message=f"Version v{getattr(version, 'version_number', version_id)} of solution '{solution.name}' has been rejected.",
                solution_id=solution_id,
            )
            db.session.commit()
        return jsonify(version.to_dict()), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/versions/auto-generate', methods=['POST'])
@login_required
@solution_required
def auto_generate_version(solution_id):
    """Auto-generate next version from feedback."""
    data = request.get_json()
    
    try:
        version = versioning_service.auto_generate_next_version(
            solution_id=solution_id,
            created_by_id=data.get('created_by_id'),
            feedback_items=data.get('feedback_items', [])
        )
        return jsonify(version.to_dict()), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/approval-matrix', methods=['GET'])
@login_required
@solution_required
def get_approval_matrix(solution_id):
    """Get approval matrix for solution."""
    try:
        matrix = versioning_service.get_approval_matrix(solution_id)
        return jsonify(matrix), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# ============================================================================
# EXECUTION TRACKING ENDPOINTS
# ============================================================================

@governance_api_bp.route('/solutions/<int:solution_id>/execution/initialize', methods=['POST'])
@login_required
@solution_required
def initialize_execution_tracking(solution_id):
    """Initialize execution tracking for tasks."""
    data = request.get_json()
    
    try:
        tracking = execution_service.initialize_tracking(
            solution_id=solution_id,
            workflow_task_id=data.get('workflow_task_id'),
            planned_duration_days=data.get('planned_duration_days'),
            planned_end_date=datetime.fromisoformat(data['planned_end_date']).date() if data.get('planned_end_date') else None
        )
        return jsonify(tracking.to_dict()), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/execution/task/<int:tracking_id>/update', methods=['POST'])
@login_required
@solution_required
def update_task_progress(solution_id, tracking_id):
    """Update task progress."""
    data = request.get_json()
    
    try:
        tracking = execution_service.update_progress(
            tracking_id=tracking_id,
            percent_complete=data.get('percent_complete', 0),
            updated_by_id=data.get('updated_by_id'),
            status_reason=data.get('status_reason'),
            actual_start_date=datetime.fromisoformat(data['actual_start_date']).date() if data.get('actual_start_date') else None,
            actual_end_date=datetime.fromisoformat(data['actual_end_date']).date() if data.get('actual_end_date') else None
        )
        return jsonify(tracking.to_dict()), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/execution/task/<int:tracking_id>/realize-risk', methods=['POST'])
@login_required
@solution_required
def realize_risk(solution_id, tracking_id):
    """Record that a risk materialized."""
    data = request.get_json()
    
    try:
        tracking = execution_service.realize_risk(
            tracking_id=tracking_id,
            risk_id=data.get('risk_id'),
            impact_description=data.get('impact_description'),
            updated_by_id=data.get('updated_by_id')
        )
        return jsonify(tracking.to_dict()), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/execution/variance-report', methods=['GET'])
@login_required
@solution_required
def get_variance_report(solution_id):
    """Get variance report for solution."""
    try:
        report = execution_service.get_variance_report(solution_id)
        return jsonify(report), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/execution/dashboard', methods=['GET'])
@login_required
@solution_required
def get_execution_dashboard(solution_id):
    """Get execution dashboard summary."""
    try:
        summary = execution_service.get_dashboard_summary(solution_id)
        return jsonify(summary), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# ============================================================================
# ISSUE TRACKING ENDPOINTS
# ============================================================================

@governance_api_bp.route('/solutions/<int:solution_id>/issues/create', methods=['POST'])
@login_required
@solution_required
def create_issue(solution_id):
    """Create new issue."""
    data = request.get_json()
    
    try:
        issue = issue_service.create_issue(
            solution_id=solution_id,
            title=data.get('title'),
            description=data.get('description'),
            severity=data.get('severity', 'P3'),
            created_by_id=data.get('created_by_id'),
            category=data.get('category'),
            workflow_task_id=data.get('workflow_task_id'),
            impact_area=data.get('impact_area'),
            target_resolution_date=datetime.fromisoformat(data['target_resolution_date']) if data.get('target_resolution_date') else None
        )
        return jsonify(issue.to_dict()), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/issues', methods=['GET'])
@login_required
@solution_required
def get_open_issues(solution_id):
    """Get all open issues for solution."""
    try:
        issues = issue_service.get_open_issues(solution_id)
        return jsonify({'issues': issues}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/issues/<int:issue_id>/assign', methods=['POST'])
@login_required
@solution_required
def assign_issue(solution_id, issue_id):
    """Assign issue to user."""
    data = request.get_json()
    
    try:
        issue = issue_service.assign_issue(
            issue_id=issue_id,
            assigned_to_id=data.get('assigned_to_id'),
            organization_id=_current_org_id(),
            solution_id=solution_id
        )
        return jsonify(issue.to_dict()), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/issues/<int:issue_id>/escalate', methods=['POST'])
@login_required
@solution_required
def escalate_issue(solution_id, issue_id):
    """Escalate issue."""
    data = request.get_json()

    try:
        issue = issue_service.escalate_issue(
            issue_id=issue_id,
            escalated_to_id=data.get('escalated_to_id'),
            escalation_reason=data.get('escalation_reason'),
            organization_id=_current_org_id(),
            solution_id=solution_id
        )
        return jsonify(issue.to_dict()), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/issues/<int:issue_id>/resolve', methods=['POST'])
@login_required
@solution_required
def resolve_issue(solution_id, issue_id):
    """Mark issue as resolved."""
    data = request.get_json()

    try:
        issue = issue_service.resolve_issue(
            issue_id=issue_id,
            resolved_by_id=current_user.id,
            resolution_notes=data.get('resolution_notes'),
            solution_id=solution_id
        )
        return jsonify(issue.to_dict()), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/issues/summary', methods=['GET'])
@login_required
@solution_required
def get_issue_summary(solution_id):
    """Get issue summary dashboard."""
    try:
        summary = issue_service.get_issue_summary(solution_id)
        return jsonify(summary), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/issues/blockers', methods=['GET'])
@login_required
@solution_required
def get_blockers(solution_id):
    """Get blocking issues analysis."""
    try:
        blockers = issue_service.get_blockers_by_date(solution_id)
        return jsonify(blockers), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# ============================================================================
# ARB INTEGRATION ENDPOINTS
# ============================================================================

@governance_api_bp.route('/solutions/<int:solution_id>/arb/submit', methods=['POST'])
@login_required
@solution_required
def submit_for_arb(solution_id):
    """Submit solution to ARB for review."""
    from app.modules.transformation_room.arb_submission_adapter import (
        TypedARBSubmissionAdapter,
    )

    data = request.get_json(silent=True) or {}
    result = TypedARBSubmissionAdapter.submit_solution_from_request(
        solution_id=solution_id,
        payload=data,
    )
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    if not result.success:
        return jsonify({"success": False, "reason_codes": result.reason_codes,
                        "missing_evidence": result.missing_evidence,
                        "request_id": request_id}), result.http_status
    return jsonify({"success": True, "review_id": result.review_item_id,
                    "review_item_id": result.review_item_id,
                    "review_number": result.review_number,
                    "evidence_id": result.snapshot_id,
                    "snapshot_id": result.snapshot_id,
                    "idempotent": result.idempotent,
                    "review_cycle_id": result.review_cycle_id,
                    "cycle_number": result.cycle_number,
                    "subject_type": result.subject_type,
                    "subject_id": result.subject_id,
                    "status": result.status,
                    "canonical_url": result.canonical_url,
                    "redirect_url": f"/arb/reviews/{result.review_item_id}",
                    "request_id": request_id}), 201 if not result.idempotent else 200


@governance_api_bp.route(
    '/arb/subjects/<subject_type>/<int:subject_id>/submit', methods=['POST']
)
@login_required
def submit_typed_subject_for_arb(subject_type, subject_id):
    """Submit a persisted typed ARB subject (ADR / Architecture Model).

    This is the first live ingress for the typed submission command for these
    two subjects.  Only a real, tenant-scoped subject row can be submitted; the
    request body contributes nothing but the ``human_reviewed`` assertion.
    """
    from app.modules.transformation_room.arb_typed_subject_ingress import (
        TypedARBSubjectIngress,
    )

    result = TypedARBSubjectIngress.submit_from_request(
        subject_type=subject_type,
        subject_id=subject_id,
        payload=request.get_json(silent=True) or {},
    )
    if not result.success:
        return jsonify(result.failure_payload()), result.http_status
    return jsonify(result.success_payload()), result.http_status


@governance_api_bp.route('/solutions/<int:solution_id>/arb/status', methods=['GET'])
@login_required
@solution_required
def get_arb_status(solution_id):
    """Get ARB status for solution."""
    try:
        status = arb_service.get_arb_status(solution_id)
        return jsonify(status), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/arb/<int:review_id>/record-decision', methods=['POST'])
@login_required
@solution_required
def record_arb_decision(solution_id, review_id):
    """Record ARB decision."""
    data = request.get_json(silent=True) or {}
    from app.modules.transformation_room.arb_decision_adapter import (
        TypedARBDecisionAdapter,
    )

    typed_result = TypedARBDecisionAdapter.decide_solution_from_request(
        solution_id=solution_id,
        review_item_id=review_id,
        payload=data,
    )
    if typed_result.typed:
        if not typed_result.success:
            return jsonify({
                "success": False,
                "reason_codes": typed_result.reason_codes,
            }), typed_result.http_status
        from app.models.architecture_review_board import ARBReviewCycle, ARBReviewItem
        from app.models.arb_decision_event import ARBDecisionEvent

        cycle = db.session.execute(
            db.select(ARBReviewCycle).where(
                ARBReviewCycle.id == typed_result.review_cycle_id,
                ARBReviewCycle.organization_id == g.current_org_id,
                ARBReviewCycle.subject_type == "solution",
                ARBReviewCycle.subject_id == solution_id,
                ARBReviewCycle.solution_id == solution_id,
            )
        ).scalar_one()
        event = db.session.execute(
            db.select(ARBDecisionEvent).where(
                ARBDecisionEvent.id == typed_result.decision_event_id,
                ARBDecisionEvent.organization_id == g.current_org_id,
                ARBDecisionEvent.review_cycle_id == cycle.id,
                ARBDecisionEvent.solution_id == solution_id,
            )
        ).scalar_one()
        review = db.session.execute(
            db.select(ARBReviewItem).where(
                ARBReviewItem.id == typed_result.review_item_id,
                ARBReviewItem.organization_id == g.current_org_id,
                ARBReviewItem.review_cycle_id == cycle.id,
                ARBReviewItem.subject_type == "solution",
                ARBReviewItem.subject_id == solution_id,
                ARBReviewItem.solution_id == solution_id,
            )
        ).scalar_one()
        response = {
            "success": True,
            "id": typed_result.review_item_id,
            "solution_id": solution_id,
            "submitted_at": review.submitted_at.isoformat()
            if review.submitted_at
            else None,
            "arb_decision": typed_result.outcome,
            "decided_at": event.created_at.isoformat() if event.created_at else None,
            "arb_attendees": [],
            "compliance_areas_reviewed": [],
            "next_steps": None,
            "next_review_date": None,
            "review_id": typed_result.review_item_id,
            "review_item_id": typed_result.review_item_id,
            "review_cycle_id": typed_result.review_cycle_id,
            "decision_event_id": typed_result.decision_event_id,
            "condition_ids": typed_result.condition_ids,
            "status": typed_result.status,
            "outcome": typed_result.outcome,
            "conditions": typed_result.conditions,
            "idempotent": typed_result.idempotent,
        }
        if not typed_result.idempotent:
            solution = db.session.execute(
                db.select(Solution).where(
                    Solution.id == solution_id,
                    Solution.organization_id == g.current_org_id,
                )
            ).scalar_one()
            if solution.created_by_id:
                _notify_if_pref(
                    user_id=solution.created_by_id,
                    pref_key="arb_decisions",  # secrets-safety-ok
                    notification_type="arb_submission",
                    message=(
                        f"ARB decision for solution '{solution.name}': "
                        f"{typed_result.outcome}."
                    ),
                    solution_id=solution_id,
                )
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    _log.exception(
                        "Could not persist typed ARB owner notification for solution %s",
                        solution_id,
                    )
        return jsonify(response), 200

    if not TypedARBDecisionAdapter.legacy_solution_review_matches_request(
        solution_id=solution_id,
        review_item_id=review_id,
    ):
        return jsonify({"error": f"ARB review {review_id} not found"}), 404

    try:
        # The actor is the authenticated session, never `decided_by_id`.
        review = arb_service.record_arb_decision(
            review_id=review_id,
            solution_id=solution_id,
            decision=data.get('decision'),
            decision_reason=data.get('decision_reason'),
            conditions=data.get('conditions'),
            compliance_notes=data.get('compliance_notes')
        )
        # PLT-014: Notify solution owner of ARB decision
        decision = data.get('decision', 'recorded')
        solution = db.session.execute(
            db.select(Solution).where(
                Solution.id == solution_id,
                Solution.organization_id == _current_org_id(),
            )
        ).scalar_one_or_none()
        if solution and getattr(solution, 'created_by_id', None):
            _notify_if_pref(
                user_id=solution.created_by_id,
                pref_key='arb_decisions',  # secrets-safety-ok
                notification_type='arb_submission',
                message=f"ARB decision for solution '{solution.name}': {decision}.",
                solution_id=solution_id,
            )
            db.session.commit()
        return jsonify(review.to_dict()), 200
    except PermissionError:
        return jsonify({
            'success': False,
            'reason_codes': ['actor_not_authorized'],
            'request_id': request.headers.get('X-Request-ID') or str(uuid.uuid4()),
        }), 403
    except LookupError:
        return jsonify({
            'success': False,
            'reason_codes': ['arb_review_not_found'],
            'request_id': request.headers.get('X-Request-ID') or str(uuid.uuid4()),
        }), 404
    except ValueError:
        db.session.rollback()
        return jsonify({
            'success': False,
            'reason_codes': ['invalid_decision'],
            'request_id': request.headers.get('X-Request-ID') or str(uuid.uuid4()),
        }), 422


@governance_api_bp.route('/solutions/<int:solution_id>/arb/<int:review_id>/compliance', methods=['POST'])
@login_required
@solution_required
def add_compliance_review(solution_id, review_id):
    """Add compliance review assessment."""
    data = request.get_json()
    
    try:
        review = arb_service.add_compliance_review(
            review_id=review_id,
            compliance_areas=data.get('compliance_areas', []),
            compliance_notes=data.get('compliance_notes', {})
        )
        return jsonify(review.to_dict()), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/arb/compliance-trail', methods=['GET'])
@login_required
@solution_required
def get_compliance_trail(solution_id):
    """Get full compliance trail for solution."""
    try:
        trail = arb_service.get_compliance_trail(solution_id)
        return jsonify({'trail': trail}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# ============================================================================
# LEARNING & OUTCOMES ENDPOINTS
# ============================================================================

@governance_api_bp.route('/solutions/<int:solution_id>/outcomes/record', methods=['POST'])
@login_required
@solution_required
def record_project_completion(solution_id):
    """Record project completion with outcomes."""
    data = request.get_json() or {}

    # Actor is the authenticated session ONLY. This took recorded_by_id straight
    # from the request body with no session fallback at all, so a caller could
    # attribute a completion record to any user -- the same class as the
    # decided_by_id hole closed elsewhere in this file.
    recorded_by = getattr(current_user, 'id', None)
    if not recorded_by:
        return jsonify({'success': False, 'reason_codes': ['actor_not_authorized']}), 403

    # A missing go-live date defaulted to utcnow(), inventing a completion date
    # indistinguishable from a recorded one. It is required.
    raw_go_live = data.get('go_live_date')
    if not raw_go_live:
        return jsonify({'success': False, 'reason_codes': ['go_live_date_required']}), 400
    try:
        go_live_date = datetime.fromisoformat(str(raw_go_live).replace('Z', '+00:00'))
    except ValueError:
        return jsonify({'success': False, 'reason_codes': ['go_live_date_invalid']}), 400

    try:
        outcome = learning_service.record_project_completion(
            solution_id=solution_id,
            go_live_date=go_live_date,
            recorded_by_id=recorded_by,
            predicted_duration_weeks=data.get('predicted_duration_weeks'),
            actual_duration_weeks=data.get('actual_duration_weeks'),
            predicted_cost_usd=data.get('predicted_cost_usd'),
            actual_cost_usd=data.get('actual_cost_usd')
        )
        # Notify solution owner (ENT-020) — PLT-017: check arb_decisions preference
        solution = db.session.execute(db.select(Solution).where(
            Solution.id == solution_id,
            Solution.organization_id == getattr(g, 'current_org_id', None),
        )).scalar_one_or_none()  # explicit predicate: .get() skips the tenant filter on an identity-map hit
        if solution and getattr(solution, 'created_by_id', None):
            _notify_if_pref(
                user_id=solution.created_by_id,
                pref_key='arb_decisions',  # secrets-safety-ok  # secrets-safety-ok
                notification_type='outcome_recorded',
                message=f"Project outcome recorded for solution '{getattr(solution, 'name', 'Solution')}'.",
                solution_id=solution_id,
            )
            db.session.commit()
        return jsonify(outcome.to_dict()), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/outcomes/vendor-performance', methods=['POST'])
@login_required
@solution_required
def record_vendor_performance(solution_id):
    """Record vendor performance ratings."""
    data = request.get_json()
    
    try:
        outcome = learning_service.record_vendor_performance(
            outcome_id=data.get('outcome_id'),
            vendor_performance=data.get('vendor_performance', {})
        )
        return jsonify(outcome.to_dict()), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/outcomes/risk-analysis', methods=['POST'])
@login_required
@solution_required
def record_risk_realization(solution_id):
    """Record risk realization analysis."""
    data = request.get_json()
    
    try:
        outcome = learning_service.record_risk_realization(
            outcome_id=data.get('outcome_id'),
            predicted_risks=data.get('predicted_risks', []),
            realized_risks=data.get('realized_risks', []),
            unforecast_risks=data.get('unforecast_risks', [])
        )
        return jsonify(outcome.to_dict()), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/outcomes/lessons-learned', methods=['POST'])
@login_required
@solution_required
def record_lessons_learned(solution_id):
    """Record lessons learned."""
    data = request.get_json()
    
    try:
        outcome = learning_service.record_lessons_learned(
            outcome_id=data.get('outcome_id'),
            lessons_learned=data.get('lessons_learned'),
            what_went_well=data.get('what_went_well'),
            what_to_improve=data.get('what_to_improve')
        )
        return jsonify(outcome.to_dict()), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# ENT-019: Single-call outcome + lessons and backtest/drift for solution detail UI
@governance_api_bp.route('/solutions/<int:solution_id>/record-outcome', methods=['POST'])
@login_required
@solution_required
def record_outcome_full(solution_id):
    """Record project outcome and lessons in one call (ENT-019)."""
    data = request.get_json() or {}
    go_live = data.get('go_live_date')
    if not go_live:
        return jsonify({'error': 'go_live_date is required'}), 400
    try:
        from datetime import datetime as dt
        if isinstance(go_live, str):
            go_live = dt.fromisoformat(go_live.replace('Z', '+00:00')).date() if 'T' in go_live else dt.strptime(go_live, '%Y-%m-%d').date()
    except Exception:
        return jsonify({'error': 'Invalid go_live_date'}), 400
    # Actor is the authenticated session ONLY. The previous `or data.get(
    # 'recorded_by_id')` fallback let a caller attribute a governance outcome
    # record to another user whenever current_user.id was falsy -- the same
    # class as the decided_by_id hole closed elsewhere in this file.
    recorded_by = getattr(current_user, 'id', None)
    if not recorded_by:
        return jsonify({'success': False, 'reason_codes': ['actor_not_authorized']}), 403
    try:
        outcome = learning_service.record_project_completion(
            solution_id=solution_id,
            go_live_date=go_live,
            recorded_by_id=recorded_by,
            predicted_duration_weeks=data.get('predicted_duration_weeks'),
            actual_duration_weeks=data.get('actual_duration_weeks'),
            predicted_cost_usd=data.get('predicted_cost_usd'),
            actual_cost_usd=data.get('actual_cost_usd')
        )
        if data.get('lessons_learned') or data.get('what_went_well') or data.get('what_to_improve'):
            learning_service.record_lessons_learned(
                outcome_id=outcome.id,
                lessons_learned=data.get('lessons_learned') or '',
                what_went_well=data.get('what_went_well') or '',
                what_to_improve=data.get('what_to_improve') or ''
            )
        if data.get('business_value_realized') is not None:
            learning_service.record_business_value(
                outcome_id=outcome.id,
                business_value_realized=data.get('business_value_realized') or '',
                estimated_business_value_usd=data.get('estimated_business_value_usd'),
                roi_percentage=data.get('roi_percentage')
            )
        return jsonify({'success': True, 'outcome_id': outcome.id, 'outcome': outcome.to_dict()}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/backtest-results', methods=['GET'])
@login_required
@solution_required
def get_solution_backtest_results(solution_id):
    """Get backtest/learning summary for a solution (ENT-019)."""
    try:
        summary = learning_service.get_learning_summary(solution_id)
        return jsonify(summary), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/solutions/<int:solution_id>/model-drift', methods=['GET'])
@login_required
@solution_required
def get_solution_model_drift(solution_id):
    """Get model drift analysis (ENT-019)."""
    try:
        drift = learning_service.detect_model_drift()
        return jsonify(drift), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/ai/model-accuracy', methods=['GET'])
@login_required
def get_model_accuracy():
    """Get overall AI model accuracy from outcomes."""
    try:
        accuracy = learning_service.calculate_model_accuracy()
        return jsonify(accuracy), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/ai/model-drift', methods=['GET'])
@login_required
def detect_drift():
    """Detect model drift (performance degradation)."""
    try:
        drift = learning_service.detect_model_drift()
        return jsonify(drift), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/ai/learning-summary', methods=['GET'])
@login_required
def get_learning_summary():
    """Get portfolio-wide learning summary."""
    solution_id = request.args.get('solution_id', type=int)
    
    try:
        summary = learning_service.get_learning_summary(solution_id)
        return jsonify(summary), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@governance_api_bp.route('/ai/retraining-queue', methods=['GET'])
@login_required
def get_retraining_queue():
    """Get outcomes ready for model retraining."""
    min_count = request.args.get('min_count', 10, type=int)

    try:
        queue = learning_service.get_outcomes_for_retraining(min_count)
        return jsonify({'retraining_queue': queue}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# ============================================================================
# CHIEF ARCHITECT — AI-7 board-ready synthesis
# ============================================================================

@governance_api_bp.route('/solutions/<int:solution_id>/chief-packet', methods=['GET'])
@login_required
@solution_required
def chief_architect_packet(solution_id):
    """Return the Chief Architect board-ready synthesis for a solution.

    Combines conformance review, latest ADR decision, and a synthesised verdict
    into one payload. Used by the solution detail page Chief Architect card and
    by Slack/Teams surfaces.
    """
    try:
        from app.modules.solutions_strategic.v2.services.chief_architect_service import (
            ChiefArchitectService,
        )
        packet = ChiefArchitectService.solution_packet(solution_id)
        status = 200 if packet.get("success") else 404
        return jsonify(packet), status
    except Exception as exc:
        _log.error("chief_architect_packet error for sol %s: %s", solution_id, exc)
        return jsonify({"success": False, "error": str(exc)}), 500

