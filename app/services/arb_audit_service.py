"""
ARB Audit Service

Provides comprehensive audit trail management for ARB entities.
Tracks all changes to review items, sessions, exceptions, and standards
with before/after values and user attribution.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from flask import has_request_context, request
from sqlalchemy import and_, desc

from app import db
from app.models.architecture_review_board import (
    ARBAuditAction,
    ARBAuditLog,
    ARBException,
    ARBReviewItem,
)

logger = logging.getLogger(__name__)


class ARBAuditService:
    """
    Service for ARB audit trail management.

    Provides methods for logging actions and retrieving audit history
    for ARB entities (review items, sessions, exceptions, standards).
    """

    # Fields to exclude from audit logging (sensitive or verbose)
    EXCLUDED_FIELDS = {"password", "token", "secret", "api_key"}

    # Fields to track for each entity type
    TRACKED_FIELDS = {
        "review_item": [
            "title",
            "description",
            "status",
            "priority",
            "business_impact",
            "review_type",
            "togaf_phase",
            "archimate_layer",
            "decision",
            "decision_rationale",
            "conditions",
            "reviewer_id",
            "arb_session_id",
            "compliance_score",
            "risk_score",
            "quality_score",
            "overall_score",
            "readiness_score",
            "version",
            "amendment_count",
        ],
        "session": [
            "name",
            "description",
            "status",
            "scheduled_date",
            "duration_minutes",
            "location",
            "meeting_link",
            "chair_id",
            "secretary_id",
            "items_reviewed",
            "items_approved",
            "items_rejected",
            "items_deferred",
        ],
        "exception": [
            "status",
            "exception_type",
            "exception_reason",
            "business_justification",
            "risk_mitigation",
            "scope",
            "expires_at",
            "approval_notes",
            "denial_reason",
        ],
        "standard": [
            "name",
            "description",
            "category",
            "status",
            "requirements",
            "checklist_items",
            "mandatory",
            "effective_date",
        ],
    }

    def __init__(self):
        pass

    def log_action(
        self,
        entity_type: str,
        entity_id: int,
        action: str,
        user_id: int,
        entity_reference: str = None,
        old_value: Dict = None,
        new_value: Dict = None,
        description: str = None,
        changed_fields: List[str] = None,
    ) -> ARBAuditLog:
        """
        Log an action to the ARB audit trail.

        Args:
            entity_type: Type of entity (review_item, session, exception, standard)
            entity_id: ID of the entity
            action: Action performed (from ARBAuditAction)
            user_id: ID of the user performing the action
            entity_reference: Human-readable reference (e.g., REV - 2026 - 001)
            old_value: Previous state (JSON)
            new_value: New state (JSON)
            description: Human-readable description of the action
            changed_fields: List of field names that changed

        Returns:
            Created ARBAuditLog instance
        """
        from app.middleware.tenant_context import current_org_id
        from app.utils.tenant_users import user_in_org

        # Get user email for denormalization. user_id may legitimately be
        # None for a system/refusal event (e.g. "no resolvable approver").
        # user_id can also arrive here from request-supplied or stored data
        # (an exception's requested_by_id/approved_by_id, forwarded by a
        # caller with no organisation check of its own), so it is resolved
        # only inside the acting session's own organisation, never by a
        # bare id lookup.
        acting_org_id = current_org_id() if has_request_context() else None
        user = user_in_org(user_id, acting_org_id) if user_id is not None else None
        user_email = user.email if user else None

        # Get request context if available
        ip_address = None
        user_agent = None
        request_id = None

        if has_request_context():
            ip_address = request.remote_addr
            user_agent = request.headers.get("User-Agent", "")[:500]
            request_id = request.headers.get("X-Request-ID")

        # Create audit log entry
        audit_log = ARBAuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            entity_reference=entity_reference,
            action=action,
            action_description=description,
            old_value=old_value,
            new_value=new_value,
            changed_fields=changed_fields,
            user_id=user_id,
            user_email=user_email,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            timestamp=datetime.utcnow(),
        )

        db.session.add(audit_log)
        db.session.commit()

        logger.info(f"Audit log: {action} on {entity_type}:{entity_id} by user {user_id}")

        # Every ARB entry is copied into the one audit store (soc2_audit_log,
        # read by /admin/audit-log) by the AuditLog model's insert hook, with
        # source_table/source_id, so it is not mirrored again here.

        return audit_log

    def log_create(
        self,
        entity_type: str,
        entity_id: int,
        user_id: int,
        entity_reference: str = None,
        new_value: Dict = None,
    ) -> ARBAuditLog:
        """Log entity creation."""
        return self.log_action(
            entity_type=entity_type,
            entity_id=entity_id,
            action=ARBAuditAction.CREATE.value,
            user_id=user_id,
            entity_reference=entity_reference,
            new_value=new_value,
            description=f"Created {entity_type} {entity_reference or entity_id}",
        )

    def log_update(
        self,
        entity_type: str,
        entity_id: int,
        user_id: int,
        entity_reference: str = None,
        old_value: Dict = None,
        new_value: Dict = None,
        changed_fields: List[str] = None,
    ) -> ARBAuditLog:
        """Log entity update."""
        fields_desc = ", ".join(changed_fields) if changed_fields else "fields"
        return self.log_action(
            entity_type=entity_type,
            entity_id=entity_id,
            action=ARBAuditAction.UPDATE.value,
            user_id=user_id,
            entity_reference=entity_reference,
            old_value=old_value,
            new_value=new_value,
            changed_fields=changed_fields,
            description=f"Updated {fields_desc} on {entity_type} {entity_reference or entity_id}",
        )

    def log_status_change(
        self,
        entity_type: str,
        entity_id: int,
        old_status: str,
        new_status: str,
        user_id: int,
        entity_reference: str = None,
    ) -> ARBAuditLog:
        """Log status change on an entity."""
        return self.log_action(
            entity_type=entity_type,
            entity_id=entity_id,
            action=ARBAuditAction.STATUS_CHANGE.value,
            user_id=user_id,
            entity_reference=entity_reference,
            old_value={"status": old_status},
            new_value={"status": new_status},
            changed_fields=["status"],
            description=f"Status changed from '{old_status}' to '{new_status}'",
        )

    def log_decision(
        self,
        review_item: ARBReviewItem,
        decision: str,
        user_id: int,
        rationale: str = None,
    ) -> ARBAuditLog:
        """Log a decision on a review item."""
        return self.log_action(
            entity_type="review_item",
            entity_id=review_item.id,
            action=ARBAuditAction.DECISION.value,
            user_id=user_id,
            entity_reference=review_item.review_number,
            old_value={"decision": review_item.decision, "status": review_item.status},
            new_value={"decision": decision, "rationale": rationale},
            changed_fields=["decision", "decision_rationale", "status"],
            description=f"Decision recorded: {decision}",
        )

    def log_assignment(
        self,
        entity_type: str,
        entity_id: int,
        user_id: int,
        assigned_to_id: int,
        entity_reference: str = None,
        assignment_type: str = "reviewer",
    ) -> ARBAuditLog:
        """Log an assignment action."""
        return self.log_action(
            entity_type=entity_type,
            entity_id=entity_id,
            action=ARBAuditAction.ASSIGNMENT.value,
            user_id=user_id,
            entity_reference=entity_reference,
            new_value={f"{assignment_type}_id": assigned_to_id},
            changed_fields=[f"{assignment_type}_id"],
            description=f"Assigned {assignment_type} (user {assigned_to_id})",
        )

    def log_score_update(
        self,
        review_item: ARBReviewItem,
        user_id: int,
        old_scores: Dict,
        new_scores: Dict,
    ) -> ARBAuditLog:
        """Log governance score updates."""
        changed = [k for k in new_scores if old_scores.get(k) != new_scores.get(k)]
        return self.log_action(
            entity_type="review_item",
            entity_id=review_item.id,
            action=ARBAuditAction.SCORE_UPDATE.value,
            user_id=user_id,
            entity_reference=review_item.review_number,
            old_value=old_scores,
            new_value=new_scores,
            changed_fields=changed,
            description=f"Scores updated: {', '.join(changed)}",
        )

    def log_comment_add(
        self,
        review_item_id: int,
        user_id: int,
        comment_id: int,
        entity_reference: str = None,
    ) -> ARBAuditLog:
        """Log comment addition."""
        return self.log_action(
            entity_type="review_item",
            entity_id=review_item_id,
            action=ARBAuditAction.COMMENT_ADD.value,
            user_id=user_id,
            entity_reference=entity_reference,
            new_value={"comment_id": comment_id},
            description=f"Comment added (id: {comment_id})",
        )

    def log_exception_request(
        self,
        exception: ARBException,
        user_id: int,
    ) -> ARBAuditLog:
        """Log exception request creation."""
        return self.log_action(
            entity_type="exception",
            entity_id=exception.id,
            action=ARBAuditAction.EXCEPTION_REQUEST.value,
            user_id=user_id,
            entity_reference=exception.exception_number,
            new_value={
                "standard_id": exception.standard_id,
                "exception_type": exception.exception_type,
                "status": exception.status,
            },
            description=f"Exception requested for standard {exception.standard_id}",
        )

    def log_exception_decision(
        self,
        exception: ARBException,
        decision: str,
        user_id: int,
        notes: str = None,
    ) -> ARBAuditLog:
        """Log exception approval or denial."""
        return self.log_action(
            entity_type="exception",
            entity_id=exception.id,
            action=ARBAuditAction.EXCEPTION_DECISION.value,
            user_id=user_id,
            entity_reference=exception.exception_number,
            old_value={"status": exception.status},
            new_value={"status": decision, "notes": notes},
            changed_fields=["status"],
            description=f"Exception {decision}: {notes or 'No notes'}",
        )

    def log_readiness_check(
        self,
        review_item: ARBReviewItem,
        user_id: int,
        old_score: float,
        new_score: float,
    ) -> ARBAuditLog:
        """Log readiness check execution."""
        return self.log_action(
            entity_type="review_item",
            entity_id=review_item.id,
            action=ARBAuditAction.READINESS_CHECK.value,
            user_id=user_id,
            entity_reference=review_item.review_number,
            old_value={"readiness_score": old_score},
            new_value={"readiness_score": new_score},
            changed_fields=["readiness_score", "readiness_checked_at"],
            description=f"Readiness check completed: {new_score:.1f}%",
        )

    # =========================================================================
    # QUERY METHODS
    # =========================================================================

    def get_entity_history(
        self,
        entity_type: str,
        entity_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ARBAuditLog]:
        """
        Get audit history for a specific entity.

        Args:
            entity_type: Type of entity
            entity_id: ID of the entity
            limit: Maximum number of records to return
            offset: Number of records to skip

        Returns:
            List of ARBAuditLog entries
        """
        return (
            ARBAuditLog.query.filter(
                and_(
                    ARBAuditLog.entity_type == entity_type,
                    ARBAuditLog.entity_id == entity_id,
                )
            )
            .order_by(desc(ARBAuditLog.timestamp))
            .offset(offset)
            .limit(limit)
            .all()
        )

    def get_user_activity(
        self,
        user_id: int,
        days: int = 30,
        limit: int = 100,
    ) -> List[ARBAuditLog]:
        """
        Get recent activity by a specific user.

        Args:
            user_id: ID of the user
            days: Number of days to look back
            limit: Maximum number of records

        Returns:
            List of ARBAuditLog entries
        """
        cutoff = datetime.utcnow() - timedelta(days=days)
        return (
            ARBAuditLog.query.filter(
                and_(
                    ARBAuditLog.user_id == user_id,
                    ARBAuditLog.timestamp >= cutoff,
                )
            )
            .order_by(desc(ARBAuditLog.timestamp))
            .limit(limit)
            .all()
        )

    def get_recent_activity(
        self,
        entity_type: str = None,
        action: str = None,
        days: int = 30,
        limit: int = 50,
    ) -> List[ARBAuditLog]:
        """
        Get recent audit activity with optional filters.

        Args:
            entity_type: Optional filter by entity type
            action: Optional filter by action type
            days: Number of days to look back (default 30)
            limit: Maximum number of records

        Returns:
            List of ARBAuditLog entries
        """
        from datetime import datetime, timedelta

        query = ARBAuditLog.query

        # Filter by time range
        cutoff = datetime.utcnow() - timedelta(days=days)
        query = query.filter(ARBAuditLog.timestamp >= cutoff)

        if entity_type:
            query = query.filter(ARBAuditLog.entity_type == entity_type)
        if action:
            query = query.filter(ARBAuditLog.action == action)

        return query.order_by(desc(ARBAuditLog.timestamp)).limit(limit).all()

    def get_version_history(
        self,
        review_item_id: int,
    ) -> List[Dict[str, Any]]:
        """
        Get formatted version history for a review item.

        Aggregates changes into version snapshots for display.

        Args:
            review_item_id: ID of the review item

        Returns:
            List of version dictionaries with changes
        """
        logs = self.get_entity_history("review_item", review_item_id, limit=200)

        versions = []
        for log in logs:
            versions.append(
                {
                    "version": len(versions) + 1,
                    "timestamp": log.timestamp,
                    "action": log.action,
                    "description": log.action_description,
                    "user": {
                        "id": log.user_id,
                        "email": log.user_email,
                        "name": f"{log.user.first_name} {log.user.last_name}" if log.user else None,
                    },
                    "changes": {
                        "fields": log.changed_fields,
                        "old": log.old_value,
                        "new": log.new_value,
                    },
                }
            )

        return versions

    def generate_audit_report(
        self,
        entity_type: str = None,
        start_date: datetime = None,
        end_date: datetime = None,
    ) -> Dict[str, Any]:
        """
        Generate audit report with statistics.

        Args:
            entity_type: Optional filter by entity type
            start_date: Start of period (defaults to 30 days ago)
            end_date: End of period (defaults to now)

        Returns:
            Report dictionary with metrics and summary
        """
        if not start_date:
            start_date = datetime.utcnow() - timedelta(days=30)
        if not end_date:
            end_date = datetime.utcnow()

        query = ARBAuditLog.query.filter(
            and_(
                ARBAuditLog.timestamp >= start_date,
                ARBAuditLog.timestamp <= end_date,
            )
        )

        if entity_type:
            query = query.filter(ARBAuditLog.entity_type == entity_type)

        logs = query.all()

        # Calculate statistics
        action_counts = {}
        entity_counts = {}
        user_counts = {}

        for log in logs:
            action_counts[log.action] = action_counts.get(log.action, 0) + 1
            entity_counts[log.entity_type] = entity_counts.get(log.entity_type, 0) + 1
            user_counts[log.user_id] = user_counts.get(log.user_id, 0) + 1

        return {
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "total_entries": len(logs),
            "by_action": action_counts,
            "by_entity_type": entity_counts,
            "by_user": user_counts,
            "most_active_users": sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:10],
        }

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def capture_entity_state(
        self,
        entity: Any,
        entity_type: str,
    ) -> Dict[str, Any]:
        """
        Capture current state of an entity for audit comparison.

        Args:
            entity: The entity object
            entity_type: Type of entity

        Returns:
            Dictionary of tracked field values
        """
        tracked = self.TRACKED_FIELDS.get(entity_type, [])
        state = {}

        for field in tracked:
            if hasattr(entity, field):
                value = getattr(entity, field)
                # Handle datetime serialization
                if isinstance(value, datetime):
                    value = value.isoformat()
                state[field] = value

        return state

    def compare_states(
        self,
        old_state: Dict,
        new_state: Dict,
    ) -> tuple[Dict, Dict, List[str]]:
        """
        Compare two entity states and identify changes.

        Args:
            old_state: Previous state
            new_state: Current state

        Returns:
            Tuple of (old_changes, new_changes, changed_field_names)
        """
        changed_fields = []
        old_changes = {}
        new_changes = {}

        all_fields = set(old_state.keys()) | set(new_state.keys())

        for field in all_fields:
            old_val = old_state.get(field)
            new_val = new_state.get(field)

            if old_val != new_val:
                changed_fields.append(field)
                old_changes[field] = old_val
                new_changes[field] = new_val

        return old_changes, new_changes, changed_fields


# Create singleton instance
arb_audit_service = ARBAuditService()
