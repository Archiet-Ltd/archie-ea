"""
Architecture Review Board (ARB) Governance Service

Implements comprehensive ARB governance aligned with TOGAF ADM and ArchiMate 3.2.
Integrates with existing platform capabilities:
- Solution Design workflow
- Capability-based planning
- Architecture Decision Records (ADRs)
- Gap Analysis
- EA Workflow Engine

TOGAF ADM Integration:
- Phase A: Architecture Vision reviews
- Phase B-D: Domain architecture reviews
- Phase E-F: Opportunity and migration reviews
- Phase G: Implementation governance
- Phase H: Change management reviews
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import func, or_

from app import db
from app.models.architecture_review_board import (
    DEFAULT_GOVERNANCE_STANDARDS,
    ARBBoardMember,
    ARBCapabilityImpact,
    ARBGovernanceStandard,
    ARBReviewItem,
    ArchitectureReviewBoard,
)

logger = logging.getLogger(__name__)


class ARBDecisionError(ValueError):
    """Base class for a decision that record_decision refuses to persist.

    Mirrors the pattern f147872 established for the AI approval queue
    (ARCH-022): refuse rather than write a decision that violates a
    governance invariant, and audit-log the refusal itself.
    """


class MissingApproverError(ARBDecisionError):
    """No resolvable approver identity was supplied (M-06)."""


class SelfApprovalError(ARBDecisionError):
    """The submitter attempted to decide their own review (M-05)."""


class InvalidDecisionError(ARBDecisionError):
    """The decision value is not one the ARB can record."""


class InvalidStateTransitionError(ARBDecisionError):
    """The item is not in a state where a decision can be recorded."""


# The only outcomes an ARB can record. Anything else is refused rather than
# stored: an unrecognised value used to be written straight through, leaving the
# row with a decision its status did not reflect, and the UI rendering
# "Outcome: Banana".
VALID_DECISIONS = frozenset({
    "approved",
    "approved_with_conditions",
    "rejected",
    "deferred",
})

# States from which a decision may be recorded.
#
# `deferred` is deliberately included: deferral means "decide later", so a
# deferred item MUST be able to come back for a decision. The terminal outcomes
# are not here, which is what makes a decision final.
#
# `draft` is deliberately excluded: an item that was never submitted has not
# been through review, and approving one bypasses the entire governance
# workflow. That was possible until 31 Aug 2026.
DECIDABLE_STATUSES = frozenset({"submitted", "under_review", "deferred"})

# Once an item reaches one of these, the decision stands. Re-deciding is not a
# correction mechanism -- it silently rewrites the record of truth, and the
# audit log then holds two contradictory entries with no indication which is
# authoritative. Reopening is a separate, deliberate action that must leave its
# own trail.
TERMINAL_STATUSES = frozenset({
    "approved",
    "approved_with_conditions",
    "rejected",
    "completed",
})


class ARBGovernanceService:
    """
    Service for managing Architecture Review Board governance processes.

    Provides comprehensive governance capabilities:
    - ARB session management
    - Review item lifecycle management
    - Governance scoring and assessment
    - Integration with capabilities and solutions
    - TOGAF ADM alignment
    - ArchiMate viewpoint validation
    """

    # Governance scoring weights
    SCORING_WEIGHTS = {"compliance": 0.35, "risk": 0.30, "quality": 0.35}

    # TOGAF ADM phase to ArchiMate layer mapping
    TOGAF_ARCHIMATE_MAPPING = {
        "phase_a_vision": ["motivation", "strategy"],
        "phase_b_business": ["business"],
        "phase_c_information_systems": ["application"],
        "phase_d_technology": ["technology"],
        "phase_e_opportunities": ["implementation"],
        "phase_f_migration": ["implementation"],
        "phase_g_implementation": ["implementation", "technology"],
        "phase_h_change_management": ["all"],
    }

    # =========================================================================
    # ARB SESSION MANAGEMENT
    # =========================================================================

    def create_arb_session(
        self,
        name: str,
        scheduled_date: datetime,
        chair_id: int,
        description: str = None,
        duration_minutes: int = 120,
        location: str = None,
        meeting_link: str = None,
        secretary_id: int = None,
    ) -> ArchitectureReviewBoard:
        """
        Create a new ARB session.

        Args:
            name: Session name/title
            scheduled_date: When the session is scheduled
            chair_id: User ID of the ARB chair
            description: Optional description
            duration_minutes: Expected duration
            location: Physical location
            meeting_link: Video conference URL
            secretary_id: User ID of secretary

        Returns:
            Created ArchitectureReviewBoard instance
        """
        board_number = ArchitectureReviewBoard.generate_board_number()

        arb = ArchitectureReviewBoard(
            board_number=board_number,
            name=name,
            description=description,
            scheduled_date=scheduled_date,
            duration_minutes=duration_minutes,
            location=location,
            meeting_link=meeting_link,
            chair_id=chair_id,
            secretary_id=secretary_id,
            status="scheduled",
        )

        db.session.add(arb)
        db.session.commit()

        logger.info(f"Created ARB session {board_number}: {name}")
        return arb

    def add_board_member(
        self, arb_session_id: int, user_id: int, role: str, voting_member: bool = True
    ) -> ARBBoardMember:
        """Add a member to an ARB session."""
        member = ARBBoardMember(
            arb_session_id=arb_session_id,
            user_id=user_id,
            role=role,
            voting_member=voting_member,
            attendance_status="pending",
        )

        db.session.add(member)
        db.session.commit()
        return member

    def get_upcoming_sessions(self, days: int = 30) -> List[ArchitectureReviewBoard]:
        """Get upcoming ARB sessions."""
        cutoff = datetime.utcnow() + timedelta(days=days)
        return (
            ArchitectureReviewBoard.query.filter(
                ArchitectureReviewBoard.scheduled_date >= datetime.utcnow(),
                ArchitectureReviewBoard.scheduled_date <= cutoff,
                ArchitectureReviewBoard.status == "scheduled",
            )
            .order_by(ArchitectureReviewBoard.scheduled_date)
            .all()
        )

    def complete_session(self, arb_session_id: int, minutes: str = None) -> ArchitectureReviewBoard:
        """Complete an ARB session and finalize metrics."""
        arb = db.session.get(ArchitectureReviewBoard, arb_session_id)
        if not arb:
            raise ValueError(f"ARB session {arb_session_id} not found")

        # Calculate metrics
        review_items = arb.review_items
        arb.items_reviewed = len(review_items)
        arb.items_approved = len(
            [i for i in review_items if i.decision in ["approved", "approved_with_conditions"]]
        )
        arb.items_rejected = len([i for i in review_items if i.decision == "rejected"])
        arb.items_deferred = len([i for i in review_items if i.decision == "deferred"])

        # Generate decisions summary
        arb.decisions_summary = {
            "total_items": arb.items_reviewed,
            "approved": arb.items_approved,
            "rejected": arb.items_rejected,
            "deferred": arb.items_deferred,
            "decisions": [
                {
                    "review_number": item.review_number,
                    "title": item.title,
                    "decision": item.decision,
                    "conditions": item.conditions,
                }
                for item in review_items
            ],
        }

        if minutes:
            arb.minutes = minutes

        arb.status = "completed"
        arb.completed_at = datetime.utcnow()

        db.session.commit()
        return arb

    # =========================================================================
    # REVIEW ITEM MANAGEMENT
    # =========================================================================

    def submit_for_review(
        self,
        title: str,
        description: str,
        review_type: str,
        submitter_id: int,
        togaf_phase: str = None,
        archimate_layer: str = None,
        solution_id: int = None,
        adr_id: int = None,
        architecture_model_id: int = None,
        priority: str = "medium",
        business_impact: str = "medium",
        estimated_effort: str = "medium",
        capability_ids: List[int] = None,
        decision_sought: str = None,
        alternatives_considered: str = None,
        application_ids: List[int] = None,
        capability_impacts: List[Dict[str, Any]] = None,
    ) -> ARBReviewItem:
        """
        Submit an item for ARB review.

        Automatically integrates with:
        - Solutions (if solution_id provided)
        - ADRs (if adr_id provided)
        - Capabilities (if capability_ids provided)

        Args:
            title: Review item title
            description: Detailed description
            review_type: Type from ReviewType enum
            submitter_id: User ID of submitter
            togaf_phase: TOGAF ADM phase
            archimate_layer: Primary ArchiMate layer
            solution_id: Optional linked solution
            adr_id: Optional linked ADR
            architecture_model_id: Optional linked architecture model
            priority: Priority level
            business_impact: Business impact level
            estimated_effort: Estimated effort
            capability_ids: List of impacted capability IDs

        Returns:
            Created ARBReviewItem
        """
        if solution_id is not None:
            raise ValueError(
                "Solution reviews require the canonical evidence-gated submission service"
            )
        if adr_id is not None or architecture_model_id is not None:
            raise ValueError(
                "ADR and model reviews require the canonical evidence-gated typed ARB "
                "submission service"
            )
        review_number = ARBReviewItem.generate_review_number()

        # Auto-determine ArchiMate layer from TOGAF phase if not provided
        if togaf_phase and not archimate_layer:
            layers = self.TOGAF_ARCHIMATE_MAPPING.get(togaf_phase, ["application"])
            archimate_layer = layers[0] if layers else "application"

        item = ARBReviewItem(
            review_number=review_number,
            title=title,
            description=description,
            review_type=review_type,
            togaf_phase=togaf_phase,
            archimate_layer=archimate_layer,
            priority=priority,
            business_impact=business_impact,
            estimated_effort=estimated_effort,
            solution_id=solution_id,
            adr_id=adr_id,
            architecture_model_id=architecture_model_id,
            submitter_id=submitter_id,
            status="draft",
        )

        # Store submission context (decision sought, alternatives considered,
        # affected applications, and the structured per-capability impacts) on the
        # capability_impacts JSON column, which the review detail view reads back as
        # a mapping. The route already validates decision_sought as required.
        submission_context = {}
        if decision_sought:
            submission_context["decision_sought"] = decision_sought
        if alternatives_considered:
            submission_context["alternatives_considered"] = alternatives_considered
        if application_ids:
            submission_context["application_ids"] = [int(a) for a in application_ids]
        if capability_impacts:
            submission_context["impacts"] = capability_impacts
        if submission_context:
            item.capability_impacts = submission_context

        db.session.add(item)
        db.session.flush()  # Get ID for capability links

        # Link capabilities — prefer the structured impacts (with per-capability
        # impact_type/impact_level), fall back to the legacy flat capability_ids.
        capability_links = []
        if capability_impacts:
            for imp in capability_impacts:
                cap_id = imp.get("capability_id")
                if cap_id:
                    capability_links.append(
                        (int(cap_id), imp.get("impact_type") or "modifies", imp.get("impact_level") or "medium")
                    )
        elif capability_ids:
            capability_links = [(cap_id, "modifies", "medium") for cap_id in capability_ids]

        for cap_id, impact_type, impact_level in capability_links:
            db.session.add(
                ARBCapabilityImpact(
                    review_item_id=item.id,
                    capability_id=cap_id,
                    impact_type=impact_type,
                    impact_level=impact_level,
                )
            )

        # Initialize governance checklist based on review type
        item.governance_checklist = self._get_governance_checklist(review_type, togaf_phase)

        db.session.commit()

        logger.info(f"Submitted review item {review_number}: {title}")
        return item

    def submit_item(self, review_item_id: int) -> ARBReviewItem:
        """Submit a draft review item for ARB consideration."""
        item = db.session.get(ARBReviewItem, review_item_id)
        if not item:
            raise ValueError(f"Review item {review_item_id} not found")
        if item.solution_id is not None:
            raise ValueError(
                "Solution reviews require the canonical evidence-gated submission service"
            )
        if item.adr_id is not None or item.architecture_model_id is not None:
            raise ValueError(
                "ADR and model reviews require the canonical evidence-gated typed ARB "
                "submission service"
            )

        if item.status != "draft":
            raise ValueError("Item must be in draft status to submit")

        item.status = "submitted"
        item.submitted_at = datetime.utcnow()

        db.session.commit()
        return item

    def assign_to_session(self, review_item_id: int, arb_session_id: int) -> ARBReviewItem:
        """Assign a review item to an ARB session."""
        item = db.session.get(ARBReviewItem, review_item_id)
        if not item:
            raise ValueError(f"Review item {review_item_id} not found")

        item.arb_session_id = arb_session_id
        item.status = "under_review"
        item.review_started_at = datetime.utcnow()

        db.session.commit()
        return item

    def record_decision(
        self,
        review_item_id: int,
        decision: str,
        rationale: str,
        decided_by_id: int,
        conditions: List[Dict] = None,
        review_date=None,
    ) -> ARBReviewItem:
        """
        Record the ARB decision for a review item.

        Args:
            review_item_id: ID of the review item
            decision: Decision outcome (approved, approved_with_conditions, rejected, deferred)
            rationale: Explanation for the decision
            decided_by_id: User ID who recorded the decision
            conditions: Optional conditions for approval
            review_date: Optional date the board looks at the decision again
                (stored on the item's follow_up_date; None stays None)

        Returns:
            Updated ARBReviewItem
        """
        item = db.session.get(ARBReviewItem, review_item_id)
        if not item:
            raise ValueError(f"Review item {review_item_id} not found")

        # The decision must be one the ARB can actually record. Until 31 Aug
        # 2026 any string was written straight through, so `decision=banana`
        # persisted and the review page rendered "Outcome: Banana" over a status
        # that had not moved.
        if decision not in VALID_DECISIONS:
            self._audit_decision_refusal(
                item,
                event="decision_refused",
                reason=f"unrecognised decision {decision!r}",
                actor_id=decided_by_id,
            )
            raise InvalidDecisionError(
                "%r is not a decision the ARB can record. Valid outcomes: %s"
                % (decision, ", ".join(sorted(VALID_DECISIONS)))
            )

        # The item must be in a state where a decision is meaningful. Two
        # distinct failures, reported distinctly because they mean different
        # things to whoever hit them.
        if item.status in TERMINAL_STATUSES:
            self._audit_decision_refusal(
                item,
                event="decision_refused",
                reason=f"already decided ({item.status})",
                actor_id=decided_by_id,
            )
            raise InvalidStateTransitionError(
                "This review is already %s. A recorded decision is final; "
                "reopen the review if it genuinely needs to change."
                % item.status
            )

        if item.status not in DECIDABLE_STATUSES:
            self._audit_decision_refusal(
                item,
                event="decision_refused",
                reason=f"not submitted for review (status {item.status})",
                actor_id=decided_by_id,
            )
            raise InvalidStateTransitionError(
                "This review is %s and has not been submitted, so there is "
                "nothing to decide. Submit it for review first."
                % (item.status or "in no state")
            )

        # M-06 (S1): the schema has no decided_by field with no enforcement.
        # decided_by_id stays NULLABLE in the database on purpose — deploys do
        # not run Alembic and reconcile-schema is add-column-nullable-only
        # (ADR-0002) — so the invariant is enforced here in application code,
        # exactly as f147872 did for the AI approval queue's approved_by_id.
        if not decided_by_id:
            self._audit_decision_refusal(
                item, event="decision_refused", reason="no resolvable approver id"
            )
            raise MissingApproverError(
                "Cannot record a decision without a resolvable approver identity"
            )

        # M-05 (S1): separation of duties — the submitter cannot also be the
        # decision-maker on their own review. This is a server-side block,
        # not a UI hint: the check runs regardless of what the caller sends.
        if item.submitter_id and item.submitter_id == decided_by_id:
            self._audit_decision_refusal(
                item,
                event="self_approval_refused",
                reason=f"user {decided_by_id} is the submitter",
                actor_id=decided_by_id,
            )
            raise SelfApprovalError(
                "The submitter of a review cannot also record its decision "
                "(separation of duties)"
            )

        # Capgemini dry-run DEF-037: this used to check only
        # `decider.can(Permission.GENERAL)` — the bar every non-read-only
        # account clears, including a solution architect with no board seat —
        # so any authenticated user could record (and re-record — see the
        # already-decided check above, which this bug bypassed the same way)
        # a decision on a review they had no governance role over. Governance
        # decisions require an enterprise_role this codebase already treats as
        # ARB-decision-eligible (see ROLE_SECTION_ACCESS in
        # app/utils/role_access.py: arb_member/cto/enterprise_architect/
        # platform_admin are the roles with a "governance" section).
        ARB_DECISION_ELIGIBLE_ROLES = {
            "arb_member", "cto", "enterprise_architect", "platform_admin",
        }
        try:
            from app.models.user import User

            decider = db.session.get(User, decided_by_id)
            if decider is not None and (decider.enterprise_role or "") not in ARB_DECISION_ELIGIBLE_ROLES:
                self._audit_decision_refusal(
                    item,
                    event="decision_refused",
                    reason=f"role '{decider.enterprise_role}' is not ARB-decision-eligible",
                    actor_id=decided_by_id,
                )
                raise ARBDecisionError(
                    "This account's role does not permit recording ARB decisions"
                )
        except ARBDecisionError:
            raise
        except Exception:  # fabricated-ok: guarded skip on error; emits no fabricated value — never let a lookup failure block a valid decision path silently succeed with bad data
            logger.exception("ARB decision role check failed for user %s", decided_by_id)

        previous_status = item.status
        previous_decision = item.decision

        item.decision = decision
        item.decision_rationale = rationale
        item.decided_by_id = decided_by_id
        item.decision_date = datetime.utcnow()
        item.review_completed_at = datetime.utcnow()

        if conditions:
            item.conditions = conditions
            item.follow_up_required = True
        if review_date is not None:
            item.follow_up_date = review_date
            item.follow_up_required = True

        # Update status based on decision
        if decision == "approved":
            item.status = "approved"
        elif decision == "approved_with_conditions":
            item.status = "approved_with_conditions"
        elif decision == "rejected":
            item.status = "rejected"
        elif decision == "deferred":
            item.status = "deferred"

        db.session.commit()

        # ARCH-092: an immutable audit record of the transition — who, when,
        # previous and new state. Reuses the existing ARBAuditLog/
        # ARBAuditService rather than inventing a second model: it is
        # already tenant-scoped, append-only (no update/delete path exists
        # anywhere in the service), and already had a ready-made
        # log_decision() helper that was simply never called from here.
        try:
            from app.services.arb_audit_service import ARBAuditService

            ARBAuditService().log_action(
                entity_type="review_item",
                entity_id=item.id,
                action="decision",
                user_id=decided_by_id,
                entity_reference=item.review_number,
                old_value={"status": previous_status, "decision": previous_decision},
                new_value={"status": item.status, "decision": item.decision},
                changed_fields=["status", "decision", "decision_rationale", "decided_by_id"],
                description=f"Decision recorded: {decision}",
            )
        except Exception:
            logger.exception(
                "Failed to write ARB decision audit log for review %s", review_item_id
            )

        self._project_decision_to_register(item)

        return item

    def _project_decision_to_register(self, item) -> None:
        """Project a recorded ARB decision into the architecture decision register.

        The register at /architecture/decisions/ and /arb/decisions reads
        ``architecture_decisions``. Recording an ARB decision wrote only to
        ``arb_review_items``, so nothing the board actually decided ever reached
        the register: production held three recorded ARB decisions and both
        register screens read "Total Decisions 0".

        Per ADR-0008 the fix is the missing PRODUCER, not repointing the
        register's readers at ``arb_review_items``. The projected row declares
        its provenance (``source_table`` / ``source_id``), which also makes this
        idempotent -- re-deciding a reopened review updates the same row instead
        of accumulating duplicates.

        Best-effort: the decision itself is already committed and must not be
        rolled back because a derived row failed to write.
        """
        from app.models.architecture_decision import ArchitectureDecision

        _STATUS_FOR_DECISION = {
            "approved": "accepted",
            "approved_with_conditions": "accepted",
            "rejected": "rejected",
            # A deferral is explicitly NOT a decision on the substance: the
            # register must not show it as accepted or rejected.
            "deferred": "proposed",
        }

        try:
            record = ArchitectureDecision.query.filter_by(
                source_table="arb_review_items", source_id=item.id
            ).first()
            if record is None:
                record = ArchitectureDecision(
                    decision_id=ArchitectureDecision.next_decision_id(),
                    source_table="arb_review_items",
                    source_id=item.id,
                    organization_id=item.organization_id,
                    created_by_id=item.decided_by_id,
                )
                db.session.add(record)

            record.title = item.title
            record.status = _STATUS_FOR_DECISION.get(item.decision, "proposed")
            record.context = item.description
            # The outcome, verbatim -- not a restatement. "Approved with
            # conditions" and "approved" are different decisions.
            record.decision = (item.decision or "").replace("_", " ").capitalize()
            record.rationale = item.decision_rationale
            record.consequences = None
            record.decided_by_id = item.decided_by_id
            record.decided_at = item.decision_date
            record.enterprise_level = True
            record.authority_level = "enterprise_arb"
            if item.arb_session_id:
                record.arb_session_id = item.arb_session_id

            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception(
                "Failed to project ARB decision for review %s into the decision register",
                item.id,
            )

    def _audit_decision_refusal(self, item, *, event, reason, actor_id=None):
        """Record that a decision write was refused, and why.

        A refusal is itself a governance-relevant event — mirrors
        AIChatApprovalAuditLog's "execution_refused" event from f147872.
        """
        try:
            from app.services.arb_audit_service import ARBAuditService

            ARBAuditService().log_action(
                entity_type="review_item",
                entity_id=item.id,
                action=event,
                user_id=actor_id,
                entity_reference=item.review_number,
                old_value={"status": item.status, "decision": item.decision},
                new_value=None,
                description=f"Decision write refused: {reason}",
            )
        except Exception:
            logger.exception("Failed to write ARB decision-refusal audit log for review %s", item.id)

    # =========================================================================
    # GOVERNANCE ASSESSMENT
    # =========================================================================

    def assess_compliance(self, review_item_id: int) -> float:
        """
        Assess compliance score for a review item.

        Returns:
            Compliance score (0 - 100)
        """
        item = db.session.get(ARBReviewItem, review_item_id)
        if not item:
            raise ValueError(f"Review item {review_item_id} not found")

        # Get applicable governance standards
        standards = self._get_applicable_standards(item.review_type, item.togaf_phase)

        if not standards:
            return 100.0  # No applicable standards = full compliance

        # Calculate compliance based on checklist completion
        checklist = item.governance_checklist or {}
        completed_items = 0
        total_items = 0

        for standard in standards:
            for checklist_item in standard.checklist_items:
                total_items += 1
                if checklist_item.get("required", False):
                    item_key = f"std_{standard.code}_{checklist_item['item']}"
                    if checklist.get(item_key, False):
                        completed_items += 1

        compliance_score = (completed_items / total_items * 100) if total_items > 0 else 100
        item.compliance_score = compliance_score

        db.session.commit()
        return compliance_score

    def assess_risk(self, review_item_id: int) -> float:
        """
        Assess risk score for a review item.

        Factors:
        - Business impact
        - Priority
        - Number of impacted capabilities
        - Technology complexity

        Returns:
            Risk score (0 - 100, higher = riskier)
        """
        item = db.session.get(ARBReviewItem, review_item_id)
        if not item:
            raise ValueError(f"Review item {review_item_id} not found")

        # Base risk factors
        risk_factors = {
            "business_impact": {"critical": 25, "high": 20, "medium": 10, "low": 5},
            "priority": {"critical": 20, "high": 15, "medium": 10, "low": 5},
            "estimated_effort": {"xl": 15, "large": 10, "medium": 5, "small": 2},
        }

        total_risk = 0

        # Business impact risk
        if item.business_impact:
            total_risk += risk_factors["business_impact"].get(item.business_impact, 10)

        # Priority risk
        if item.priority:
            total_risk += risk_factors["priority"].get(item.priority, 10)

        # Effort risk
        if item.estimated_effort:
            total_risk += risk_factors["estimated_effort"].get(item.estimated_effort, 5)

        # Capability impact risk
        capability_count = len(item.capability_links)
        if capability_count > 5:
            total_risk += 15
        elif capability_count > 2:
            total_risk += 10
        elif capability_count > 0:
            total_risk += 5

        # Cap at 100
        risk_score = min(total_risk, 100)
        item.risk_score = risk_score

        db.session.commit()
        return risk_score

    def assess_quality(self, review_item_id: int) -> float:
        """
        Assess quality score for a review item.

        Factors:
        - Documentation completeness
        - ArchiMate model quality
        - ADR coverage
        - Requirements traceability

        Returns:
            Quality score (0 - 100)
        """
        item = db.session.get(ARBReviewItem, review_item_id)
        if not item:
            raise ValueError(f"Review item {review_item_id} not found")

        quality_score = 0
        max_score = 0

        # Description quality (20 points)
        max_score += 20
        if item.description and len(item.description) > 100:
            quality_score += 20
        elif item.description:
            quality_score += 10

        # Linked entities (30 points)
        max_score += 30
        linked_entities = 0
        if item.solution_id:
            linked_entities += 10
        if item.adr_id:
            linked_entities += 10
        if item.architecture_model_id:
            linked_entities += 10
        quality_score += linked_entities

        # Capability mapping (20 points)
        max_score += 20
        if item.capability_links:
            quality_score += 20

        # ArchiMate layer specification (10 points)
        max_score += 10
        if item.archimate_layer:
            quality_score += 10

        # TOGAF phase specification (10 points)
        max_score += 10
        if item.togaf_phase:
            quality_score += 10

        # Attachments (10 points)
        max_score += 10
        if item.attachments and len(item.attachments) > 0:
            quality_score += 10

        # Normalize to 0 - 100
        final_score = (quality_score / max_score * 100) if max_score > 0 else 0
        item.quality_score = final_score

        db.session.commit()
        return final_score

    def calculate_overall_score(self, review_item_id: int) -> float:
        """
        Calculate weighted overall governance score.

        Returns:
            Overall score (0 - 100)
        """
        item = db.session.get(ARBReviewItem, review_item_id)
        if not item:
            raise ValueError(f"Review item {review_item_id} not found")

        # Ensure individual scores are calculated
        if item.compliance_score is None:
            self.assess_compliance(review_item_id)
        if item.risk_score is None:
            self.assess_risk(review_item_id)
        if item.quality_score is None:
            self.assess_quality(review_item_id)

        # Calculate weighted average
        scores = []
        weights = []

        if item.compliance_score is not None:
            scores.append(item.compliance_score)
            weights.append(self.SCORING_WEIGHTS["compliance"])

        if item.risk_score is not None:
            # Risk is inverted - lower risk = higher score
            scores.append(100 - item.risk_score)
            weights.append(self.SCORING_WEIGHTS["risk"])

        if item.quality_score is not None:
            scores.append(item.quality_score)
            weights.append(self.SCORING_WEIGHTS["quality"])

        if scores and weights:
            overall_score = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
        else:
            overall_score = 0

        item.overall_score = overall_score
        db.session.commit()

        return overall_score

    # =========================================================================
    # INTEGRATION WITH EXISTING WORKFLOWS
    # =========================================================================

    def auto_submit_solution_for_review(self, solution_id: int, submitter_id: int) -> ARBReviewItem:
        """
        Automatically submit a solution for ARB review when it reaches certain criteria.

        Triggered when:
        - Solution status changes to 'ready_for_review'
        - Solution has required documentation
        - Solution has capability mappings

        Args:
            solution_id: Solution ID
            submitter_id: User ID of the submitter

        Returns:
            Created ARBReviewItem
        """
        raise ValueError(
            "Automatic solution review is disabled; use the canonical "
            "evidence-gated submission service"
        )

    def auto_submit_adr_for_review(self, adr_id: int, submitter_id: int) -> ARBReviewItem:
        """
        Automatically submit an ADR for ARB review when significant.

        Triggered when:
        - ADR has high business value
        - ADR affects multiple systems
        - ADR introduces new technology

        Args:
            adr_id: ADR ID
            submitter_id: User ID of the submitter

        Returns:
            Created ARBReviewItem
        """
        raise ValueError(
            "Automatic ADR review is disabled; use the canonical typed ARB "
            "submission service"
        )

    def get_pending_reviews_by_capability(self, capability_id: int) -> List[ARBReviewItem]:
        """
        Get all pending review items that impact a specific capability.

        Useful for capability owners to track governance activities.

        Args:
            capability_id: Capability ID

        Returns:
            List of ARBReviewItem
        """
        return (
            ARBReviewItem.query.join(ARBCapabilityImpact)
            .filter(
                ARBCapabilityImpact.capability_id == capability_id,
                ARBReviewItem.status.in_(["submitted", "under_review", "pending_info"]),
            )
            .order_by(ARBReviewItem.priority.desc(), ARBReviewItem.created_at)
            .all()
        )

    def get_governance_dashboard(self) -> Dict[str, Any]:
        """
        Get comprehensive governance dashboard data.

        Returns:
            Dashboard metrics and data
        """
        # Overall metrics
        total_items = ARBReviewItem.query.count()
        pending_items = ARBReviewItem.query.filter(
            ARBReviewItem.status.in_(["submitted", "under_review", "pending_info"])
        ).count()
        # approved_with_conditions IS an approval -- the list badge already said
        # "Approved" for those rows while this count excluded them, so recording
        # one made the approval rate FALL. Both surfaces now mean the same thing.
        approved_items = ARBReviewItem.query.filter(
            ARBReviewItem.status.in_(["approved", "approved_with_conditions"])
        ).count()
        rejected_items = ARBReviewItem.query.filter(ARBReviewItem.status == "rejected").count()

        # Recent activity
        recent_reviews = (
            ARBReviewItem.query.order_by(ARBReviewItem.created_at.desc()).limit(10).all()
        )

        # Upcoming ARB sessions
        upcoming_sessions = self.get_upcoming_sessions(days=30)

        # Review type distribution
        review_types = (
            db.session.query(ARBReviewItem.review_type, func.count(ARBReviewItem.id))
            .group_by(ARBReviewItem.review_type)
            .all()
        )

        # TOGAF phase distribution
        togaf_phases = (
            db.session.query(ARBReviewItem.togaf_phase, func.count(ARBReviewItem.id))
            .filter(ARBReviewItem.togaf_phase.isnot(None))
            .group_by(ARBReviewItem.togaf_phase)
            .all()
        )

        return {
            "metrics": {
                "total_items": total_items,
                "pending_items": pending_items,
                "approved_items": approved_items,
                "rejected_items": rejected_items,
                "approval_rate": (approved_items / total_items * 100) if total_items > 0 else 0,
            },
            "recent_reviews": [item.to_dict(include_details=False) for item in recent_reviews],
            "upcoming_sessions": [session.to_dict() for session in upcoming_sessions],
            "review_types": [{"type": rt[0], "count": rt[1]} for rt in review_types],
            "togaf_phases": [{"phase": tp[0], "count": tp[1]} for tp in togaf_phases],
        }

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def _get_governance_checklist(self, review_type: str, togaf_phase: str = None) -> Dict:
        """Get governance checklist for a review type."""
        standards = self._get_applicable_standards(review_type, togaf_phase)
        checklist = {}

        for standard in standards:
            for item in standard.checklist_items:
                key = f"std_{standard.code}_{item['item']}"
                checklist[key] = False  # Default to not completed

        return checklist

    def _get_applicable_standards(
        self, review_type: str, togaf_phase: str = None
    ) -> List[ARBGovernanceStandard]:
        """Get governance standards applicable to a review type."""
        query = ARBGovernanceStandard.query.filter(
            ARBGovernanceStandard.status == "active",
        )

        if togaf_phase:
            query = query.filter(
                or_(
                    ARBGovernanceStandard.togaf_phase == togaf_phase,
                    ARBGovernanceStandard.togaf_phase.is_(None),
                )
            )

        # applies_to_review_types is a db.JSON column holding a list. SQLAlchemy's
        # .contains() compiles to a SQL LIKE, which Postgres rejects on the json
        # type ("operator does not exist: json ~~ text"). This is a small config
        # table, so filter membership in Python — DB-agnostic and correct.
        return [
            s
            for s in query.all()
            if isinstance(s.applies_to_review_types, (list, tuple))
            and review_type in s.applies_to_review_types
        ]

    def _determine_priority_from_solution(self, solution) -> str:
        """Determine review priority from solution characteristics."""
        # This would analyze solution attributes to determine priority
        # For now, return medium as default
        return "medium"

    def _determine_priority_from_adr(self, adr) -> str:
        """Determine review priority from ADR characteristics."""
        if adr.business_value == "critical":
            return "critical"
        elif adr.business_value == "high":
            return "high"
        elif adr.estimated_effort in ["large", "xl"]:
            return "high"
        return "medium"

    def _adr_needs_arb_review(self, adr) -> bool:
        """Determine if an ADR needs ARB review."""
        # High business value decisions need review
        if adr.business_value in ["critical", "high"]:
            return True

        # Decisions affecting multiple systems need review
        if adr.affected_systems and len(adr.affected_systems.split(",")) > 1:
            return True

        # Technology stack changes need review
        if adr.technology_stack_id:
            return True

        return False

    def _map_adr_to_togaf_phase(self, adr) -> str:
        """Map ADR to appropriate TOGAF phase."""
        if adr.archimate_layer == "motivation":
            return "phase_a_vision"
        elif adr.archimate_layer == "strategy":
            return "phase_a_vision"
        elif adr.archimate_layer == "business":
            return "phase_b_business"
        elif adr.archimate_layer == "application":
            return "phase_c_information_systems"
        elif adr.archimate_layer == "technology":
            return "phase_d_technology"
        else:
            return "phase_e_opportunities"

    # =========================================================================
    # GOVERNANCE STANDARDS MANAGEMENT
    # =========================================================================

    def initialize_governance_standards(self):
        """Initialize default governance standards."""
        for std_data in DEFAULT_GOVERNANCE_STANDARDS:
            existing = ARBGovernanceStandard.query.filter_by(code=std_data["code"]).first()
            if not existing:
                standard = ARBGovernanceStandard(
                    code=std_data["code"],
                    name=std_data["name"],
                    description=std_data["description"],
                    category=std_data["category"],
                    requirements=std_data["requirements"],
                    checklist_items=std_data["checklist_items"],
                    mandatory=std_data["mandatory"],
                    status="active",
                    effective_date=datetime.utcnow().date(),
                )
                db.session.add(standard)

        db.session.commit()
        logger.info("Initialized ARB governance standards")

    def get_governance_standards(self, category: str = None) -> List[ARBGovernanceStandard]:
        """Get governance standards, optionally filtered by category."""
        query = ARBGovernanceStandard.query.filter_by(status="active")

        if category:
            query = query.filter_by(category=category)

        return query.order_by(ARBGovernanceStandard.code).all()
