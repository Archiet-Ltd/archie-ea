"""
ADM Analytics Service - Meaningful EA KPIs

Replaces vanity metrics (card counts, completion percentages) with real EA KPIs:
- Architecture Board decision velocity
- Compliance coverage
- Stakeholder concurrence rate
- Architecture debt
- Principle adherence
- Reuse rate
- Business value realization
- Complexity metrics
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import func

from app import db
from app.models.adm_kanban import ADMPhase, KanbanCard
from app.models.adm_phase_approval import ADMPhaseApproval, ADMComplianceCheckpoint, ADMStakeholderConcurrence
from app.models.adm_kanban_junctions import CardArchiMateElement
from app.models.archimate_core import ArchiMateElement

logger = logging.getLogger(__name__)


class ADMAnalyticsService:
    """
    Service for calculating meaningful EA KPIs and governance metrics.

    Provides:
    - Architecture Board decision velocity
    - Compliance coverage metrics
    - Stakeholder concurrence rates
    - Architecture debt tracking
    - Principle adherence scores
    - Business value realization
    - Complexity metrics
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def get_governance_dashboard(self, board_id: int = None, days: int = 30) -> Dict[str, Any]:
        """
        Get comprehensive governance dashboard with meaningful EA KPIs.

        Args:
            board_id: Optional board ID to filter by
            days: Number of days to include in metrics

        Returns:
            Dictionary of governance KPIs
        """
        start_date = datetime.utcnow() - timedelta(days=days)

        return {
            "period": {"days": days, "start": start_date.isoformat(), "end": datetime.utcnow().isoformat()},
            "architecture_board_velocity": self._calculate_ab_velocity(board_id, start_date),
            "compliance_coverage": self._calculate_compliance_coverage(board_id, start_date),
            "stakeholder_concurrence_rate": self._calculate_stakeholder_concurrence(board_id, start_date),
            "architecture_debt": self._calculate_architecture_debt(board_id),
            "principle_adherence": self._calculate_principle_adherence(board_id, start_date),
            "business_value_realization": self._calculate_business_value(board_id, start_date),
            "complexity_metrics": self._calculate_complexity_metrics(board_id),
            "phase_distribution": self._calculate_phase_distribution(board_id),
            "approval_efficiency": self._calculate_approval_efficiency(board_id, start_date),
        }

    def _calculate_ab_velocity(self, board_id: int = None, start_date: datetime = None) -> Dict[str, Any]:
        """Calculate Architecture Board decision velocity."""

        query = ADMPhaseApproval.query.filter(
            ADMPhaseApproval.decision_date >= start_date,
            ADMPhaseApproval.decision.isnot(None)
        )

        if board_id:
            query = query.filter_by(board_id=board_id)

        decisions = query.all()

        if not decisions:
            return {"velocity_score": 0, "avg_decision_time_days": None, "total_decisions": 0}

        # Calculate average time from submission to decision
        decision_times = []
        for approval in decisions:
            if approval.requested_at and approval.decision_date:
                delta = approval.decision_date - approval.requested_at
                decision_times.append(delta.total_seconds() / 86400)  # Convert to days

        avg_decision_time = sum(decision_times) / len(decision_times) if decision_times else 0

        # Velocity score (lower is better, max 30 days = 0 score)
        velocity_score = max(0, 100 - (avg_decision_time / 30 * 100))

        return {
            "velocity_score": round(velocity_score, 1),
            "avg_decision_time_days": round(avg_decision_time, 1),
            "total_decisions": len(decisions),
            "decisions_by_outcome": {
                "approved": len([d for d in decisions if d.decision == "approved"]),
                "approved_with_conditions": len([d for d in decisions if d.decision == "approved_with_conditions"]),
                "rejected": len([d for d in decisions if d.decision == "rejected"]),
                "deferred": len([d for d in decisions if d.decision == "deferred"]),
            },
        }

    def _calculate_compliance_coverage(self, board_id: int = None, start_date: datetime = None) -> Dict[str, Any]:
        """Calculate compliance coverage across phase transitions."""

        # Get all checkpoints
        checkpoint_query = ADMComplianceCheckpoint.query.join(
            ADMPhaseApproval, ADMComplianceCheckpoint.approval_id == ADMPhaseApproval.id
        ).filter(ADMPhaseApproval.created_at >= start_date)

        if board_id:
            checkpoint_query = checkpoint_query.filter(ADMPhaseApproval.board_id == board_id)

        total_checkpoints = checkpoint_query.count()
        completed_checkpoints = checkpoint_query.filter(ADMComplianceCheckpoint.is_completed == True).count()
        verified_checkpoints = checkpoint_query.filter(ADMComplianceCheckpoint.verified == True).count()

        coverage_rate = (completed_checkpoints / total_checkpoints * 100) if total_checkpoints > 0 else 100
        verification_rate = (verified_checkpoints / completed_checkpoints * 100) if completed_checkpoints > 0 else 0

        return {
            "coverage_rate": round(coverage_rate, 1),
            "verification_rate": round(verification_rate, 1),
            "total_checkpoints": total_checkpoints,
            "completed_checkpoints": completed_checkpoints,
            "verified_checkpoints": verified_checkpoints,
            "by_category": self._get_checkpoint_breakdown(board_id, start_date),
        }

    def _get_checkpoint_breakdown(self, board_id: int = None, start_date: datetime = None) -> Dict[str, Any]:
        """Get checkpoint completion breakdown by category."""

        query = db.session.query(
            ADMComplianceCheckpoint.checkpoint_category,
            func.count(ADMComplianceCheckpoint.id).label("total"),
            func.sum(func.cast(ADMComplianceCheckpoint.is_completed, db.Integer)).label("completed")
        ).join(
            ADMPhaseApproval, ADMComplianceCheckpoint.approval_id == ADMPhaseApproval.id
        ).filter(ADMPhaseApproval.created_at >= start_date)

        if board_id:
            query = query.filter(ADMPhaseApproval.board_id == board_id)

        results = query.group_by(ADMComplianceCheckpoint.checkpoint_category).all()

        return {
            category: {
                "total": total,
                "completed": int(completed or 0),
                "rate": round((completed / total * 100), 1) if total > 0 else 0
            }
            for category, total, completed in results
        }

    def _calculate_stakeholder_concurrence(self, board_id: int = None, start_date: datetime = None) -> Dict[str, Any]:
        """Calculate stakeholder concurrence rates."""

        query = ADMStakeholderConcurrence.query.join(
            ADMPhaseApproval, ADMStakeholderConcurrence.approval_id == ADMPhaseApproval.id
        ).filter(ADMPhaseApproval.created_at >= start_date)

        if board_id:
            query = query.filter(ADMPhaseApproval.board_id == board_id)

        total_requests = query.count()
        approved = query.filter(ADMStakeholderConcurrence.status == "approved").count()
        rejected = query.filter(ADMStakeholderConcurrence.status == "rejected").count()
        pending = query.filter(ADMStakeholderConcurrence.status == "pending").count()

        concurrence_rate = (approved / total_requests * 100) if total_requests > 0 else 0

        return {
            "concurrence_rate": round(concurrence_rate, 1),
            "total_requests": total_requests,
            "approved": approved,
            "rejected": rejected,
            "pending": pending,
            "by_role": self._get_concurrence_by_role(board_id, start_date),
        }

    def _get_concurrence_by_role(self, board_id: int = None, start_date: datetime = None) -> Dict[str, Any]:
        """Get concurrence rates by stakeholder role."""

        query = db.session.query(
            ADMStakeholderConcurrence.stakeholder_role,
            func.count(ADMStakeholderConcurrence.id).label("total"),
            func.sum(func.cast(ADMStakeholderConcurrence.status == "approved", db.Integer)).label("approved")
        ).join(
            ADMPhaseApproval, ADMStakeholderConcurrence.approval_id == ADMPhaseApproval.id
        ).filter(ADMPhaseApproval.created_at >= start_date)

        if board_id:
            query = query.filter(ADMPhaseApproval.board_id == board_id)

        results = query.group_by(ADMStakeholderConcurrence.stakeholder_role).all()

        return {
            role: {
                "total": total,
                "approved": int(approved or 0),
                "rate": round((approved / total * 100), 1) if total > 0 else 0
            }
            for role, total, approved in results
        }

    def _calculate_architecture_debt(self, board_id: int = None) -> Dict[str, Any]:
        """Calculate architecture debt indicators."""

        # Cards in later phases without approval
        debt_query = KanbanCard.query.filter(
            KanbanCard.adm_phase_id.in_(
                db.session.query(ADMPhase.id).filter(ADMPhase.code.in_(["E", "F", "G"]))
            )
        )

        if board_id:
            debt_query = debt_query.filter_by(board_id=board_id)

        # Cards without approvals
        cards_without_approval = debt_query.filter(
            ~KanbanCard.id.in_(
                db.session.query(ADMPhaseApproval.card_id).filter(
                    ADMPhaseApproval.status.in_(["approved", "approved_with_conditions"])
                )
            )
        ).count()

        # Rejected approvals (debt risk)
        rejected_query = ADMPhaseApproval.query.filter_by(status="rejected")
        if board_id:
            rejected_query = rejected_query.filter_by(board_id=board_id)
        rejected_count = rejected_query.count()

        # Deferred decisions
        deferred_query = ADMPhaseApproval.query.filter_by(status="deferred")
        if board_id:
            deferred_query = deferred_query.filter_by(board_id=board_id)
        deferred_count = deferred_query.count()

        total_cards = KanbanCard.query.filter_by(board_id=board_id).count() if board_id else KanbanCard.query.count()

        debt_ratio = ((cards_without_approval + rejected_count + deferred_count) / total_cards * 100) if total_cards > 0 else 0

        return {
            "debt_score": round(min(100, debt_ratio * 2), 1),  # Scale to 0-100
            "cards_without_approval": cards_without_approval,
            "rejected_approvals": rejected_count,
            "deferred_decisions": deferred_count,
            "total_cards": total_cards,
            "debt_ratio": round(debt_ratio, 1),
        }

    def _calculate_principle_adherence(self, board_id: int = None, start_date: datetime = None) -> Dict[str, Any]:
        """Calculate architecture principle adherence score."""

        # Based on compliance checkpoints related to principles
        principle_checkpoints = ADMComplianceCheckpoint.query.filter(
            ADMComplianceCheckpoint.checkpoint_category == "governance"
        ).join(
            ADMPhaseApproval, ADMComplianceCheckpoint.approval_id == ADMPhaseApproval.id
        ).filter(ADMPhaseApproval.created_at >= start_date)

        if board_id:
            principle_checkpoints = principle_checkpoints.filter(ADMPhaseApproval.board_id == board_id)

        total = principle_checkpoints.count()
        completed = principle_checkpoints.filter(ADMComplianceCheckpoint.is_completed == True).count()

        adherence_score = (completed / total * 100) if total > 0 else 100

        return {
            "adherence_score": round(adherence_score, 1),
            "total_principle_checks": total,
            "completed_checks": completed,
        }

    def _calculate_business_value(self, board_id: int = None, start_date: datetime = None) -> Dict[str, Any]:
        """Calculate business value realization metrics."""

        # Completed cards (value realized)
        completed_query = KanbanCard.query.filter(
            KanbanCard.status == "done",
            KanbanCard.completed_at >= start_date
        )

        if board_id:
            completed_query = completed_query.filter_by(board_id=board_id)

        completed_cards = completed_query.count()

        # Total cards
        total_query = KanbanCard.query
        if board_id:
            total_query = total_query.filter_by(board_id=board_id)
        total_cards = total_query.count()

        # Value realization rate
        realization_rate = (completed_cards / total_cards * 100) if total_cards > 0 else 0

        # Avg time to value
        completion_times = []
        completed_cards_list = completed_query.all()
        for card in completed_cards_list:
            if card.created_at and card.completed_at:
                delta = card.completed_at - card.created_at
                completion_times.append(delta.total_seconds() / 86400)

        avg_time_to_value = sum(completion_times) / len(completion_times) if completion_times else 0

        return {
            "realization_rate": round(realization_rate, 1),
            "completed_cards": completed_cards,
            "total_cards": total_cards,
            "avg_time_to_value_days": round(avg_time_to_value, 1),
        }

    def _calculate_complexity_metrics(self, board_id: int = None) -> Dict[str, Any]:
        """Calculate complexity distribution across cards."""

        base_query = KanbanCard.query
        if board_id:
            base_query = base_query.filter_by(board_id=board_id)

        total = base_query.count()

        # By priority
        critical = base_query.filter_by(priority="critical").count()
        high = base_query.filter_by(priority="high").count()
        medium = base_query.filter_by(priority="medium").count()
        low = base_query.filter_by(priority="low").count()

        # Complexity score based on dependencies
        cards_with_deps = base_query.filter(KanbanCard.depends_on.isnot(None)).count()

        # Complexity score
        complexity_score = (
            (critical * 4) + (high * 3) + (medium * 2) + (low * 1)
        ) / max(total * 4, 1) * 100

        return {
            "complexity_score": round(complexity_score, 1),
            "distribution": {
                "critical": {"count": critical, "percentage": round(critical / total * 100, 1) if total > 0 else 0},
                "high": {"count": high, "percentage": round(high / total * 100, 1) if total > 0 else 0},
                "medium": {"count": medium, "percentage": round(medium / total * 100, 1) if total > 0 else 0},
                "low": {"count": low, "percentage": round(low / total * 100, 1) if total > 0 else 0},
            },
            "cards_with_dependencies": cards_with_deps,
            "total_cards": total,
        }

    # ------------------------------------------------------------------
    # Derived views: which elements a phase / deliverable produced, and which
    # phases an element belongs to. Read-only, computed from the card to
    # element links; every one is scoped to the organisation passed in.
    # ------------------------------------------------------------------

    @staticmethod
    def _linked_elements(organization_id, *card_filters) -> List[Dict[str, Any]]:
        if organization_id is None:
            return []
        cols = (ArchiMateElement.id, ArchiMateElement.name, ArchiMateElement.type, ArchiMateElement.layer)
        rows = (
            db.session.query(*cols)
            .join(CardArchiMateElement, CardArchiMateElement.archimate_element_id == ArchiMateElement.id)
            .join(KanbanCard, KanbanCard.id == CardArchiMateElement.card_id)
            .filter(
                KanbanCard.organization_id == organization_id,
                ArchiMateElement.organization_id == organization_id,
                *card_filters,
            )
            .distinct()
            .order_by(ArchiMateElement.id)
            .all()
        )
        return [{"id": r[0], "name": r[1], "type": r[2], "layer": r[3]} for r in rows]

    @staticmethod
    def elements_for_phase(phase, organization_id) -> List[Dict[str, Any]]:
        """Elements linked to any card of the phase (an ADMPhase, its id or its code)."""
        if isinstance(phase, ADMPhase):
            phase_id = phase.id
        elif isinstance(phase, int):
            phase_id = phase
        else:
            found = ADMPhase.query.filter_by(code=str(phase)).first()
            phase_id = found.id if found else None
        if phase_id is None:
            return []
        return ADMAnalyticsService._linked_elements(organization_id, KanbanCard.adm_phase_id == phase_id)

    @staticmethod
    def elements_for_deliverable(deliverable, organization_id) -> List[Dict[str, Any]]:
        """Elements linked to any card that names the deliverable (an ADMDeliverable or its name)."""
        name = getattr(deliverable, "name", deliverable)
        if not name:
            return []
        return ADMAnalyticsService._linked_elements(organization_id, KanbanCard.togaf_deliverable == str(name))

    @staticmethod
    def phase_for_element(element, organization_id) -> List[Dict[str, Any]]:
        """Every ADM phase the element belongs to through its cards, in phase order."""
        element_id = getattr(element, "id", element)
        if organization_id is None or element_id is None:
            return []
        rows = (
            db.session.query(ADMPhase, func.count(KanbanCard.id))
            .join(KanbanCard, KanbanCard.adm_phase_id == ADMPhase.id)
            .join(CardArchiMateElement, CardArchiMateElement.card_id == KanbanCard.id)
            .join(ArchiMateElement, ArchiMateElement.id == CardArchiMateElement.archimate_element_id)
            .filter(
                CardArchiMateElement.archimate_element_id == element_id,
                KanbanCard.organization_id == organization_id,
                ArchiMateElement.organization_id == organization_id,
            )
            .group_by(ADMPhase.id)
            .order_by(ADMPhase.order)
            .all()
        )
        return [
            {"phase_id": p.id, "code": p.code, "name": p.name, "order": p.order, "card_count": n}
            for p, n in rows
        ]

    def _calculate_phase_distribution(self, board_id: int = None) -> Dict[str, Any]:
        """Calculate card distribution across ADM phases."""

        phases = ADMPhase.query.filter_by(is_active=True).order_by(ADMPhase.order).all()

        if not phases:
            return {}

        phase_ids = [p.id for p in phases]

        # Batch load card counts per phase using GROUP BY to avoid N+1
        base_card_query = KanbanCard.query.filter(KanbanCard.adm_phase_id.in_(phase_ids))
        if board_id:
            base_card_query = base_card_query.filter_by(board_id=board_id)

        count_results = db.session.query(
            KanbanCard.adm_phase_id, func.count(KanbanCard.id)
        ).filter(KanbanCard.adm_phase_id.in_(phase_ids))
        if board_id:
            count_results = count_results.filter(KanbanCard.board_id == board_id)
        count_results = count_results.group_by(KanbanCard.adm_phase_id).all()
        count_map = dict(count_results)

        # Batch load aging card counts per phase using GROUP BY
        aging_threshold = datetime.utcnow() - timedelta(days=30)
        aging_results = db.session.query(
            KanbanCard.adm_phase_id, func.count(KanbanCard.id)
        ).filter(
            KanbanCard.adm_phase_id.in_(phase_ids),
            KanbanCard.updated_at < aging_threshold
        )
        if board_id:
            aging_results = aging_results.filter(KanbanCard.board_id == board_id)
        aging_results = aging_results.group_by(KanbanCard.adm_phase_id).all()
        aging_map = dict(aging_results)

        distribution = {}
        for phase in phases:
            count = count_map.get(phase.id, 0)
            aging_count = aging_map.get(phase.id, 0)

            distribution[phase.code] = {
                "name": phase.name,
                "count": count,
                "aging_count": aging_count,
                "health": "good" if aging_count == 0 else "warning" if aging_count < count / 2 else "critical"
            }

        return distribution

    def _calculate_approval_efficiency(self, board_id: int = None, start_date: datetime = None) -> Dict[str, Any]:
        """Calculate approval workflow efficiency."""

        # Approval throughput
        approval_query = ADMPhaseApproval.query.filter(ADMPhaseApproval.created_at >= start_date)
        if board_id:
            approval_query = approval_query.filter_by(board_id=board_id)

        total_approvals = approval_query.count()
        approved = approval_query.filter(ADMPhaseApproval.status.in_(["approved", "approved_with_conditions"])).count()
        rejected = approval_query.filter_by(status="rejected").count()
        pending = approval_query.filter(ADMPhaseApproval.status.in_(["draft", "submitted", "under_review"])).count()

        success_rate = (approved / (approved + rejected) * 100) if (approved + rejected) > 0 else 0

        # Bottleneck identification (approvals taking too long)
        old_pending = approval_query.filter(
            ADMPhaseApproval.status.in_(["submitted", "under_review"]),
            ADMPhaseApproval.created_at < datetime.utcnow() - timedelta(days=14)
        ).count()

        return {
            "success_rate": round(success_rate, 1),
            "total_approvals": total_approvals,
            "approved": approved,
            "rejected": rejected,
            "pending": pending,
            "old_pending": old_pending,
            "bottleneck_detected": old_pending > 0,
        }

    def get_kpi_trends(self, board_id: int = None, weeks: int = 12) -> Dict[str, Any]:
        """Get KPI trends over time."""

        trends = []
        for week in range(weeks):
            end_date = datetime.utcnow() - timedelta(weeks=week)
            start_date = end_date - timedelta(weeks=1)

            # Calculate weekly snapshot
            dashboard = self.get_governance_dashboard(board_id=board_id, days=7)

            trends.append({
                "week": weeks - week,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "velocity_score": dashboard["architecture_board_velocity"]["velocity_score"],
                "compliance_rate": dashboard["compliance_coverage"]["coverage_rate"],
                "concurrence_rate": dashboard["stakeholder_concurrence_rate"]["concurrence_rate"],
                "debt_score": dashboard["architecture_debt"]["debt_score"],
            })

        return {
            "trends": list(reversed(trends)),
            "trend_direction": self._calculate_trend_direction(trends),
        }

    def _calculate_trend_direction(self, trends: List[Dict]) -> Dict[str, str]:
        """Calculate overall trend direction for KPIs."""

        if len(trends) < 2:
            return {"velocity": "stable", "compliance": "stable", "debt": "stable"}

        first = trends[0]
        last = trends[-1]

        def direction(current, previous, higher_is_better=True):
            diff = current - previous
            if abs(diff) < 5:
                return "stable"
            if higher_is_better:
                return "improving" if diff > 0 else "declining"
            return "improving" if diff < 0 else "declining"

        return {
            "velocity": direction(last["velocity_score"], first["velocity_score"]),
            "compliance": direction(last["compliance_rate"], first["compliance_rate"]),
            "concurrence": direction(last["concurrence_rate"], first["concurrence_rate"]),
            "debt": direction(last["debt_score"], first["debt_score"], higher_is_better=False),
        }


# Singleton instance
adm_analytics_service = ADMAnalyticsService()
