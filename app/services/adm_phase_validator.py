"""
ADM Phase Validator Service

Implements TOGAF-compliant phase transition validation rules.
Replaces the arbitrary '2-phase backward limit' with methodology-compliant rules:
- Forward progression requires deliverable completion, Architecture Board approval, compliance verification
- Allow backward movement for Architecture Change Management (Phase H to A)
- Proper dependency validation including external dependencies
"""

from typing import Any, Dict, List

from app import db
from app.models.adm_kanban import ADMPhase, KanbanCard
from app.models.adm_phase_approval import ADMPhaseApproval, ADMComplianceCheckpoint, ApprovalStatus
from app.models.adm_kanban_junctions import CardDependency
from app.services.adm_audit_service import ADMAuditAction, adm_audit_service


class PhaseValidationError(Exception):
    """Exception raised when phase transition validation fails."""

    def __init__(self, message: str, code: str = None, details: Dict = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)


class ADMPhaseValidator:
    """
    TOGAF-compliant phase transition validator.

    Validates phase transitions according to TOGAF ADM methodology:
    - Preliminary -> A: Framework ready, principles established
    - A -> B: Vision approved, requirements documented
    - B -> C: Business architecture approved
    - C -> D: IS architecture (data + apps) approved
    - D -> E: Technology architecture approved
    - E -> F: Opportunities identified, roadmap defined
    - F -> G: Migration plan approved
    - G -> H: Implementation complete
    - H -> A: Change management triggers new cycle
    """

    # TOGAF phase sequence for forward/backward validation
    PHASE_SEQUENCE = ["PRELIM", "A", "B", "C", "D", "E", "F", "G", "H"]

    # REQ is continuous, not part of sequence
    PHASE_ORDER = {code: idx for idx, code in enumerate(PHASE_SEQUENCE)}

    # Allowed backward transitions for Architecture Change Management
    ALLOWED_BACKWARD_TRANSITIONS = {
        "H": ["A", "PRELIM"],  # Phase H can trigger new cycle
        "G": ["F", "E"],       # Implementation issues may require replanning
        "F": ["E"],            # Migration planning may reveal new opportunities
    }

    def __init__(self):
        self.errors = []
        self.warnings = []

    def validate_phase_transition(
        self,
        card: KanbanCard,
        target_phase: ADMPhase,
        user_id: int,
        skip_approval_check: bool = False,
    ) -> Dict[str, Any]:
        """
        Validate if a card can transition to the target phase.

        Args:
            card: The card to transition
            target_phase: Target ADM phase
            user_id: User requesting the transition
            skip_approval_check: Skip approval validation (for system operations)

        Returns:
            Validation result with errors, warnings, and requirements

        Raises:
            PhaseValidationError: If validation fails critically
        """
        self.errors = []
        self.warnings = []

        current_phase = card.adm_phase

        if not current_phase:
            raise PhaseValidationError(
                "Card is not assigned to any phase",
                code="NO_CURRENT_PHASE"
            )

        # Determine transition direction
        current_idx = self.PHASE_ORDER.get(current_phase.code, -1)
        target_idx = self.PHASE_ORDER.get(target_phase.code, -1)

        if current_idx == -1 or target_idx == -1:
            raise PhaseValidationError(
                f"Unknown phase code: {current_phase.code} or {target_phase.code}",
                code="UNKNOWN_PHASE"
            )

        # Same phase - no transition needed
        if current_phase.id == target_phase.id:
            return {"valid": True, "warnings": [], "requires_approval": False}

        # Validate based on direction
        if target_idx > current_idx:
            # Forward progression
            self._validate_forward_transition(card, current_phase, target_phase, user_id, skip_approval_check)
        elif target_idx < current_idx:
            # Backward movement
            self._validate_backward_transition(card, current_phase, target_phase)
        else:
            # Same order but different phases (REQ handling)
            self._validate_req_transition(card, current_phase, target_phase)

        # Check for unresolved dependencies
        self._validate_dependencies(card, target_phase)

        # Check for blocking issues
        blocking_issues = self._check_blocking_issues(card)

        # Determine if approval is required
        requires_approval = self._requires_approval(current_phase, target_phase)

        valid = len(self.errors) == 0

        result = {
            "valid": valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "requires_approval": requires_approval and not skip_approval_check,
            "blocking_issues": blocking_issues,
            "source_phase": {
                "id": current_phase.id,
                "code": current_phase.code,
                "name": current_phase.name,
            },
            "target_phase": {
                "id": target_phase.id,
                "code": target_phase.code,
                "name": target_phase.name,
            },
        }

        if not valid:
            # Log validation failure
            adm_audit_service.log_event(
                action=ADMAuditAction.PHASE_TRANSITION_REJECTED,
                entity_type="card",
                entity_id=card.id,
                actor_id=user_id,
                entity_reference=card.title,
                board_id=card.board_id,
                card_id=card.id,
                source_phase_id=current_phase.id,
                target_phase_id=target_phase.id,
                new_values={"errors": self.errors, "warnings": self.warnings},
                justification="Phase transition validation failed",
                organization_id=card.organization_id,
            )

        return result

    def _validate_forward_transition(
        self,
        card: KanbanCard,
        current_phase: ADMPhase,
        target_phase: ADMPhase,
        user_id: int,
        skip_approval_check: bool,
    ):
        """Validate forward phase progression."""

        # 1. Check sequential progression (no skipping)
        current_idx = self.PHASE_ORDER[current_phase.code]
        target_idx = self.PHASE_ORDER[target_phase.code]

        if target_idx > current_idx + 1:
            # Skip more than one phase
            self.errors.append({
                "code": "SKIP_PHASE",
                "message": "Cannot skip phases. Must progress through intermediate phases.",
                "required_path": self.PHASE_SEQUENCE[current_idx + 1:target_idx],
            })

        # 2. Check Architecture Board approval
        if not skip_approval_check:
            approval = (
                ADMPhaseApproval.query.filter_by(
                    card_id=card.id,
                    target_phase_id=target_phase.id,
                    status=ApprovalStatus.APPROVED.value,
                ).first()
            )

            if not approval:
                self.errors.append({
                    "code": "APPROVAL_REQUIRED",
                    "message": f"Architecture Board approval required for transition to {target_phase.name}",
                    "action": "Create and submit phase transition approval request",
                })
            else:
                # Check if all conditions are fulfilled
                if approval.conditions:
                    unfulfilled = [
                        c for c in approval.conditions
                        if not c.get("fulfilled", False)
                    ]
                    if unfulfilled:
                        self.warnings.append({
                            "code": "UNFULFILLED_CONDITIONS",
                            "message": f"{len(unfulfilled)} approval conditions not yet fulfilled",
                            "conditions": unfulfilled,
                        })

        # 3. Check gate criteria from phase definition
        gate_criteria = target_phase.architecture_board_gate_criteria
        if gate_criteria:
            # Check if deliverables are completed
            required_deliverables = target_phase.expected_outputs or []
            if required_deliverables:
                completed = card.deliverables_completed or []
                missing = [d for d in required_deliverables if d not in completed]
                if missing:
                    self.errors.append({
                        "code": "MISSING_DELIVERABLES",
                        "message": f"Required deliverables not completed for {target_phase.name}",
                        "missing": missing,
                    })

        # 4. Check compliance checkpoints
        checkpoints = ADMComplianceCheckpoint.query.filter_by(
            card_id=card.id,
            target_phase_id=target_phase.id,
        ).all()

        if checkpoints:
            incomplete = [cp for cp in checkpoints if not cp.is_completed]
            if incomplete:
                self.errors.append({
                    "code": "INCOMPLETE_CHECKPOINTS",
                    "message": f"{len(incomplete)} compliance checkpoints incomplete",
                    "checkpoints": [cp.checkpoint_name for cp in incomplete],
                })

        # 5. Check stakeholder concurrence
        # This would be implemented based on stakeholder requirements

    def _validate_backward_transition(
        self,
        card: KanbanCard,
        current_phase: ADMPhase,
        target_phase: ADMPhase,
    ):
        """Validate backward phase movement (for change management)."""

        # Check if backward transition is allowed
        allowed_targets = self.ALLOWED_BACKWARD_TRANSITIONS.get(current_phase.code, [])

        if target_phase.code not in allowed_targets:
            self.errors.append({
                "code": "BACKWARD_NOT_ALLOWED",
                "message": f"Backward transition from {current_phase.name} to {target_phase.name} not allowed",
                "allowed_transitions": allowed_targets,
                "context": "Backward movement only allowed for Architecture Change Management",
            })

        # Backward transitions require strong justification
        self.warnings.append({
            "code": "BACKWARD_TRANSITION",
            "message": f"Moving backward to {target_phase.name} - ensure proper change management process followed",
            "recommendation": "Document business justification and obtain Architecture Board approval",
        })

    def _validate_req_transition(
        self,
        card: KanbanCard,
        current_phase: ADMPhase,
        target_phase: ADMPhase,
    ):
        """Validate transitions involving Requirements Management (REQ)."""

        # REQ can transition to/from any phase for requirements management
        if target_phase.code == "REQ":
            # Moving to REQ - OK for requirements management
            pass
        elif current_phase.code == "REQ":
            # Moving from REQ - ensure requirements are documented
            self.warnings.append({
                "code": "REQ_TRANSITION",
                "message": "Ensure all requirements changes are documented",
            })

    def _validate_dependencies(self, card: KanbanCard, target_phase: ADMPhase):
        """Check for unresolved dependencies."""

        # Get blocking dependencies
        blocking_deps = (
            CardDependency.query.filter_by(
                source_card_id=card.id,
                is_blocking=True,
                status="active",
            ).all()
        )

        for dep in blocking_deps:
            target_card = dep.target_card
            if target_card and target_card.adm_phase_id != target_phase.id:
                # Target card not in same phase
                self.warnings.append({
                    "code": "DEPENDENCY_NOT_ALIGNED",
                    "message": f"Blocking dependency {target_card.title} not in target phase",
                    "dependency_id": dep.id,
                    "target_card_id": target_card.id,
                })

        # Check external dependencies (from JSON field - to be migrated)
        depends_on = card.depends_on or []
        if depends_on:
            for dep_card_id in depends_on:
                dep_card = db.session.get(KanbanCard, dep_card_id)
                if dep_card:
                    dep_phase_idx = self.PHASE_ORDER.get(dep_card.adm_phase.code, -1)
                    target_idx = self.PHASE_ORDER.get(target_phase.code, -1)

                    if dep_phase_idx < target_idx:
                        self.errors.append({
                            "code": "DEPENDENCY_NOT_COMPLETE",
                            "message": f"Dependency {dep_card.title} not yet in target phase",
                            "dependency_card_id": dep_card_id,
                        })

    def _check_blocking_issues(self, card: KanbanCard) -> List[Dict]:
        """Check for issues that block phase transition."""

        blocking_issues = []

        # Check card status
        if card.status in ["blocked", "on_hold"]:
            blocking_issues.append({
                "type": "card_status",
                "message": f"Card status is '{card.status}' - resolve before phase transition",
            })

        # Check for unresolved comments requiring action
        # This would check comment flags

        return blocking_issues

    def _requires_approval(self, current_phase: ADMPhase, target_phase: ADMPhase) -> bool:
        """Determine if Architecture Board approval is required."""

        # All forward transitions require approval
        current_idx = self.PHASE_ORDER.get(current_phase.code, -1)
        target_idx = self.PHASE_ORDER.get(target_phase.code, -1)

        if target_idx > current_idx:
            return True

        # Backward transitions always require approval
        if target_idx < current_idx:
            return True

        return False

    def get_phase_requirements(self, phase: ADMPhase) -> Dict[str, Any]:
        """Get requirements for entering a phase."""

        return {
            "phase_id": phase.id,
            "phase_code": phase.code,
            "phase_name": phase.name,
            "gate_criteria": phase.architecture_board_gate_criteria,
            "required_inputs": phase.required_inputs or [],
            "expected_outputs": phase.expected_outputs or [],
            "governance_checkpoints": phase.governance_checkpoints or [],
            "requires_approval": True,  # All transitions require approval in governed model
        }

    def get_available_transitions(self, card: KanbanCard) -> Dict[str, Any]:
        """Get available phase transitions for a card."""

        current_phase = card.adm_phase
        if not current_phase:
            return {"error": "Card not assigned to any phase"}

        current_idx = self.PHASE_ORDER.get(current_phase.code, -1)
        if current_idx == -1:
            return {"error": f"Unknown current phase: {current_phase.code}"}

        all_phases = ADMPhase.query.filter_by(is_active=True).order_by(ADMPhase.order).all()

        available = []
        for phase in all_phases:
            if phase.id == current_phase.id:
                continue

            target_idx = self.PHASE_ORDER.get(phase.code, -1)

            # Check basic transition rules
            can_transition = True
            reason = None

            if target_idx == -1:
                # REQ phase - always available
                pass
            elif target_idx > current_idx:
                # Forward - allowed if sequential
                if target_idx > current_idx + 1:
                    can_transition = False
                    reason = "Cannot skip phases"
            elif target_idx < current_idx:
                # Backward - check if allowed
                allowed = self.ALLOWED_BACKWARD_TRANSITIONS.get(current_phase.code, [])
                if phase.code not in allowed:
                    can_transition = False
                    reason = f"Backward transition to {phase.code} not allowed"

            available.append({
                "phase_id": phase.id,
                "code": phase.code,
                "name": phase.name,
                "can_transition": can_transition,
                "reason": reason,
                "requires_approval": True,
            })

        return {
            "card_id": card.id,
            "current_phase": {
                "id": current_phase.id,
                "code": current_phase.code,
                "name": current_phase.name,
            },
            "available_transitions": available,
        }


# Singleton instance
adm_phase_validator = ADMPhaseValidator()
