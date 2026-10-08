"""
Confidence Threshold Controls & Review Queue Service

Provides comprehensive confidence threshold management and review queue system
with human-in-the-loop validation, quality assessment, and AI learning capabilities.

Features:
- Dynamic confidence threshold configuration
- Automated review queue management
- Human-in-the-loop validation workflow
- Quality assessment and scoring
- AI learning from human decisions
- Escalation and timeout management
- Performance analytics and statistics
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


from app import db

# Import models
from app.models.confidence_review import ReviewQueueItem, ReviewStatus
from app.utils.route_guards import load_entity
from decimal import Decimal

logger = logging.getLogger(__name__)


@dataclass
class ConfidenceThresholdConfig:
    """Configuration for confidence threshold evaluation."""

    threshold_name: str
    threshold_type: str
    minimum_confidence: float = 0.6
    auto_approval_threshold: float = 0.8
    rejection_threshold: float = 0.3
    context_type: Optional[str] = None
    context_value: Optional[str] = None
    domain_filter: Optional[str] = None
    requires_human_review: bool = True
    auto_review_enabled: bool = False
    review_queue_priority: int = 5
    validation_rules: Dict[str, Any] = None
    quality_gates: Dict[str, Any] = None


# Item-type → table mapping for resolving the reviewed entity's organisation.
# Used by add_to_review_queue and the backfill command.  Keep in sync with
# app/commands/backfill_review_queue_org.py:_ITEM_TYPE_TABLE.
_ITEM_TYPE_TABLE = {
    "capability_mapping": "application_components",
    "process_classification": "application_components",
    "process_mapping": "application_components",
    "vendor_analysis": "application_components",
    "taxonomy_validation": "application_components",
    "archimate_generation": "application_components",
    "archimate_element": "archimate_elements",
}


def _resolve_org_id_from_item(item_type: str, item_id: int) -> Optional[int]:
    """Return the organisation id for a reviewed entity, or None.

    Each item_type value refers to a single ``TenantMixin`` table whose
    ``id`` column maps to ``item_id``.  The table provides
    ``organization_id``.

    Returns None when the type is unrecognised or the row is missing.
    """
    table_name = _ITEM_TYPE_TABLE.get(item_type)
    if table_name is None or item_id is None or item_id <= 0:
        return None
    try:
        from app.extensions import db

        row = db.session.execute(
            db.text(
                f"SELECT organization_id FROM {table_name} WHERE id = :id"  # nosec B608 -- only fixed table and column names from a mapping in code are interpolated; values are bound parameters
            ),
            {"id": item_id},
        ).first()
        return row[0] if row else None
    except Exception:
        return None


@dataclass
class ReviewQueueItemData:
    """Data for review queue item creation."""

    item_type: str
    item_id: int
    item_name: str
    item_data: Dict[str, Any]
    confidence_score: float
    confidence_factors: Dict[str, Any]
    ai_model_used: str
    generation_timestamp: datetime
    threshold_name: str
    context_type: Optional[str] = None
    context_value: Optional[str] = None
    domain: Optional[str] = None


@dataclass
class ReviewDecisionData:
    """Data for review decision recording."""

    review_item_id: int
    decision_type: str
    decision_reason: str
    reviewer_id: int
    reviewer_role: str
    reviewer_experience_level: str
    quality_assessment: Dict[str, Any]
    identified_issues: List[str]
    suggested_improvements: List[str]
    human_confidence_estimate: float
    ai_accuracy_assessment: int
    correction_made: bool
    corrected_data: Dict[str, Any]
    review_duration_seconds: int


class ConfidenceReviewService:
    """
    Comprehensive confidence threshold and review queue management service.

    Provides dynamic threshold configuration, automated review queue management,
    human-in-the-loop validation, and AI learning capabilities.
    """

    def __init__(self):
        """Initialize the confidence review service."""
        self._init_default_thresholds()
        self._init_quality_criteria()
        self._init_escalation_rules()

    def _init_default_thresholds(self):
        """Initialize default confidence thresholds."""
        self.default_thresholds = {
            "global_ai_import": ConfidenceThresholdConfig(
                threshold_name="Global AI Import",
                threshold_type="global",
                minimum_confidence=0.6,
                auto_approval_threshold=0.8,
                rejection_threshold=0.3,
                requires_human_review=True,
                review_queue_priority=5,
            ),
            "capability_mapping_strategic": ConfidenceThresholdConfig(
                threshold_name="Strategic Capability Mapping",
                threshold_type="capability",
                minimum_confidence=0.7,
                auto_approval_threshold=0.85,
                rejection_threshold=0.4,
                context_type="capability_level",
                context_value="strategic",
                domain_filter="business",
                requires_human_review=True,
                review_queue_priority=3,  # Higher priority for strategic
            ),
            "capability_mapping_tactical": ConfidenceThresholdConfig(
                threshold_name="Tactical Capability Mapping",
                threshold_type="capability",
                minimum_confidence=0.6,
                auto_approval_threshold=0.8,
                rejection_threshold=0.3,
                context_type="capability_level",
                context_value="tactical",
                requires_human_review=True,
                review_queue_priority=5,
            ),
            "capability_mapping_operational": ConfidenceThresholdConfig(
                threshold_name="Operational Capability Mapping",
                threshold_type="capability",
                minimum_confidence=0.5,
                auto_approval_threshold=0.75,
                rejection_threshold=0.25,
                context_type="capability_level",
                context_value="operational",
                requires_human_review=False,  # Can auto-approve operational
                review_queue_priority=7,
            ),
            "apqc_process_classification": ConfidenceThresholdConfig(
                threshold_name="APQC Process Classification",
                threshold_type="process",
                minimum_confidence=0.65,
                auto_approval_threshold=0.8,
                rejection_threshold=0.35,
                requires_human_review=True,
                review_queue_priority=5,
            ),
            "vendor_product_analysis": ConfidenceThresholdConfig(
                threshold_name="Vendor Product Analysis",
                threshold_type="vendor",
                minimum_confidence=0.7,
                auto_approval_threshold=0.85,
                rejection_threshold=0.4,
                requires_human_review=True,
                review_queue_priority=4,
            ),
            "archimate_generation": ConfidenceThresholdConfig(
                threshold_name="ArchiMate Element Generation",
                threshold_type="archimate",
                minimum_confidence=0.6,
                auto_approval_threshold=0.8,
                rejection_threshold=0.3,
                requires_human_review=True,
                review_queue_priority=6,
            ),
        }

    def _init_quality_criteria(self):
        """Initialize quality assessment criteria."""
        self.quality_criteria = {
            "capability_mapping": {
                "accuracy": {
                    "description": "Accuracy of capability assignment",
                    "weight": 0.4,
                    "criteria": [
                        "correct_capability_level",
                        "appropriate_domain",
                        "logical_relationships",
                    ],
                },
                "completeness": {
                    "description": "Completeness of mapping information",
                    "weight": 0.3,
                    "criteria": [
                        "full_capability_details",
                        "confidence_justification",
                        "context_information",
                    ],
                },
                "relevance": {
                    "description": "Relevance to business context",
                    "weight": 0.3,
                    "criteria": [
                        "business_alignment",
                        "strategic_importance",
                        "operational_relevance",
                    ],
                },
            },
            "process_classification": {
                "accuracy": {
                    "description": "Accuracy of APQC process assignment",
                    "weight": 0.5,
                    "criteria": [
                        "correct_process_level",
                        "appropriate_category",
                        "hierarchical_placement",
                    ],
                },
                "completeness": {
                    "description": "Completeness of process information",
                    "weight": 0.3,
                    "criteria": ["process_details", "rationale", "confidence_factors"],
                },
                "relevance": {
                    "description": "Relevance to application functionality",
                    "weight": 0.2,
                    "criteria": ["functional_alignment", "process_fit", "business_context"],
                },
            },
            "vendor_analysis": {
                "accuracy": {
                    "description": "Accuracy of vendor/product identification",
                    "weight": 0.6,
                    "criteria": ["correct_vendor", "accurate_product", "proper_version"],
                },
                "completeness": {
                    "description": "Completeness of vendor information",
                    "weight": 0.2,
                    "criteria": ["vendor_details", "product_specifications", "market_context"],
                },
                "relevance": {
                    "description": "Relevance to application technology stack",
                    "weight": 0.2,
                    "criteria": ["technology_alignment", "vendor_relationship", "strategic_fit"],
                },
            },
            "archimate_generation": {
                "accuracy": {
                    "description": "Accuracy of ArchiMate element generation",
                    "weight": 0.5,
                    "criteria": [
                        "correct_element_types",
                        "proper_relationships",
                        "valid_structure",
                    ],
                },
                "completeness": {
                    "description": "Completeness of architectural model",
                    "weight": 0.3,
                    "criteria": ["element_details", "relationship_descriptions", "layer_coverage"],
                },
                "relevance": {
                    "description": "Relevance to enterprise architecture",
                    "weight": 0.2,
                    "criteria": ["business_alignment", "technical_accuracy", "strategic_value"],
                },
            },
        }

    def _init_escalation_rules(self):
        """Initialize escalation rules for review queue."""
        self.escalation_rules = {
            "timeout_hours": {
                "high_priority": 4,  # 4 hours for high priority
                "medium_priority": 24,  # 24 hours for medium priority
                "low_priority": 72,  # 72 hours for low priority
            },
            "escalation_triggers": {
                "review_timeout": True,
                "quality_below_threshold": True,
                "multiple_rejections": True,
                "stakeholder_dispute": True,
            },
            "escalation_levels": {
                0: "reviewer",  # Initial reviewer
                1: "senior_reviewer",  # Senior reviewer
                2: "architect",  # Enterprise architect
                3: "admin",  # System administrator
            },
        }

    # ------------------------------------------------------------------
    # Wrapper methods: bridge route-expected names → actual service names
    # ------------------------------------------------------------------

    def get_pending_reviews(
        self,
        user_id: int = None,
        status: str = "pending",
        page: int = 1,
        per_page: int = 50,
    ) -> list:
        """Return review queue items as ORM objects for the current tenant."""
        from app.models.confidence_review import ReviewQueueItem, ReviewStatus

        query = ReviewQueueItem.query
        if status:
            query = query.filter(ReviewQueueItem.status == ReviewStatus(status))
        if user_id:
            query = query.filter(ReviewQueueItem.assigned_to_id == user_id)
        query = query.order_by(
            ReviewQueueItem.review_priority.asc(), ReviewQueueItem.created_at.asc()
        )
        return query.limit(per_page).all()

    def get_queue_statistics(self) -> Dict[str, Any]:
        """Return review statistics (wrapper around get_review_statistics)."""
        return self.get_review_statistics()

    def approve_item(self, item_id: int, reviewer_id: int, **kwargs) -> Dict[str, Any]:
        """Approve a review item (wrapper around submit_review_decision)."""
        from app.models.confidence_review import ReviewQueueItem, ReviewStatus

        # Ensure item is in IN_REVIEW state first
        review_item = load_entity(ReviewQueueItem, item_id)
        if not review_item:
            return {"success": False, "error": "Review item not found"}
        if review_item.status == ReviewStatus.PENDING:
            self.assign_review_item(item_id, reviewer_id)

        decision = ReviewDecisionData(
            review_item_id=item_id,
            decision_type="approve",
            decision_reason=kwargs.get("decision_reason", "Approved by reviewer"),
            reviewer_id=reviewer_id,
            reviewer_role=kwargs.get("reviewer_role", "architect"),
            reviewer_experience_level=kwargs.get("reviewer_experience_level", "senior"),
            quality_assessment=kwargs.get("quality_assessment", {}),
            identified_issues=kwargs.get("identified_issues", []),
            suggested_improvements=kwargs.get("suggested_improvements", []),
            human_confidence_estimate=kwargs.get("human_confidence_estimate", 0.9),
            ai_accuracy_assessment=kwargs.get("ai_accuracy_assessment", 4),
            correction_made=kwargs.get("correction_made", False),
            corrected_data=kwargs.get("corrected_data", {}),
            review_duration_seconds=kwargs.get("review_duration_seconds", 30),
        )
        return self.submit_review_decision(decision)

    def reject_item(self, item_id: int, reviewer_id: int, **kwargs) -> Dict[str, Any]:
        """Reject a review item (wrapper around submit_review_decision)."""
        from app.models.confidence_review import ReviewQueueItem, ReviewStatus

        review_item = load_entity(ReviewQueueItem, item_id)
        if not review_item:
            return {"success": False, "error": "Review item not found"}
        if review_item.status == ReviewStatus.PENDING:
            self.assign_review_item(item_id, reviewer_id)

        decision = ReviewDecisionData(
            review_item_id=item_id,
            decision_type="reject",
            decision_reason=kwargs.get("decision_reason", "Rejected by reviewer"),
            reviewer_id=reviewer_id,
            reviewer_role=kwargs.get("reviewer_role", "architect"),
            reviewer_experience_level=kwargs.get("reviewer_experience_level", "senior"),
            quality_assessment=kwargs.get("quality_assessment", {}),
            identified_issues=kwargs.get("identified_issues", []),
            suggested_improvements=kwargs.get("suggested_improvements", []),
            human_confidence_estimate=kwargs.get("human_confidence_estimate", 0.3),
            ai_accuracy_assessment=kwargs.get("ai_accuracy_assessment", 2),
            correction_made=kwargs.get("correction_made", False),
            corrected_data=kwargs.get("corrected_data", {}),
            review_duration_seconds=kwargs.get("review_duration_seconds", 30),
        )
        return self.submit_review_decision(decision)

    def get_current_thresholds(self) -> List[Dict[str, Any]]:
        """Return active confidence thresholds (wrapper around get_confidence_thresholds)."""
        return self.get_confidence_thresholds(is_active=True)

    def create_threshold(
        self,
        threshold_name: str,
        threshold_type: str,
        minimum_confidence: float = 0.5,
        auto_approval_threshold: float = 0.8,
        rejection_threshold: float = 0.3,
        requires_human_review: bool = True,
        user_id: int = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a threshold from keyword args (wrapper around create_confidence_threshold)."""
        config = ConfidenceThresholdConfig(
            threshold_name=threshold_name,
            threshold_type=threshold_type,
            minimum_confidence=minimum_confidence,
            auto_approval_threshold=auto_approval_threshold,
            rejection_threshold=rejection_threshold,
            requires_human_review=requires_human_review,
        )
        return self.create_confidence_threshold(config, user_id)

    # ------------------------------------------------------------------
    # Core methods
    # ------------------------------------------------------------------

    def create_confidence_threshold(
        self, config: ConfidenceThresholdConfig, user_id: int
    ) -> Dict[str, Any]:
        """
        Create a new confidence threshold configuration.

        Args:
            config: Threshold configuration
            user_id: User ID creating the threshold

        Returns:
            Dictionary with creation result
        """
        try:
            from app.models.confidence_review import ConfidenceThreshold

            # Check for duplicate threshold name
            existing = ConfidenceThreshold.query.filter_by(
                threshold_name=config.threshold_name
            ).first()
            if existing:
                return {
                    "success": False,
                    "error": f'Threshold with name "{config.threshold_name}" already exists',
                }

            # Create threshold
            threshold = ConfidenceThreshold(
                threshold_name=config.threshold_name,
                threshold_type=config.threshold_type,
                minimum_confidence=config.minimum_confidence,
                auto_approval_threshold=config.auto_approval_threshold,
                rejection_threshold=config.rejection_threshold,
                context_type=config.context_type,
                context_value=config.context_value,
                domain_filter=config.domain_filter,
                requires_human_review=config.requires_human_review,
                auto_review_enabled=config.auto_review_enabled,
                review_queue_priority=config.review_queue_priority,
                validation_rules=json.dumps(config.validation_rules or {}),
                quality_gates=json.dumps(config.quality_gates or {}),
                created_by_id=user_id,
            )

            db.session.add(threshold)
            db.session.commit()

            return {"success": True, "threshold_id": threshold.id, "threshold": threshold.to_dict()}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating confidence threshold: {e}")
            return {"success": False, "error": str(e)}

    def evaluate_confidence_threshold(self, item_data: ReviewQueueItemData) -> Dict[str, Any]:
        """
        Evaluate item against confidence thresholds and determine review action.

        Args:
            item_data: Review queue item data

        Returns:
            Dictionary with evaluation result and action
        """
        try:

            # Find applicable threshold
            threshold = self._find_applicable_threshold(
                item_data.item_type,
                item_data.context_type,
                item_data.context_value,
                item_data.domain,
            )

            if not threshold:
                # Use default threshold
                threshold = self._get_default_threshold(item_data.item_type)

            # Evaluate confidence score
            evaluation_result = self._evaluate_confidence_score(
                item_data.confidence_score, threshold, item_data.confidence_factors
            )

            # Determine action
            action = self._determine_review_action(evaluation_result, threshold, item_data)

            return {
                "success": True,
                "threshold_id": threshold.id if threshold else None,
                "threshold_name": threshold.threshold_name if threshold else "default",
                "evaluation_result": evaluation_result,
                "action": action,
                "requires_review": action["action"] in ["queue_for_review", "reject"],
                "auto_approve": action["action"] == "auto_approve",
            }

        except Exception as e:
            logger.error(f"Error evaluating confidence threshold: {e}")
            return {"success": False, "error": str(e)}

    def _find_applicable_threshold(
        self,
        item_type: str,
        context_type: Optional[str],
        context_value: Optional[str],
        domain: Optional[str],
    ) -> Optional[Any]:
        """Find the most specific applicable threshold for an item."""
        try:
            from app.models.confidence_review import ConfidenceThreshold

            # Build query for specific threshold
            query = ConfidenceThreshold.query.filter_by(threshold_type=item_type, is_active=True)

            # Add context filters
            if context_type:
                query = query.filter_by(context_type=context_type)

            if context_value:
                query = query.filter_by(context_value=context_value)

            if domain:
                query = query.filter_by(domain_filter=domain)

            # Order by specificity (context_value > context_type > domain > general)
            query = query.order_by(
                ConfidenceThreshold.context_value.desc().nulls_last(),
                ConfidenceThreshold.context_type.desc().nulls_last(),
                ConfidenceThreshold.domain_filter.desc().nulls_last(),
            )

            return query.first()

        except Exception as e:
            logger.error(f"Error finding applicable threshold: {e}")
            return None

    def _get_default_threshold(self, item_type: str) -> Any:
        """Get default threshold for item type."""
        try:
            from app.models.confidence_review import ConfidenceThreshold

            return ConfidenceThreshold.query.filter_by(
                threshold_type="global", is_active=True
            ).first()

        except Exception as e:
            logger.error(f"Error getting default threshold: {e}")
            return None

    def _evaluate_confidence_score(
        self, confidence_score: float, threshold: Any, confidence_factors: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Evaluate confidence score against threshold."""
        evaluation = {
            "confidence_score": confidence_score,
            "threshold_minimum": float(threshold.minimum_confidence),
            "auto_approval_threshold": float(threshold.auto_approval_threshold),
            "rejection_threshold": float(threshold.rejection_threshold),
            "meets_minimum": confidence_score >= float(threshold.minimum_confidence),
            "meets_auto_approval": confidence_score >= float(threshold.auto_approval_threshold),
            "below_rejection": confidence_score < float(threshold.rejection_threshold),
            "confidence_gap": confidence_score - float(threshold.minimum_confidence),
            "factors": confidence_factors,
        }

        # Add quality assessment if available
        if threshold.quality_gates:
            quality_gates = json.loads(threshold.quality_gates)
            evaluation["quality_gates_met"] = self._evaluate_quality_gates(
                confidence_factors, quality_gates
            )

        return evaluation

    def _determine_review_action(
        self, evaluation_result: Dict[str, Any], threshold: Any, item_data: ReviewQueueItemData
    ) -> Dict[str, Any]:
        """Determine the appropriate review action based on evaluation."""
        confidence_score = evaluation_result["confidence_score"]

        # Auto-reject if below rejection threshold
        if evaluation_result["below_rejection"]:
            return {
                "action": "reject",
                "reason": f'Confidence score {confidence_score} below rejection threshold {evaluation_result["rejection_threshold"]}',
                "priority": "low",
            }

        # Auto-approve if above auto-approval threshold and no human review required
        if (
            evaluation_result["meets_auto_approval"]
            and not threshold.requires_human_review
            and not evaluation_result.get("quality_gates_failed", False)
        ):
            return {
                "action": "auto_approve",
                "reason": f'Confidence score {confidence_score} above auto-approval threshold {evaluation_result["auto_approval_threshold"]}',
                "priority": "low",
            }

        # Queue for review
        return {
            "action": "queue_for_review",
            "reason": f"Confidence score {confidence_score} requires human review",
            "priority": self._calculate_review_priority(threshold, evaluation_result, item_data),
            "estimated_review_time": self._estimate_review_time(threshold, item_data),
        }

    def _calculate_review_priority(
        self, threshold: Any, evaluation_result: Dict[str, Any], item_data: ReviewQueueItemData
    ) -> int:
        """Calculate review priority based on various factors."""
        base_priority = threshold.review_queue_priority

        # Adjust based on confidence gap
        confidence_gap = evaluation_result["confidence_gap"]
        if confidence_gap < 0:
            base_priority -= 1  # Lower priority if below minimum
        elif confidence_gap > 0.2:
            base_priority += 1  # Higher priority if well above minimum

        # Adjust based on item type
        if item_data.item_type == "capability_mapping" and item_data.context_value == "strategic":
            base_priority -= 2  # High priority for strategic capabilities

        # Adjust based on domain
        if item_data.domain == "business":
            base_priority -= 1  # Higher priority for business domain

        # Ensure priority is within valid range
        return max(1, min(10, base_priority))

    def _estimate_review_time(self, threshold: Any, item_data: ReviewQueueItemData) -> int:
        """Estimate review time in hours."""
        base_time = 2  # Base 2 hours

        # Adjust based on item type
        type_multipliers = {
            "capability_mapping": 1.5,
            "process_classification": 1.0,
            "vendor_analysis": 1.2,
            "archimate_generation": 2.0,
        }

        multiplier = type_multipliers.get(item_data.item_type, 1.0)
        estimated_time = int(base_time * multiplier)

        return estimated_time

    def _evaluate_quality_gates(
        self, confidence_factors: Dict[str, Any], quality_gates: Dict[str, Any]
    ) -> bool:
        """Evaluate quality gates against confidence factors."""
        # Simplified quality gate evaluation
        # In production, this would be more sophisticated
        required_factors = quality_gates.get("required_factors", [])

        for factor in required_factors:
            if factor not in confidence_factors:
                return False

        return True

    def add_to_review_queue(
        self, item_data: ReviewQueueItemData, evaluation_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Add item to review queue based on evaluation result.

        Args:
            item_data: Review queue item data
            evaluation_result: Confidence evaluation result

        Returns:
            Dictionary with queue addition result
        """
        try:
            from app.models.confidence_review import ReviewQueueItem, ReviewStatus

            # Calculate review deadline
            review_priority = evaluation_result["action"].get("priority", 5)
            estimated_review_time = evaluation_result["action"].get("estimated_review_time", 24)
            review_deadline = datetime.utcnow() + timedelta(hours=estimated_review_time)

            # Resolve the reviewed item's organisation so new rows always have an
            # org even outside a request context (background jobs, CLI seeds).
            # The TenantMixin before_flush listener stamps organization_id from
            # g.current_org_id when that is set; this explicit lookup covers the
            # paths that lack one and is a no-op when g is already set.
            resolved_org_id = _resolve_org_id_from_item(
                item_data.item_type, item_data.item_id
            )

            # Create review queue item
            review_item = ReviewQueueItem(
                threshold_id=evaluation_result.get("threshold_id"),
                item_type=item_data.item_type,
                item_id=item_data.item_id,
                item_name=item_data.item_name,
                item_data=json.dumps(item_data.item_data),
                confidence_score=item_data.confidence_score,
                confidence_factors=json.dumps(item_data.confidence_factors),
                ai_model_used=item_data.ai_model_used,
                generation_timestamp=item_data.generation_timestamp,
                status=ReviewStatus.PENDING,
                review_priority=review_priority,
                review_deadline=review_deadline,
                organization_id=resolved_org_id,
            )

            db.session.add(review_item)
            db.session.flush()

            # Consolidation: repointed to the approval creator so this item also
            # surfaces in the one organisation-wide approval inbox, alongside
            # assistant-raised and blueprint proposals. ReviewQueueItem stays
            # the system of record for the review-specific fields (confidence
            # factors, quality ratings) and its own dedicated review UI; this
            # is additive, not a replacement (dual-write during the wave-1
            # consolidation — see docs/adr note recorded in the PR). Skipped
            # when no organisation could be resolved: an unattributed item is
            # already unreachable in the review queue itself today, so a
            # paired approval row would be equally unreachable.
            if resolved_org_id is not None:
                from app.modules.ai_chat.services.ai_chat_approval_service import (
                    create_approval_record,
                )

                approval = create_approval_record(
                    organization_id=resolved_org_id,
                    operation_type="review",
                    entity_type=item_data.item_type,
                    entity_id=item_data.item_id,
                    summary=f"Confidence review: {item_data.item_name}",
                    operation_payload={
                        "review_queue_item_id": review_item.id,
                        "item_type": item_data.item_type,
                        "item_name": item_data.item_name,
                        "confidence_score": float(item_data.confidence_score)
                        if item_data.confidence_score is not None else None,
                    },
                    source_table="review_queue_items",
                    source_id=review_item.id,
                    # A confidence-review item's own deadline (hours, not the
                    # chat-approval default of 15 minutes) is its overdue
                    # marker here too, so escalation timing matches what the
                    # review queue already promised the reviewer.
                    expiry_minutes=max(
                        1, int((review_deadline - datetime.utcnow()).total_seconds() // 60)
                    ),
                )
                # Mark superseded immediately (consolidation pattern step 5):
                # without this the backfill's idempotency check (`WHERE
                # retired_into_id IS NULL`) would re-copy this row and create
                # a second approval for it the next time it runs.
                review_item.retired_into_id = approval.id
                db.session.flush()

            db.session.commit()

            return {
                "success": True,
                "review_item_id": review_item.id,
                "status": review_item.status.value,
                "review_priority": review_item.review_priority,
                "review_deadline": review_item.review_deadline.isoformat(),
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error adding item to review queue: {e}")
            return {"success": False, "error": str(e)}

    def get_review_queue(
        self,
        status: Optional[str] = None,
        item_type: Optional[str] = None,
        assigned_to_id: Optional[int] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        Get review queue items with optional filtering.

        Args:
            status: Optional status filter
            item_type: Optional item type filter
            assigned_to_id: Optional assigned reviewer filter
            limit: Maximum results

        Returns:
            Dictionary with review queue items
        """
        try:
            from app.models.confidence_review import ReviewQueueItem, ReviewStatus

            query = ReviewQueueItem.query

            if status:
                query = query.filter(ReviewQueueItem.status == ReviewStatus(status))

            if item_type:
                query = query.filter(ReviewQueueItem.item_type == item_type)

            if assigned_to_id:
                query = query.filter(ReviewQueueItem.assigned_to_id == assigned_to_id)

            # Order by priority and creation date
            query = query.order_by(
                ReviewQueueItem.review_priority.asc(), ReviewQueueItem.created_at.asc()
            )

            items = query.limit(limit).all()

            return {
                "success": True,
                "items": [item.to_dict() for item in items],
                "total_items": len(items),
            }

        except Exception as e:
            logger.error(f"Error getting review queue: {e}")
            return {"success": False, "error": str(e)}

    def assign_review_item(self, review_item_id: int, reviewer_id: int) -> Dict[str, Any]:
        """
        Assign a review queue item to a reviewer.

        Args:
            review_item_id: Review item ID
            reviewer_id: Reviewer user ID

        Returns:
            Dictionary with assignment result
        """
        try:
            from app.models.confidence_review import ReviewQueueItem, ReviewStatus

            review_item = load_entity(ReviewQueueItem, review_item_id)
            if not review_item:
                return {"success": False, "error": "Review item not found"}

            if review_item.status != ReviewStatus.PENDING:
                return {
                    "success": False,
                    "error": f"Item cannot be assigned, current status: {review_item.status.value}",
                }

            # Assign to reviewer
            review_item.status = ReviewStatus.IN_REVIEW
            review_item.assigned_to_id = reviewer_id
            review_item.assigned_at = datetime.utcnow()

            db.session.commit()

            return {
                "success": True,
                "review_item_id": review_item_id,
                "assigned_to_id": reviewer_id,
                "assigned_at": review_item.assigned_at.isoformat(),
                "status": review_item.status.value,
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error assigning review item {review_item_id}: {e}")
            return {"success": False, "error": str(e)}

    def submit_review_decision(self, decision_data: ReviewDecisionData) -> Dict[str, Any]:
        """
        Submit a review decision for a queue item.

        Args:
            decision_data: Review decision data

        Returns:
            Dictionary with decision submission result
        """
        try:
            from app.models.confidence_review import ReviewDecision, ReviewQueueItem, ReviewStatus

            review_item = load_entity(ReviewQueueItem, decision_data.review_item_id)
            if not review_item:
                return {"success": False, "error": "Review item not found"}

            if review_item.status != ReviewStatus.IN_REVIEW:
                return {
                    "success": False,
                    "error": f"Item cannot be reviewed, current status: {review_item.status.value}",
                }

            # Update review item
            review_item.status = self._map_decision_to_status(decision_data.decision_type)
            review_item.reviewed_by_id = decision_data.reviewer_id
            review_item.reviewed_at = datetime.utcnow()
            review_item.review_notes = decision_data.decision_reason
            review_item.review_decision = decision_data.decision_type
            review_item.quality_score = self._calculate_quality_score(
                decision_data.quality_assessment
            )
            review_item.accuracy_rating = decision_data.ai_accuracy_assessment

            if decision_data.decision_type == "reject":
                review_item.rejection_reason = decision_data.decision_reason

            # Create review decision record
            review_decision = ReviewDecision(
                review_item_id=decision_data.review_item_id,
                # Take the queue item's own organisation rather than relying on
                # request context, which this service does not otherwise touch.
                organization_id=review_item.organization_id,
                decision_type=decision_data.decision_type,
                decision_reason=decision_data.decision_reason,
                confidence_adjustment=Decimal(str(decision_data.human_confidence_estimate))
                - Decimal(str(review_item.confidence_score)),
                quality_assessment=json.dumps(decision_data.quality_assessment),
                identified_issues=json.dumps(decision_data.identified_issues),
                suggested_improvements=json.dumps(decision_data.suggested_improvements),
                human_confidence_estimate=decision_data.human_confidence_estimate,
                ai_accuracy_assessment=decision_data.ai_accuracy_assessment,
                correction_made=decision_data.correction_made,
                corrected_data=json.dumps(decision_data.corrected_data)
                if decision_data.correction_made
                else None,
                reviewer_id=decision_data.reviewer_id,
                reviewer_role=decision_data.reviewer_role,
                reviewer_experience_level=decision_data.reviewer_experience_level,
                review_duration_seconds=decision_data.review_duration_seconds,
            )

            db.session.add(review_decision)
            db.session.commit()

            # Update AI learning based on decision
            self._update_ai_learning(decision_data, review_item)

            return {
                "success": True,
                "review_decision_id": review_decision.id,
                "review_item_id": decision_data.review_item_id,
                "status": review_item.status.value,
                "decision_type": decision_data.decision_type,
            }

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error submitting review decision: {e}")
            return {"success": False, "error": str(e)}

    def _map_decision_to_status(self, decision_type: str) -> ReviewStatus:
        """Map decision type to review status."""
        status_mapping = {
            "approve": ReviewStatus.APPROVED,
            "reject": ReviewStatus.REJECTED,
            "request_revision": ReviewStatus.REQUIRES_REVISION,
            "auto_approve": ReviewStatus.AUTO_APPROVED,
        }
        return status_mapping.get(decision_type, ReviewStatus.PENDING)

    def _calculate_quality_score(self, quality_assessment: Dict[str, Any]) -> float:
        """Calculate overall quality score from assessment."""
        if not quality_assessment:
            return 0.0

        total_score = 0.0
        total_weight = 0.0

        for criterion, assessment in quality_assessment.items():
            if isinstance(assessment, dict) and "score" in assessment and "weight" in assessment:
                total_score += assessment["score"] * assessment["weight"]
                total_weight += assessment["weight"]

        return total_score / total_weight if total_weight > 0 else 0.0

    def _update_ai_learning(self, decision_data: ReviewDecisionData, review_item: ReviewQueueItem):
        """Update AI learning based on human review decisions."""
        try:
            # This would integrate with AI learning systems
            # For now, just log the learning data
            logger.info(
                f"AI Learning Update: Decision={decision_data.decision_type}, "
                f"AI_Confidence={review_item.confidence_score}, "
                f"Human_Confidence={decision_data.human_confidence_estimate}, "
                f"Accuracy={decision_data.ai_accuracy_assessment}"
            )

            # In production, this would:
            # 1. Update confidence thresholds based on human feedback
            # 2. Retrain AI models with corrected data
            # 3. Adjust quality criteria based on identified issues
            # 4. Update review priority calculations

        except Exception as e:
            logger.error(f"Error updating AI learning: {e}")

    def get_review_statistics(
        self, date_from: Optional[datetime] = None, date_to: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Get review queue statistics with optional date filtering.

        Args:
            date_from: Optional start date filter
            date_to: Optional end date filter

        Returns:
            Dictionary with review statistics
        """
        try:
            from app.models.confidence_review import ReviewQueueItem, ReviewStatus

            query = ReviewQueueItem.query

            if date_from:
                query = query.filter(ReviewQueueItem.created_at >= date_from)

            if date_to:
                query = query.filter(ReviewQueueItem.created_at <= date_to)

            items = query.all()

            if not items:
                return {
                    "total_items": 0,
                    "pending_items": 0,
                    "in_review_items": 0,
                    "approved_items": 0,
                    "rejected_items": 0,
                    "auto_approved_items": 0,
                    "average_confidence_score": 0.0,
                    "average_quality_score": 0.0,
                    "approval_rate": 0.0,
                }

            # Calculate statistics
            total_items = len(items)
            pending_items = len([i for i in items if i.status == ReviewStatus.PENDING])
            in_review_items = len([i for i in items if i.status == ReviewStatus.IN_REVIEW])
            approved_items = len([i for i in items if i.status == ReviewStatus.APPROVED])
            rejected_items = len([i for i in items if i.status == ReviewStatus.REJECTED])
            auto_approved_items = len([i for i in items if i.status == ReviewStatus.AUTO_APPROVED])

            # Calculate averages
            confidence_scores = [i.confidence_score for i in items if i.confidence_score]
            quality_scores = [i.quality_score for i in items if i.quality_score]

            avg_confidence = (
                sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.0
            )
            avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0.0

            # Calculate approval rate
            completed_items = approved_items + rejected_items + auto_approved_items
            approval_rate = (
                (approved_items + auto_approved_items) / completed_items * 100
                if completed_items > 0
                else 0.0
            )

            return {
                "total_items": total_items,
                "pending_items": pending_items,
                "in_review_items": in_review_items,
                "approved_items": approved_items,
                "rejected_items": rejected_items,
                "auto_approved_items": auto_approved_items,
                "average_confidence_score": avg_confidence,
                "average_quality_score": avg_quality,
                "approval_rate": approval_rate,
            }

        except Exception as e:
            logger.error(f"Error getting review statistics: {e}")
            return {"error": str(e)}

    def get_confidence_thresholds(
        self, threshold_type: Optional[str] = None, is_active: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
        """
        Get confidence thresholds with optional filtering.

        Args:
            threshold_type: Optional threshold type filter
            is_active: Optional active status filter

        Returns:
            List of confidence thresholds
        """
        try:
            from app.models.confidence_review import ConfidenceThreshold

            query = ConfidenceThreshold.query

            if threshold_type:
                query = query.filter_by(threshold_type=threshold_type)

            if is_active is not None:
                query = query.filter_by(is_active=is_active)

            thresholds = query.order_by(ConfidenceThreshold.threshold_name).all()

            return [threshold.to_dict() for threshold in thresholds]

        except Exception as e:
            logger.error(f"Error getting confidence thresholds: {e}")
            return []

    def update_confidence_threshold(
        self, threshold_id: int, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update a confidence threshold configuration.

        Args:
            threshold_id: Threshold ID
            updates: Dictionary of updates

        Returns:
            Dictionary with update result
        """
        try:
            from app.models.confidence_review import ConfidenceThreshold

            threshold = ConfidenceThreshold.query.get(threshold_id)
            if not threshold:
                return {"success": False, "error": "Threshold not found"}

            # Update fields
            for field, value in updates.items():
                if hasattr(threshold, field):
                    setattr(threshold, field, value)

            threshold.updated_at = datetime.utcnow()
            db.session.commit()

            return {"success": True, "threshold_id": threshold_id, "threshold": threshold.to_dict()}

        except Exception as e:
            db.session.rollback()
            logger.error(f"Error updating confidence threshold {threshold_id}: {e}")
            return {"success": False, "error": str(e)}
