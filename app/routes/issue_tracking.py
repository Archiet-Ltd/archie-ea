"""
Solution Issue Tracking API Routes (ent-08)
Handles CRUD operations for issue tracking and escalation
"""

import logging
from datetime import datetime
from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from werkzeug.exceptions import HTTPException

from app import db
from app.middleware.tenant_context import current_org_id
from app.models.solution_governance import SolutionIssue
from app.utils.tenant_users import user_in_org

logger = logging.getLogger(__name__)

# Create blueprint
issue_bp = Blueprint('issue_tracking', __name__, url_prefix='/api/solutions/<int:solution_id>/issues')


@issue_bp.route('', methods=['GET'])
@login_required
def list_issues(solution_id):
    """
    GET /api/solutions/<solution_id>/issues
    Returns all issues for a solution, with filtering and sorting
    """
    try:
        from app.models.solution_models import Solution
        from app.utils.route_guards import require_entity

        # Unknown solution -> 404, not an empty issue list.
        require_entity(Solution, solution_id, description="Solution not found")

        # Get query parameters
        status = request.args.get('status')
        priority = request.args.get('priority')
        assigned_to = request.args.get('assigned_to')
        sort_by = request.args.get('sort_by', 'created_at')
        sort_order = request.args.get('sort_order', 'desc')

        # Build query
        query = SolutionIssue.query.filter(SolutionIssue.solution_id == solution_id)

        # Apply filters
        if status:
            query = query.filter(SolutionIssue.status == status)
        if priority:
            query = query.filter(SolutionIssue.severity == priority)
        if assigned_to:
            query = query.filter(SolutionIssue.assigned_to_id == assigned_to)

        # Sort
        if sort_by == 'priority':
            # Numerical priority (lower = higher priority)
            query = query.order_by(
                SolutionIssue.priority.asc() if sort_order == 'asc' else SolutionIssue.priority.desc()
            )
        elif sort_by == 'created_at':
            query = query.order_by(
                SolutionIssue.created_at.asc() if sort_order == 'asc' else SolutionIssue.created_at.desc()
            )
        elif sort_by == 'age':
            # Sort by how old the issue is
            query = query.order_by(
                SolutionIssue.created_at.asc() if sort_order == 'desc' else SolutionIssue.created_at.desc()
            )

        issues = query.all()

        # Convert to JSON with related data
        issues_data = []
        for issue in issues:
            issue_dict = issue.to_dict()
            
            # Add assignee info — assigned_to_id is request-supplied (see create/update
            # below), so it must be resolved inside the caller's organisation, not just
            # trusted as a FK off an org-scoped issue row.
            if issue.assigned_to_id:
                assignee = user_in_org(issue.assigned_to_id, current_org_id())
                if assignee:
                    issue_dict['assigned_to'] = assignee.email.split('@')[0]

            # Add creator info
            if issue.created_by_id:
                creator = user_in_org(issue.created_by_id, current_org_id())
                if creator:
                    issue_dict['created_by'] = creator.email.split('@')[0]

            # Add impact description (from workflow task context if available)
            issue_dict['estimated_impact'] = issue.estimated_impact or 'Impact not assessed'

            # Add comment count (would query from comments table if it existed)
            issue_dict['comment_count'] = 0

            # Add percent complete (from execution tracking if available)
            issue_dict['percent_complete'] = 0

            # Add status for frontend
            status_map = {
                'open': 'NEW',
                'investigating': 'IN_PROGRESS',
                'resolved': 'RESOLVED',
                'closed': 'RESOLVED',
                'on_hold': 'NEW'
            }
            issue_dict['status'] = status_map.get(issue.status, 'NEW')

            # Map priority
            priority_map = {
                'P1': 'P0',  # P1 in DB = P0 in UI (most critical)
                'P2': 'P1',
                'P3': 'P2',
                'P4': 'P2'
            }
            issue_dict['priority'] = priority_map.get(issue.severity, 'P2')

            issues_data.append(issue_dict)

        return jsonify(issues_data)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f'Error listing issues: {str(e)}')
        return jsonify({'error': 'Failed to list issues'}), 500


