"""
ADM Audit Service

Provides comprehensive audit logging for all ADM activities.
Orchestrates audit event recording, querying, and reporting.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from flask import has_request_context, request
from sqlalchemy import func

from app import db
from app.models.adm_audit_log import ADMAuditAction, ADMAuditLog
from app.models.adm_kanban import ADMPhase, KanbanBoard, KanbanCard

logger = logging.getLogger(__name__)


class ADMAuditService:
    """
    Service for comprehensive ADM audit logging.

    Provides:
    - Audit event recording
    - Audit log querying
    - Audit summary generation
    - Regulatory compliance reporting
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def _get_request_context(self) -> Dict[str, Any]:
        """Extract request context if available."""
        context = {
            "ip_address": None,
            "user_agent": None,
            "request_id": None,
        }

        if has_request_context():
            try:
                context["ip_address"] = request.remote_addr
                context["user_agent"] = request.headers.get("User-Agent")
                context["request_id"] = request.headers.get("X-Request-ID")
            except Exception:
                logger.debug("Failed to extract request context for audit log", exc_info=True)

        return context

    def _get_user_info(self, user_id: int, organization_id: int = None) -> Dict[str, Any]:
        """Get user information for audit, scoped to organization_id.

        actor_id is not always the acting session's own id (see log_event
        callers), so it is resolved only inside organization_id rather than
        by a bare id lookup.
        """
        from app.utils.tenant_users import user_in_org

        user = user_in_org(user_id, organization_id)
        if user:
            return {
                "email": user.email,
                "role": getattr(user, "primary_role", None),  # model-safety-ok: optional field (not on User schema)
            }
        return {"email": None, "role": None}

    def log_event(
        self,
        action: ADMAuditAction,
        entity_type: str,
        entity_id: int,
        actor_id: int,
        entity_reference: str = None,
        board_id: int = None,
        card_id: int = None,
        approval_id: int = None,
        phase_id: int = None,
        source_phase_id: int = None,
        target_phase_id: int = None,
        old_values: Dict = None,
        new_values: Dict = None,
        changed_fields: List[str] = None,
        justification: str = None,
        organization_id: int = None,
    ) -> ADMAuditLog:
        """
        Record an audit event.

        Args:
            action: Type of action from ADMAuditAction
            entity_type: Type of entity (card, board, approval, etc.)
            entity_id: ID of the entity
            actor_id: User ID who performed the action
            entity_reference: Human-readable reference
            board_id: Associated board ID
            card_id: Associated card ID
            approval_id: Associated approval ID
            phase_id: Associated phase ID
            source_phase_id: Source phase (for transitions)
            target_phase_id: Target phase (for transitions)
            old_values: Previous values (for updates)
            new_values: New values (for updates)
            changed_fields: List of field names that changed
            justification: Business justification
            organization_id: Organisation actor_id is resolved against (the
                entity's own organisation, when the caller has it to hand);
                falls back to the acting request's own organisation

        Returns:
            Created ADMAuditLog entry
        """
        # Get phase codes if IDs provided
        source_phase_code = None
        target_phase_code = None

        if source_phase_id:
            phase = db.session.get(ADMPhase, source_phase_id)
            if phase:
                source_phase_code = phase.code

        if target_phase_id:
            phase = db.session.get(ADMPhase, target_phase_id)
            if phase:
                target_phase_code = phase.code

        # Get request context
        request_context = self._get_request_context()

        if organization_id is None and has_request_context():
            from app.middleware.tenant_context import current_org_id

            organization_id = current_org_id()

        # Get user info
        user_info = self._get_user_info(actor_id, organization_id)

        audit_log = ADMAuditLog(
            audit_id=ADMAuditLog.generate_audit_id(),
            entity_type=entity_type,
            entity_id=entity_id,
            entity_reference=entity_reference,
            action=action.value,
            action_description=self._get_action_description(action, entity_type),
            old_values=old_values,
            new_values=new_values,
            changed_fields=changed_fields,
            board_id=board_id,
            card_id=card_id,
            approval_id=approval_id,
            phase_id=phase_id,
            source_phase_id=source_phase_id,
            target_phase_id=target_phase_id,
            source_phase_code=source_phase_code,
            target_phase_code=target_phase_code,
            actor_id=actor_id,
            actor_email=user_info["email"],
            actor_role=user_info["role"],
            ip_address=request_context["ip_address"],
            user_agent=request_context["user_agent"],
            request_id=request_context["request_id"],
            justification=justification,
        )

        db.session.add(audit_log)
        db.session.commit()

        self.logger.debug(f"Recorded audit event: {audit_log.audit_id} - {action.value}")
        return audit_log

    def _get_action_description(self, action: ADMAuditAction, entity_type: str) -> str:
        """Get human-readable description for an action."""

        descriptions = {
            ADMAuditAction.CARD_CREATED: f"Created new {entity_type}",
            ADMAuditAction.CARD_UPDATED: f"Updated {entity_type}",
            ADMAuditAction.CARD_DELETED: f"Deleted {entity_type}",
            ADMAuditAction.CARD_MOVED: f"Moved {entity_type} to different phase",
            ADMAuditAction.PHASE_TRANSITION_REQUESTED: f"Requested phase transition for {entity_type}",
            ADMAuditAction.PHASE_TRANSITION_APPROVED: f"Approved phase transition for {entity_type}",
            ADMAuditAction.PHASE_TRANSITION_REJECTED: f"Rejected phase transition for {entity_type}",
            ADMAuditAction.PHASE_TRANSITION_EXECUTED: f"Executed approved phase transition for {entity_type}",
            ADMAuditAction.APPROVAL_CREATED: f"Created approval request for {entity_type}",
            ADMAuditAction.APPROVAL_SUBMITTED: f"Submitted approval request for {entity_type}",
            ADMAuditAction.APPROVAL_DECISION_RECORDED: f"Recorded decision on approval for {entity_type}",
            ADMAuditAction.CHECKPOINT_COMPLETED: f"Completed compliance checkpoint for {entity_type}",
            ADMAuditAction.STAKEHOLDER_CONCURRENCE_RECORDED: f"Recorded stakeholder concurrence for {entity_type}",
            ADMAuditAction.COMMENT_ADDED: f"Added comment to {entity_type}",
            ADMAuditAction.ATTACHMENT_ADDED: f"Added attachment to {entity_type}",
        }

        return descriptions.get(action, f"{action.value} on {entity_type}")

    # =========================================================================
    # Convenience methods for common audit events
    # =========================================================================

    def log_card_created(self, card: KanbanCard, actor_id: int) -> ADMAuditLog:
        """Log card creation."""
        return self.log_event(
            action=ADMAuditAction.CARD_CREATED,
            entity_type="card",
            entity_id=card.id,
            actor_id=actor_id,
            entity_reference=card.title,
            board_id=card.board_id,
            card_id=card.id,
            phase_id=card.adm_phase_id,
            new_values={"title": card.title, "phase": card.adm_phase.code if card.adm_phase else None},
        )

    def log_card_moved(
        self,
        card: KanbanCard,
        source_phase: ADMPhase,
        target_phase: ADMPhase,
        actor_id: int,
        approval_id: int = None,
        justification: str = None,
    ) -> ADMAuditLog:
        """Log card phase transition."""
        return self.log_event(
            action=ADMAuditAction.CARD_MOVED,
            entity_type="card",
            entity_id=card.id,
            actor_id=actor_id,
            entity_reference=card.title,
            board_id=card.board_id,
            card_id=card.id,
            approval_id=approval_id,
            source_phase_id=source_phase.id,
            target_phase_id=target_phase.id,
            old_values={"phase": source_phase.code, "phase_name": source_phase.name},
            new_values={"phase": target_phase.code, "phase_name": target_phase.name},
            changed_fields=["adm_phase_id"],
            justification=justification,
        )

    def log_card_updated(
        self,
        card: KanbanCard,
        actor_id: int,
        old_values: Dict,
        new_values: Dict,
        changed_fields: List[str],
    ) -> ADMAuditLog:
        """Log card update."""
        return self.log_event(
            action=ADMAuditAction.CARD_UPDATED,
            entity_type="card",
            entity_id=card.id,
            actor_id=actor_id,
            entity_reference=card.title,
            board_id=card.board_id,
            card_id=card.id,
            phase_id=card.adm_phase_id,
            old_values=old_values,
            new_values=new_values,
            changed_fields=changed_fields,
        )

    def log_board_created(self, board: KanbanBoard, actor_id: int) -> ADMAuditLog:
        """Log board creation."""
        return self.log_event(
            action=ADMAuditAction.BOARD_CREATED,
            entity_type="board",
            entity_id=board.id,
            actor_id=actor_id,
            entity_reference=board.name,
            board_id=board.id,
            new_values={"name": board.name, "project": board.project_name},
        )

    def log_approval_created(self, approval, actor_id: int) -> ADMAuditLog:
        """Log approval request creation."""
        return self.log_event(
            action=ADMAuditAction.APPROVAL_CREATED,
            entity_type="approval",
            entity_id=approval.id,
            actor_id=actor_id,
            entity_reference=approval.approval_number,
            board_id=approval.board_id,
            card_id=approval.card_id,
            source_phase_id=approval.source_phase_id,
            target_phase_id=approval.target_phase_id,
            new_values={
                "approval_number": approval.approval_number,
                "source_phase": approval.source_phase.code,
                "target_phase": approval.target_phase.code,
            },
        )

    def log_approval_decision(
        self,
        approval,
        actor_id: int,
        decision: str,
        rationale: str,
    ) -> ADMAuditLog:
        """Log approval decision."""
        return self.log_event(
            action=ADMAuditAction.APPROVAL_DECISION_RECORDED,
            entity_type="approval",
            entity_id=approval.id,
            actor_id=actor_id,
            entity_reference=approval.approval_number,
            board_id=approval.board_id,
            card_id=approval.card_id,
            approval_id=approval.id,
            old_values={"status": approval.status},
            new_values={"status": decision, "decision": decision},
            changed_fields=["status", "decision"],
            justification=rationale,
        )

    def log_checkpoint_completed(self, checkpoint, actor_id: int) -> ADMAuditLog:
        """Log compliance checkpoint completion."""
        return self.log_event(
            action=ADMAuditAction.CHECKPOINT_COMPLETED,
            entity_type="checkpoint",
            entity_id=checkpoint.id,
            actor_id=actor_id,
            entity_reference=checkpoint.checkpoint_name,
            approval_id=checkpoint.approval_id,
            new_values={
                "checkpoint_name": checkpoint.checkpoint_name,
                "completed": True,
                "evidence_url": checkpoint.evidence_url,
            },
        )

    def log_stakeholder_concurrence(self, concurrence, actor_id: int) -> ADMAuditLog:
        """Log stakeholder concurrence."""
        return self.log_event(
            action=ADMAuditAction.STAKEHOLDER_CONCURRENCE_RECORDED,
            entity_type="stakeholder_concurrence",
            entity_id=concurrence.id,
            actor_id=actor_id,
            entity_reference=concurrence.stakeholder_role,
            approval_id=concurrence.approval_id,
            old_values={"status": "pending"},
            new_values={"status": concurrence.status},
            changed_fields=["status"],
        )

    # =========================================================================
    # Audit Query Methods
    # =========================================================================

    def get_audit_logs(
        self,
        entity_type: str = None,
        entity_id: int = None,
        action: str = None,
        actor_id: int = None,
        board_id: int = None,
        card_id: int = None,
        start_date: datetime = None,
        end_date: datetime = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ADMAuditLog]:
        """
        Query audit logs with filters.
        """
        query = ADMAuditLog.query

        if entity_type:
            query = query.filter_by(entity_type=entity_type)
        if entity_id:
            query = query.filter_by(entity_id=entity_id)
        if action:
            query = query.filter_by(action=action)
        if actor_id:
            query = query.filter_by(actor_id=actor_id)
        if board_id:
            query = query.filter_by(board_id=board_id)
        if card_id:
            query = query.filter_by(card_id=card_id)
        if start_date:
            query = query.filter(ADMAuditLog.timestamp >= start_date)
        if end_date:
            query = query.filter(ADMAuditLog.timestamp <= end_date)

        return (
            query.order_by(ADMAuditLog.timestamp.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

    def get_card_audit_trail(self, card_id: int) -> List[ADMAuditLog]:
        """Get complete audit trail for a card."""
        return (
            ADMAuditLog.query.filter_by(card_id=card_id)
            .order_by(ADMAuditLog.timestamp.asc())
            .all()
        )

    def get_board_audit_summary(self, board_id: int, days: int = 30) -> Dict[str, Any]:
        """Get audit summary for a board."""
        start_date = datetime.utcnow() - timedelta(days=days)

        # Total actions
        total_actions = (
            ADMAuditLog.query.filter(
                ADMAuditLog.board_id == board_id,
                ADMAuditLog.timestamp >= start_date,
            ).count()
        )

        # Actions by type
        actions_by_type = (
            db.session.query(ADMAuditLog.action, func.count(ADMAuditLog.id))
            .filter(
                ADMAuditLog.board_id == board_id,
                ADMAuditLog.timestamp >= start_date,
            )
            .group_by(ADMAuditLog.action)
            .all()
        )

        # Top actors
        top_actors = (
            db.session.query(
                ADMAuditLog.actor_id,
                ADMAuditLog.actor_email,
                func.count(ADMAuditLog.id).label("action_count"),
            )
            .filter(
                ADMAuditLog.board_id == board_id,
                ADMAuditLog.timestamp >= start_date,
            )
            .group_by(ADMAuditLog.actor_id, ADMAuditLog.actor_email)
            .order_by(func.count(ADMAuditLog.id).desc())
            .limit(10)
            .all()
        )

        return {
            "board_id": board_id,
            "period_days": days,
            "total_actions": total_actions,
            "actions_by_type": {action: count for action, count in actions_by_type},
            "top_actors": [
                {"user_id": actor_id, "email": email, "action_count": count}
                for actor_id, email, count in top_actors
            ],
        }

    def get_regulatory_report(
        self,
        start_date: datetime,
        end_date: datetime,
        board_id: int = None,
    ) -> Dict[str, Any]:
        """
        Generate regulatory compliance report.

        Provides comprehensive audit trail for regulatory requirements.
        """
        query = ADMAuditLog.query.filter(
            ADMAuditLog.timestamp >= start_date,
            ADMAuditLog.timestamp <= end_date,
        )

        if board_id:
            query = query.filter_by(board_id=board_id)

        # Phase transitions (critical for regulatory compliance)
        transitions = query.filter(
            ADMAuditLog.action.in_([
                "phase_transition_requested",
                "phase_transition_approved",
                "phase_transition_executed",
            ])
        ).all()

        # Approval decisions
        approvals = query.filter(
            ADMAuditLog.action.in_([
                "approval_created",
                "approval_submitted",
                "approval_decision_recorded",
            ])
        ).all()

        # Who made changes
        actors = (
            db.session.query(
                ADMAuditLog.actor_id,
                ADMAuditLog.actor_email,
                func.count(ADMAuditLog.id).label("action_count"),
            )
            .filter(
                ADMAuditLog.timestamp >= start_date,
                ADMAuditLog.timestamp <= end_date,
            )
            .group_by(ADMAuditLog.actor_id, ADMAuditLog.actor_email)
            .all()
        )

        return {
            "report_period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "total_events": query.count(),
            "phase_transitions": len(transitions),
            "approval_events": len(approvals),
            "unique_actors": len(actors),
            "actors": [
                {"user_id": actor_id, "email": email, "action_count": count}
                for actor_id, email, count in actors
            ],
            "transitions_detail": [
                {
                    "timestamp": t.timestamp.isoformat(),
                    "action": t.action,
                    "card_id": t.card_id,
                    "source_phase": t.source_phase_code,
                    "target_phase": t.target_phase_code,
                    "actor_email": t.actor_email,
                    "justification": t.justification,
                }
                for t in transitions
            ],
            "approvals_detail": [
                {
                    "timestamp": a.timestamp.isoformat(),
                    "action": a.action,
                    "approval_id": a.approval_id,
                    "actor_email": a.actor_email,
                    "decision": a.new_values.get("decision") if a.new_values else None,
                    "justification": a.justification,
                }
                for a in approvals
            ],
        }


# Singleton instance
adm_audit_service = ADMAuditService()
