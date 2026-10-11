"""
ARB Intake Service

Implements governance for ARB review intake to prevent queue flooding.
Adds ARB chair approval for intake, review type validation,
automatic reviewer routing, review scope assessment.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


from app import db
from app.models.architecture_review_board import (
    ARBReviewItem,
    ReviewType,
)
from app.models.adm_kanban import KanbanCard
from app.services.adm_audit_service import ADMAuditAction, adm_audit_service

logger = logging.getLogger(__name__)


class ARBIntakeError(Exception):
    """Exception raised when ARB intake validation fails."""

    def __init__(self, message: str, code: str = None, details: Dict = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)


class ARBIntakeService:
    """
    Service for managing ARB review intake with governance controls.

    Features:
    - ARB chair approval for intake
    - Review type validation
    - Automatic reviewer routing
    - Review scope assessment
    - Rate limiting to prevent queue flooding
    - Batching logic for multiple cards
    """

    # Rate limiting configuration
    MAX_REVIEWS_PER_DAY = 10  # Per user
    MAX_REVIEWS_PER_WEEK = 30  # Per board
    MAX_BATCH_SIZE = 5  # Cards per batch review

    # Review type requirements
    REVIEW_TYPE_REQUIREMENTS = {
        ReviewType.SOLUTION_DESIGN.value: {
            "requires_architecture_diagram": True,
            "min_description_length": 100,
            "togaf_phase_required": True,
        },
        ReviewType.ARCHITECTURE_CHANGE.value: {
            "requires_adr": True,
            "min_description_length": 200,
            "togaf_phase_required": True,
        },
        ReviewType.TECHNOLOGY_SELECTION.value: {
            "requires_evaluation_criteria": True,
            "min_description_length": 150,
        },
        ReviewType.CAPABILITY_IMPLEMENTATION.value: {
            "requires_capability_mapping": True,
            "min_description_length": 100,
        },
        ReviewType.SECURITY_REVIEW.value: {
            "requires_threat_assessment": True,
            "min_description_length": 100,
            "priority_minimum": "high",
        },
    }

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def validate_intake_request(
        self,
        title: str,
        description: str,
        review_type: str,
        submitter_id: int,
        card_id: int = None,
        board_id: int = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Validate a review intake request before creating ARB review.

        Args:
            title: Review title
            description: Review description
            review_type: Type of review
            submitter_id: User submitting the review
            card_id: Associated kanban card ID
            board_id: Associated kanban board ID

        Returns:
            Validation result with errors and warnings
        """
        errors = []
        warnings = []

        # 1. Rate limiting check
        rate_check = self._check_rate_limits(submitter_id, board_id)
        if not rate_check["allowed"]:
            errors.append({
                "code": "RATE_LIMIT_EXCEEDED",
                "message": rate_check["message"],
                "limit": rate_check["limit"],
                "current": rate_check["current"],
                "retry_after": rate_check["retry_after"],
            })

        # 2. Review type validation
        if review_type not in [rt.value for rt in ReviewType]:
            errors.append({
                "code": "INVALID_REVIEW_TYPE",
                "message": f"Unknown review type: {review_type}",
                "valid_types": [rt.value for rt in ReviewType],
            })
        else:
            # Check review type requirements
            requirements = self.REVIEW_TYPE_REQUIREMENTS.get(review_type, {})

            # Check description length
            min_length = requirements.get("min_description_length", 50)
            if len(description) < min_length:
                errors.append({
                    "code": "INSUFFICIENT_DESCRIPTION",
                    "message": f"Description must be at least {min_length} characters for {review_type}",
                    "current_length": len(description),
                    "required_length": min_length,
                })

            # Check required fields
            if requirements.get("togaf_phase_required") and not kwargs.get("togaf_phase"):
                warnings.append({
                    "code": "TOGAF_PHASE_RECOMMENDED",
                    "message": "TOGAF phase specification recommended for this review type",
                })

        # 3. Duplicate check
        duplicate = self._check_duplicate_review(title, description, board_id)
        if duplicate:
            warnings.append({
                "code": "POTENTIAL_DUPLICATE",
                "message": f"Similar review already exists: {duplicate.review_number}",
                "existing_review_id": duplicate.id,
                "existing_review_number": duplicate.review_number,
            })

        # 4. Scope assessment
        scope_assessment = self._assess_review_scope(title, description, review_type)
        if scope_assessment["complexity"] == "high":
            warnings.append({
                "code": "HIGH_COMPLEXITY",
                "message": "High complexity review detected - consider breaking into smaller reviews",
                "complexity_factors": scope_assessment["factors"],
            })

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "rate_limit_status": rate_check if "rate_check" in locals() else None,
            "scope_assessment": scope_assessment if "scope_assessment" in locals() else None,
        }

    def _check_rate_limits(self, submitter_id: int, board_id: int = None) -> Dict[str, Any]:
        """Check if user/board has exceeded review rate limits."""

        # Check daily limit per user
        day_ago = datetime.utcnow() - timedelta(days=1)
        daily_count = (
            ARBReviewItem.query.filter(
                ARBReviewItem.submitter_id == submitter_id,
                ARBReviewItem.created_at >= day_ago,
            ).count()
        )

        if daily_count >= self.MAX_REVIEWS_PER_DAY:
            return {
                "allowed": False,
                "message": f"Daily review limit reached ({self.MAX_REVIEWS_PER_DAY} reviews per day)",
                "limit": self.MAX_REVIEWS_PER_DAY,
                "current": daily_count,
                "retry_after": "24 hours",
            }

        # Check weekly limit per board
        if board_id:
            week_ago = datetime.utcnow() - timedelta(days=7)
            weekly_count = (
                ARBReviewItem.query.filter(
                    ARBReviewItem.submitter_id == submitter_id,
                    ARBReviewItem.board_id == board_id,
                    ARBReviewItem.created_at >= week_ago,
                ).count()
            )

            if weekly_count >= self.MAX_REVIEWS_PER_WEEK:
                return {
                    "allowed": False,
                    "message": f"Weekly review limit reached for this board ({self.MAX_REVIEWS_PER_WEEK} reviews per week)",
                    "limit": self.MAX_REVIEWS_PER_WEEK,
                    "current": weekly_count,
                    "retry_after": "7 days",
                }

        return {
            "allowed": True,
            "daily_count": daily_count,
            "daily_remaining": self.MAX_REVIEWS_PER_DAY - daily_count,
        }

    def _check_duplicate_review(self, title: str, description: str, board_id: int = None) -> Optional[ARBReviewItem]:
        """Check for potential duplicate reviews."""

        # Simple duplicate detection based on title similarity
        # In production, this could use text similarity algorithms

        recent_reviews = (
            ARBReviewItem.query.filter(
                ARBReviewItem.created_at >= datetime.utcnow() - timedelta(days=30)
            )
            .filter(ARBReviewItem.title.ilike(f"%{title[:50]}%"))
        )

        if board_id:
            recent_reviews = recent_reviews.filter_by(board_id=board_id)

        return recent_reviews.first()

    def _assess_review_scope(self, title: str, description: str, review_type: str) -> Dict[str, Any]:
        """Assess the scope and complexity of a review."""

        factors = []
        complexity_score = 0

        # Description length indicates complexity
        desc_length = len(description)
        if desc_length > 2000:
            complexity_score += 3
            factors.append("Very detailed description suggests high complexity")
        elif desc_length > 1000:
            complexity_score += 2
            factors.append("Detailed description suggests moderate-high complexity")

        # Keywords indicating complexity
        complex_keywords = ["enterprise", "critical", "mission-critical", "integration", "migration", "security"]
        text_lower = f"{title} {description}".lower()

        for keyword in complex_keywords:
            if keyword in text_lower:
                complexity_score += 1
                factors.append(f"Contains complexity keyword: {keyword}")

        # Review type complexity
        high_complexity_types = [
            ReviewType.SOLUTION_DESIGN.value,
            ReviewType.ARCHITECTURE_CHANGE.value,
            ReviewType.INTEGRATION_PATTERN.value,
        ]

        if review_type in high_complexity_types:
            complexity_score += 1
            factors.append(f"{review_type} typically involves high complexity")

        # Determine complexity level
        if complexity_score >= 5:
            complexity = "high"
        elif complexity_score >= 3:
            complexity = "medium-high"
        elif complexity_score >= 1:
            complexity = "medium"
        else:
            complexity = "low"

        return {
            "complexity": complexity,
            "score": complexity_score,
            "factors": factors,
        }

    def route_to_reviewer(self, review_type: str, togaf_phase: str = None, archimate_layer: str = None) -> Dict[str, Any]:
        """
        Determine appropriate reviewer based on review characteristics.

        Returns:
            Routing recommendation with reviewer role and rationale
        """

        # Routing rules based on review type
        routing_map = {
            ReviewType.SOLUTION_DESIGN.value: {
                "primary_role": "solution_architect",
                "secondary_role": "enterprise_architect",
                "rationale": "Solution design requires solution architect expertise",
            },
            ReviewType.ARCHITECTURE_CHANGE.value: {
                "primary_role": "enterprise_architect",
                "secondary_role": "domain_architect",
                "rationale": "Architecture changes require EA oversight",
            },
            ReviewType.TECHNOLOGY_SELECTION.value: {
                "primary_role": "technology_architect",
                "secondary_role": "enterprise_architect",
                "rationale": "Technology selection requires technical expertise",
            },
            ReviewType.CAPABILITY_IMPLEMENTATION.value: {
                "primary_role": "business_architect",
                "secondary_role": "solution_architect",
                "rationale": "Capability implementation spans business and solution architecture",
            },
            ReviewType.SECURITY_REVIEW.value: {
                "primary_role": "security_architect",
                "secondary_role": "enterprise_architect",
                "rationale": "Security reviews require security architect expertise",
            },
            ReviewType.INTEGRATION_PATTERN.value: {
                "primary_role": "integration_architect",
                "secondary_role": "solution_architect",
                "rationale": "Integration patterns require integration expertise",
            },
        }

        # Layer-based routing if no specific mapping
        if togaf_phase and not routing_map.get(review_type):
            layer_routing = {
                "business": {"primary_role": "business_architect"},
                "application": {"primary_role": "application_architect"},
                "technology": {"primary_role": "technology_architect"},
                "motivation": {"primary_role": "enterprise_architect"},
            }
            return layer_routing.get(archimate_layer, {
                "primary_role": "enterprise_architect",
                "rationale": "Default routing to EA",
            })

        return routing_map.get(review_type, {
            "primary_role": "enterprise_architect",
            "secondary_role": None,
            "rationale": "Default routing to EA",
        })

    def create_review_from_card(
        self,
        card: KanbanCard,
        arb_session_id: int,
        submitter_id: int,
        auto_submit: bool = False,
    ) -> ARBReviewItem:
        """
        Create an ARB review from a kanban card with intake validation.

        Args:
            card: The kanban card
            arb_session_id: ARB session ID
            submitter_id: User submitting the review
            auto_submit: Whether to auto-submit (requires pre-approval)

        Returns:
            Created ARBReviewItem
        """

        # Validate intake
        validation = self.validate_intake_request(
            title=card.title,
            description=card.description or "No description provided",
            review_type=self._determine_review_type(card),
            submitter_id=submitter_id,
            card_id=card.id,
            board_id=card.board_id,
            togaf_phase=card.adm_phase.code if card.adm_phase else None,
        )

        if not validation["valid"]:
            raise ARBIntakeError(
                f"Intake validation failed: {validation['errors'][0]['message']}",
                code="INTAKE_VALIDATION_FAILED",
                details={"errors": validation["errors"], "warnings": validation["warnings"]},
            )

        # Get routing recommendation
        routing = self.route_to_reviewer(
            review_type=self._determine_review_type(card),
            togaf_phase=card.adm_phase.code if card.adm_phase else None,
        )

        # Create review item
        from app.services.arb_governance_service import arb_governance_service

        review_item = arb_governance_service.submit_for_review(
            title=card.title,
            description=card.description or "No description provided",
            review_type=self._determine_review_type(card),
            submitter_id=submitter_id,
            togaf_phase=card.adm_phase.code if card.adm_phase else None,
            priority=card.priority,
        )

        # Assign to ARB session
        review_item.arb_session_id = arb_session_id

        # Link to card
        card.arb_review_id = review_item.id

        db.session.commit()

        # Log audit event
        adm_audit_service.log_event(
            action=ADMAuditAction.ARB_REVIEW_CREATED,
            entity_type="card",
            entity_id=card.id,
            actor_id=submitter_id,
            entity_reference=card.title,
            board_id=card.board_id,
            card_id=card.id,
            new_values={
                "review_id": review_item.id,
                "review_number": review_item.review_number,
                "routing": routing,
            },
            organization_id=card.organization_id,
        )

        self.logger.info(f"Created ARB review {review_item.review_number} from card {card.id}")

        return review_item

    def _determine_review_type(self, card: KanbanCard) -> str:
        """Determine appropriate review type based on card characteristics."""

        # Map card type to review type
        type_mapping = {
            "requirement": ReviewType.SOLUTION_DESIGN.value,
            "design": ReviewType.SOLUTION_DESIGN.value,
            "implementation": ReviewType.CAPABILITY_IMPLEMENTATION.value,
            "change": ReviewType.ARCHITECTURE_CHANGE.value,
            "security": ReviewType.SECURITY_REVIEW.value,
            "integration": ReviewType.INTEGRATION_PATTERN.value,
        }

        return type_mapping.get(card.card_type, ReviewType.SOLUTION_DESIGN.value)

    def batch_cards_for_review(
        self,
        card_ids: List[int],
        arb_session_id: int,
        submitter_id: int,
    ) -> Dict[str, Any]:
        """
        Batch multiple cards into a single ARB review session.

        Args:
            card_ids: List of card IDs to batch
            arb_session_id: Target ARB session
            submitter_id: User creating the batch

        Returns:
            Batch creation result with created reviews
        """

        if len(card_ids) > self.MAX_BATCH_SIZE:
            raise ARBIntakeError(
                f"Batch size exceeds maximum of {self.MAX_BATCH_SIZE}",
                code="BATCH_SIZE_EXCEEDED",
            )

        results = {
            "created": [],
            "failed": [],
            "warnings": [],
        }

        # Validate all cards belong to same board
        cards = [db.session.get(KanbanCard, cid) for cid in card_ids]
        cards = [c for c in cards if c]

        if not cards:
            raise ARBIntakeError("No valid cards found", code="NO_VALID_CARDS")

        board_ids = set(c.board_id for c in cards)
        if len(board_ids) > 1:
            results["warnings"].append("Batch contains cards from multiple boards")

        # Create batch review
        for card in cards:
            try:
                review = self.create_review_from_card(
                    card=card,
                    arb_session_id=arb_session_id,
                    submitter_id=submitter_id,
                )
                results["created"].append({
                    "card_id": card.id,
                    "review_id": review.id,
                    "review_number": review.review_number,
                })
            except ARBIntakeError as e:
                results["failed"].append({
                    "card_id": card.id,
                    "error": e.message,
                    "code": e.code,
                })

        return results


# Singleton instance
arb_intake_service = ARBIntakeService()