@issue_bp.route('/<int:issue_id>', methods=['GET'])
@login_required
def get_issue(solution_id, issue_id):
    """
    GET /api/solutions/<solution_id>/issues/<issue_id>
    Returns a specific issue with full details
    """
    try:
        issue = SolutionIssue.query.filter(
            SolutionIssue.id == issue_id,
            SolutionIssue.solution_id == solution_id
        ).first()

        if not issue:
            return jsonify({'error': 'Issue not found'}), 404

        issue_dict = issue.to_dict()

        # Add related data
        if issue.assigned_to_id:
            assignee = user_in_org(issue.assigned_to_id, current_org_id())
            if assignee:
                issue_dict['assigned_to'] = assignee.email.split('@')[0]

        if issue.created_by_id:
            creator = user_in_org(issue.created_by_id, current_org_id())
            if creator:
                issue_dict['created_by'] = creator.email.split('@')[0]

        return jsonify(issue_dict)

    except Exception as e:
        logger.error(f'Error getting issue: {str(e)}')
        return jsonify({'error': 'Failed to get issue'}), 500


@issue_bp.route('', methods=['POST'])
@login_required
def create_issue(solution_id):
    """
    POST /api/solutions/<solution_id>/issues
    Create a new issue
    """
    try:
        data = request.get_json()

        # Validate required fields
        if not data.get('title'):
            return jsonify({'error': 'Title is required'}), 400
        if not data.get('description'):
            return jsonify({'error': 'Description is required'}), 400

        # Validate priority
        priority_map = {
            'P0': 'P1',  # P0 in UI = P1 in DB
            'P1': 'P2',
            'P2': 'P3'
        }
        db_priority = priority_map.get(data.get('severity', 'P2'), 'P3')

        assigned_to_id = data.get('assigned_to_id')
        if assigned_to_id and not user_in_org(assigned_to_id, current_org_id()):
            return jsonify({'error': 'Invalid assigned_to_id'}), 400

        # Create issue
        issue = SolutionIssue(
            solution_id=solution_id,
            title=data.get('title'),
            description=data.get('description'),
            category=data.get('category'),
            severity=db_priority,
            priority=len(data.get('title', 'x')) * 10,  # Simple priority calculation
            status='open',
            impact_area=data.get('impact_area'),
            estimated_impact=data.get('estimated_impact'),
            assigned_to_id=assigned_to_id,
            created_by_id=current_user.id,
            target_resolution_date=data.get('target_resolution_date'),
            auto_escalate_if_not_resolved_by=data.get('auto_escalate_if_not_resolved_by')
        )

        db.session.add(issue)
        db.session.commit()

        issue_dict = issue.to_dict()
        issue_dict['status'] = 'NEW'
        issue_dict['priority'] = data.get('severity', 'P2')

        return jsonify(issue_dict), 201

    except Exception as e:
        db.session.rollback()
        logger.error(f'Error creating issue: {str(e)}')
        return jsonify({'error': 'Failed to create issue'}), 500


@issue_bp.route('/<int:issue_id>', methods=['PUT'])
@login_required
def update_issue(solution_id, issue_id):
    """
    PUT /api/solutions/<solution_id>/issues/<issue_id>
    Update an issue (status, assignment, etc.)
    """
    try:
        issue = SolutionIssue.query.filter(
            SolutionIssue.id == issue_id,
            SolutionIssue.solution_id == solution_id
        ).first()

        if not issue:
            return jsonify({'error': 'Issue not found'}), 404

        data = request.get_json()

        if 'assigned_to_id' in data and data['assigned_to_id'] and not user_in_org(
            data['assigned_to_id'], current_org_id()
        ):
            return jsonify({'error': 'Invalid assigned_to_id'}), 400

        # Update status
        if 'status' in data:
            status_map = {
                'NEW': 'open',
                'IN_PROGRESS': 'investigating',
                'RESOLVED': 'resolved',
                'ESCALATED': 'investigating'
            }
            issue.status = status_map.get(data['status'], issue.status)

            # If transitioning to RESOLVED, set resolved_at
            if data['status'] == 'RESOLVED' and not issue.resolved_at:
                issue.resolved_at = datetime.utcnow()
                issue.resolved_by_id = current_user.id

        # Update assignment
        if 'assigned_to_id' in data:
            issue.assigned_to_id = data['assigned_to_id']

        # Update other fields
        if 'title' in data:
            issue.title = data['title']
        if 'description' in data:
            issue.description = data['description']
        if 'estimated_impact' in data:
            issue.estimated_impact = data['estimated_impact']

        db.session.commit()

        issue_dict = issue.to_dict()
        if issue.assigned_to_id:
            assignee = user_in_org(issue.assigned_to_id, current_org_id())
            if assignee:
                issue_dict['assigned_to'] = assignee.email.split('@')[0]

        status_map_reverse = {
            'open': 'NEW',
            'investigating': 'IN_PROGRESS',
            'resolved': 'RESOLVED'
        }
        issue_dict['status'] = status_map_reverse.get(issue.status, 'NEW')

        return jsonify(issue_dict)

    except Exception as e:
        db.session.rollback()
        logger.error(f'Error updating issue: {str(e)}')
        return jsonify({'error': 'Failed to update issue'}), 500


@issue_bp.route('/<int:issue_id>/escalate', methods=['POST'])
@login_required
def escalate_issue(solution_id, issue_id):
    """
    POST /api/solutions/<solution_id>/issues/<issue_id>/escalate
    Manually escalate an issue
    """
    try:
        issue = SolutionIssue.query.filter(
            SolutionIssue.id == issue_id,
            SolutionIssue.solution_id == solution_id
        ).first()

        if not issue:
            return jsonify({'error': 'Issue not found'}), 404

        # Increment escalation count
        issue.escalation_count = (issue.escalation_count or 0) + 1
        issue.escalated_at = datetime.utcnow()
        issue.escalation_reason = request.get_json().get('reason', 'Manual escalation')
        
        # For P0 issues, mark as ESCALATED status
        if issue.severity == 'P1':  # P1 in DB = P0 in UI
            issue.status = 'investigating'

        db.session.commit()

        issue_dict = issue.to_dict()
        if issue.assigned_to_id:
            assignee = user_in_org(issue.assigned_to_id, current_org_id())
            if assignee:
                issue_dict['assigned_to'] = assignee.email.split('@')[0]

        issue_dict['status'] = 'ESCALATED'
        priority_map = {'P1': 'P0', 'P2': 'P1', 'P3': 'P2'}
        issue_dict['priority'] = priority_map.get(issue.severity, 'P2')

        return jsonify(issue_dict)

    except Exception as e:
        db.session.rollback()
        logger.error(f'Error escalating issue: {str(e)}')
        return jsonify({'error': 'Failed to escalate issue'}), 500


@issue_bp.route('/<int:issue_id>/escalations', methods=['GET'])
@login_required
def get_escalation_history(solution_id, issue_id):
    """
    GET /api/solutions/<solution_id>/issues/<issue_id>/escalations
    Returns escalation history for an issue
    """
    try:
        issue = SolutionIssue.query.filter(
            SolutionIssue.id == issue_id,
            SolutionIssue.solution_id == solution_id
        ).first()

        if not issue:
            return jsonify({'error': 'Issue not found'}), 404

        escalation_history = []

        # Build escalation history
        if issue.created_at:
            escalation_history.append({
                'event': 'created',
                'timestamp': issue.created_at.isoformat(),
                'by': issue.created_by_id
            })

        if issue.escalated_at:
            escalation_history.append({
                'event': 'escalated',
                'timestamp': issue.escalated_at.isoformat(),
                'count': issue.escalation_count,
                'reason': issue.escalation_reason
            })

        if issue.resolved_at:
            escalation_history.append({
                'event': 'resolved',
                'timestamp': issue.resolved_at.isoformat(),
                'by': issue.resolved_by_id
            })

        return jsonify({
            'issue_id': issue_id,
            'escalation_count': issue.escalation_count,
            'history': escalation_history
        })

    except Exception as e:
        logger.error(f'Error getting escalation history: {str(e)}')
        return jsonify({'error': 'Failed to get escalation history'}), 500
