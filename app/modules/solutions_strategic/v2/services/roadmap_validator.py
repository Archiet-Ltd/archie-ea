"""
Roadmap Validation and Business Rules Enforcement
Comprehensive validation for roadmap entities and business logic
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


from app.models.roadmap_models import (
    PlanningDeliverable,
    RoadmapWorkPackage,
)

# Aliases for backwards compatibility
Deliverable = PlanningDeliverable
ImplementationWorkPackage = RoadmapWorkPackage
from app.models.unified_capability import UnifiedCapability

logger = logging.getLogger(__name__)


class ValidationSeverity(Enum):
    """Validation error severity levels"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class ValidationError:
    """Validation error information"""

    field: str
    message: str
    severity: ValidationSeverity
    code: str
    suggested_fix: Optional[str] = None


@dataclass
class ValidationResult:
    """Validation result container"""

    valid: bool
    errors: List[ValidationError]
    warnings: List[ValidationError]
    info: List[ValidationError]

    def has_errors(self) -> bool:
        """Check if there are any errors"""
        return len(self.errors) > 0

    def has_critical_errors(self) -> bool:
        """Check if there are any critical errors"""
        return any(error.severity == ValidationSeverity.CRITICAL for error in self.errors)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "valid": self.valid,
            "errors": [
                {
                    "field": e.field,
                    "message": e.message,
                    "severity": e.severity.value,
                    "code": e.code,
                    "suggested_fix": e.suggested_fix,
                }
                for e in self.errors
            ],
            "warnings": [
                {
                    "field": e.field,
                    "message": e.message,
                    "severity": e.severity.value,
                    "code": e.code,
                    "suggested_fix": e.suggested_fix,
                }
                for e in self.warnings
            ],
            "info": [
                {
                    "field": e.field,
                    "message": e.message,
                    "severity": e.severity.value,
                    "code": e.code,
                    "suggested_fix": e.suggested_fix,
                }
                for e in self.info
            ],
        }


class RoadmapValidator:
    """Comprehensive validation for roadmap entities and business rules"""

    def __init__(self):
        self.business_rules = self._load_business_rules()
        self.validation_constraints = self._load_validation_constraints()

    def validate_work_package_data(self, data: Dict[str, Any]) -> ValidationResult:
        """
        Validate work package creation data

        Args:
            data: Work package data dictionary

        Returns:
            ValidationResult with validation details
        """
        errors = []
        warnings = []
        info = []

        # Required fields validation
        required_fields = ["name", "business_capability"]
        for field in required_fields:
            if field not in data or not data[field]:
                errors.append(
                    ValidationError(
                        field=field,
                        message=f"{field.replace('_', ' ').title()} is required",
                        severity=ValidationSeverity.ERROR,
                        code="REQUIRED_FIELD",
                        suggested_fix=f"Provide a valid {field}",
                    )
                )

        # Name validation
        if "name" in data:
            name_errors = self._validate_work_package_name(data["name"])
            errors.extend(name_errors)

        # Business capability validation
        if "business_capability" in data:
            capability_errors = self._validate_business_capability(data["business_capability"])
            errors.extend(capability_errors)

        # Timeline validation
        if "start_date" in data and "end_date" in data:
            timeline_errors = self._validate_timeline(data["start_date"], data["end_date"])
            errors.extend(timeline_errors)

        # Cost validation
        if "estimated_cost" in data:
            cost_errors = self._validate_cost(data["estimated_cost"])
            warnings.extend(cost_errors)

        # Progress validation
        if "progress_percentage" in data:
            progress_errors = self._validate_progress(
                data["progress_percentage"], data.get("status")
            )
            errors.extend(progress_errors)

        # Priority validation
        if "priority" in data:
            priority_errors = self._validate_priority(data["priority"])
            warnings.extend(priority_errors)

        # Risk level validation
        if "risk_level" in data:
            risk_errors = self._validate_risk_level(data["risk_level"])
            info.extend(risk_errors)

        # RoadmapResource validation
        if "assigned_to" in data:
            resource_errors = self._validate_resource_assignment(data["assigned_to"])
            warnings.extend(resource_errors)

        # Business rules validation
        business_rule_errors = self._validate_work_package_business_rules(data)
        errors.extend(business_rule_errors)

        result = ValidationResult(
            valid=len(errors) == 0, errors=errors, warnings=warnings, info=info
        )

        return result

    def validate_work_package_update(
        self, work_package: ImplementationWorkPackage, data: Dict[str, Any]
    ) -> ValidationResult:
        """
        Validate work package update data

        Args:
            work_package: Existing work package
            data: Update data dictionary

        Returns:
            ValidationResult with validation details
        """
        errors = []
        warnings = []
        info = []

        # Status transition validation
        if "status" in data:
            status_errors = self._validate_status_transition(work_package.status, data["status"])
            errors.extend(status_errors)

        # Timeline change validation
        if "start_date" in data or "end_date" in data:
            new_start = (
                datetime.fromisoformat(data["start_date"])
                if "start_date" in data
                else work_package.start_date
            )
            new_end = (
                datetime.fromisoformat(data["end_date"])
                if "end_date" in data
                else work_package.end_date
            )

            timeline_errors = self._validate_timeline_change(work_package, new_start, new_end)
            errors.extend(timeline_errors)

        # Progress validation
        if "progress_percentage" in data:
            current_status = data.get("status", work_package.status)
            progress_errors = self._validate_progress(data["progress_percentage"], current_status)
            errors.extend(progress_errors)

        # Cost increase validation
        if "estimated_cost" in data and work_package.estimated_cost:
            cost_errors = self._validate_cost_change(
                work_package.estimated_cost, data["estimated_cost"]
            )
            warnings.extend(cost_errors)

        # Dependency validation
        if "dependencies" in data:
            dependency_errors = self._validate_dependency_changes(
                work_package, data["dependencies"]
            )
            errors.extend(dependency_errors)

        result = ValidationResult(
            valid=len(errors) == 0, errors=errors, warnings=warnings, info=info
        )

        return result

    def validate_deliverable_data(self, data: Dict[str, Any]) -> ValidationResult:
        """Validate deliverable creation data"""
        errors = []
        warnings = []
        info = []

        # Required fields
        required_fields = ["name", "work_package_id"]
        for field in required_fields:
            if field not in data or not data[field]:
                errors.append(
                    ValidationError(
                        field=field,
                        message=f"{field.replace('_', ' ').title()} is required",
                        severity=ValidationSeverity.ERROR,
                        code="REQUIRED_FIELD",
                    )
                )

        # Work package existence
        if "work_package_id" in data:
            wp_errors = self._validate_work_package_exists(data["work_package_id"])
            errors.extend(wp_errors)

        # Due date validation
        if "due_date" in data:
            date_errors = self._validate_due_date(data["due_date"], data.get("work_package_id"))
            errors.extend(date_errors)

        # Quality score validation
        if "quality_score" in data:
            score_errors = self._validate_quality_score(data["quality_score"])
            warnings.extend(score_errors)

        result = ValidationResult(
            valid=len(errors) == 0, errors=errors, warnings=warnings, info=info
        )

        return result

    def validate_gap_data(self, data: Dict[str, Any]) -> ValidationResult:
        """Validate gap creation data"""
        errors = []
        warnings = []
        info = []

        # Required fields
        required_fields = ["name", "gap_type"]
        for field in required_fields:
            if field not in data or not data[field]:
                errors.append(
                    ValidationError(
                        field=field,
                        message=f"{field.replace('_', ' ').title()} is required",
                        severity=ValidationSeverity.ERROR,
                        code="REQUIRED_FIELD",
                    )
                )

        # Gap type validation
        if "gap_type" in data:
            type_errors = self._validate_gap_type(data["gap_type"])
            errors.extend(type_errors)

        # Impact assessment validation
        if "impact_assessment" in data and data["impact_assessment"]:
            impact_errors = self._validate_impact_assessment(data["impact_assessment"])
            warnings.extend(impact_errors)

        # Resolution cost validation
        if "estimated_resolution_cost" in data:
            cost_errors = self._validate_resolution_cost(data["estimated_resolution_cost"])
            warnings.extend(cost_errors)

        result = ValidationResult(
            valid=len(errors) == 0, errors=errors, warnings=warnings, info=info
        )

        return result

    def validate_plateau_data(self, data: Dict[str, Any]) -> ValidationResult:
        """Validate plateau creation data"""
        errors = []
        warnings = []
        info = []

        # Required fields
        if "name" not in data or not data["name"]:
            errors.append(
                ValidationError(
                    field="name",
                    message="Name is required",
                    severity=ValidationSeverity.ERROR,
                    code="REQUIRED_FIELD",
                )
            )

        # Timeline validation
        if "start_date" in data and "end_date" in data:
            timeline_errors = self._validate_timeline(data["start_date"], data["end_date"])
            errors.extend(timeline_errors)

        # Stability period validation
        if "stability_period" in data:
            period_errors = self._validate_stability_period(data["stability_period"])
            warnings.extend(period_errors)

        result = ValidationResult(
            valid=len(errors) == 0, errors=errors, warnings=warnings, info=info
        )

        return result

    def validate_roadmap_consistency(self, work_package_ids: List[int]) -> ValidationResult:
        """
        Validate consistency across multiple work packages

        Args:
            work_package_ids: List of work package IDs to validate

        Returns:
            ValidationResult with consistency issues
        """
        errors = []
        warnings = []
        info = []

        work_packages = ImplementationWorkPackage.query.filter(
            ImplementationWorkPackage.id.in_(work_package_ids)
        ).all()

        # Timeline consistency
        timeline_errors = self._validate_timeline_consistency(work_packages)
        errors.extend(timeline_errors)

        # RoadmapResource consistency
        resource_errors = self._validate_resource_consistency(work_packages)
        warnings.extend(resource_errors)

        # Budget consistency
        budget_errors = self._validate_budget_consistency(work_packages)
        warnings.extend(budget_errors)

        # Dependency consistency
        dependency_errors = self._validate_dependency_consistency(work_packages)
        errors.extend(dependency_errors)

        result = ValidationResult(
            valid=len(errors) == 0, errors=errors, warnings=warnings, info=info
        )

        return result

    # Private validation methods
    def _validate_work_package_name(self, name: str) -> List[ValidationError]:
        """Validate work package name"""
        errors = []

        if not name or len(name.strip()) == 0:
            errors.append(
                ValidationError(
                    field="name",
                    message="Name cannot be empty",
                    severity=ValidationSeverity.ERROR,
                    code="EMPTY_NAME",
                )
            )
        elif len(name) > 255:
            errors.append(
                ValidationError(
                    field="name",
                    message="Name cannot exceed 255 characters",
                    severity=ValidationSeverity.ERROR,
                    code="NAME_TOO_LONG",
                    suggested_fix="Shorten the name to 255 characters or less",
                )
            )

        # Check for duplicates
        existing = ImplementationWorkPackage.query.filter_by(name=name).first()
        if existing:
            errors.append(
                ValidationError(
                    field="name",
                    message="Work package with this name already exists",
                    severity=ValidationSeverity.ERROR,
                    code="DUPLICATE_NAME",
                    suggested_fix="Choose a unique name",
                )
            )

        return errors

    def _validate_business_capability(self, capability: str) -> List[ValidationError]:
        """Validate business capability"""
        errors = []

        # Check if capability exists in the system
        existing_capability = UnifiedCapability.query.filter_by(name=capability).first()  # retired-ok: resolves a stored reference or chosen id, which may point at a retired record
        if not existing_capability:
            errors.append(
                ValidationError(
                    field="business_capability",
                    message=f"Business capability '{capability}' does not exist",
                    severity=ValidationSeverity.ERROR,
                    code="UNKNOWN_CAPABILITY",
                    suggested_fix="Select an existing business capability",
                )
            )

        return errors

    def _validate_timeline(self, start_date: str, end_date: str) -> List[ValidationError]:
        """Validate timeline dates"""
        errors = []

        try:
            start = datetime.fromisoformat(start_date)
            end = datetime.fromisoformat(end_date)

            if start >= end:
                errors.append(
                    ValidationError(
                        field="timeline",
                        message="Start date must be before end date",
                        severity=ValidationSeverity.ERROR,
                        code="INVALID_TIMELINE",
                        suggested_fix="Adjust dates to ensure start is before end",
                    )
                )

            if start < datetime.utcnow():
                errors.append(
                    ValidationError(
                        field="start_date",
                        message="Start date cannot be in the past",
                        severity=ValidationSeverity.WARNING,
                        code="PAST_START_DATE",
                        suggested_fix="Set start date to today or a future date",
                    )
                )

            # Check if duration is reasonable (not too long)
            duration = (end - start).days
            if duration > 365 * 3:  # More than 3 years
                errors.append(
                    ValidationError(
                        field="timeline",
                        message="Timeline duration exceeds 3 years",
                        severity=ValidationSeverity.WARNING,
                        code="LONG_TIMELINE",
                        suggested_fix="Consider breaking into smaller work packages",
                    )
                )

        except ValueError as e:
            errors.append(
                ValidationError(
                    field="timeline",
                    message=f"Invalid date format: {str(e)}",
                    severity=ValidationSeverity.ERROR,
                    code="INVALID_DATE_FORMAT",
                    suggested_fix="Use ISO format (YYYY-MM-DD)",
                )
            )

        return errors

    def _validate_cost(self, cost: float) -> List[ValidationError]:
        """Validate estimated cost"""
        warnings = []

        if cost < 0:
            warnings.append(
                ValidationError(
                    field="estimated_cost",
                    message="Estimated cost cannot be negative",
                    severity=ValidationSeverity.WARNING,
                    code="NEGATIVE_COST",
                    suggested_fix="Set cost to zero or a positive value",
                )
            )

        if cost > 10000000:  # $10M
            warnings.append(
                ValidationError(
                    field="estimated_cost",
                    message="Estimated cost exceeds $10M",
                    severity=ValidationSeverity.WARNING,
                    code="HIGH_COST",
                    suggested_fix="Consider breaking into smaller work packages",
                )
            )

        return warnings

    def _validate_progress(self, progress: float, status: str) -> List[ValidationError]:
        """Validate progress percentage"""
        errors = []

        if progress < 0 or progress > 100:
            errors.append(
                ValidationError(
                    field="progress_percentage",
                    message="Progress must be between 0 and 100",
                    severity=ValidationSeverity.ERROR,
                    code="INVALID_PROGRESS",
                    suggested_fix="Set progress to a value between 0 and 100",
                )
            )

        # Check progress consistency with status
        if status == "completed" and progress != 100:
            errors.append(
                ValidationError(
                    field="progress_percentage",
                    message="Completed work packages must have 100% progress",
                    severity=ValidationSeverity.ERROR,
                    code="PROGRESS_STATUS_MISMATCH",
                    suggested_fix="Set progress to 100% or change status",
                )
            )

        if status == "planned" and progress > 0:
            errors.append(
                ValidationError(
                    field="progress_percentage",
                    message="Planned work packages should have 0% progress",
                    severity=ValidationSeverity.WARNING,
                    code="PROGRESS_STATUS_MISMATCH",
                    suggested_fix="Set progress to 0% or change status to in_progress",
                )
            )

        return errors

    def _validate_priority(self, priority: str) -> List[ValidationError]:
        """Validate priority level"""
        warnings = []

        valid_priorities = ["low", "medium", "high", "critical"]
        if priority not in valid_priorities:
            warnings.append(
                ValidationError(
                    field="priority",
                    message=f"Invalid priority '{priority}'. Valid values: {', '.join(valid_priorities)}",
                    severity=ValidationSeverity.WARNING,
                    code="INVALID_PRIORITY",
                    suggested_fix=f"Use one of: {', '.join(valid_priorities)}",
                )
            )

        return warnings

    def _validate_risk_level(self, risk_level: str) -> List[ValidationError]:
        """Validate risk level"""
        info = []

        valid_risk_levels = ["low", "medium", "high", "critical"]
        if risk_level not in valid_risk_levels:
            info.append(
                ValidationError(
                    field="risk_level",
                    message=f"Risk level '{risk_level}' is not standard",
                    severity=ValidationSeverity.INFO,
                    code="NON_STANDARD_RISK",
                    suggested_fix=f"Consider using: {', '.join(valid_risk_levels)}",
                )
            )

        return info

    def _validate_resource_assignment(self, assigned_to: str) -> List[ValidationError]:
        """Validate resource assignment"""
        warnings = []

        if assigned_to:
            # Check if resource exists (simplified check)
            # In production, this would query the resources table
            if len(assigned_to.strip()) < 2:
                warnings.append(
                    ValidationError(
                        field="assigned_to",
                        message="RoadmapResource name seems too short",
                        severity=ValidationSeverity.WARNING,
                        code="SHORT_RESOURCE_NAME",
                        suggested_fix="Provide a complete resource name",
                    )
                )

        return warnings

    def _validate_work_package_business_rules(self, data: Dict[str, Any]) -> List[ValidationError]:
        """Validate business rules for work packages"""
        errors = []

        # Rule: Critical work packages must have risk assessment
        if data.get("priority") == "critical" and not data.get("risk_level"):
            errors.append(
                ValidationError(
                    field="risk_level",
                    message="Critical work packages must have risk level specified",
                    severity=ValidationSeverity.ERROR,
                    code="MISSING_RISK_ASSESSMENT",
                    suggested_fix="Specify risk level for critical work packages",
                )
            )

        # Rule: High cost work packages must have detailed description
        if data.get("estimated_cost", 0) > 500000 and not data.get("description"):
            errors.append(
                ValidationError(
                    field="description",
                    message="High-cost work packages must have detailed description",
                    severity=ValidationSeverity.ERROR,
                    code="MISSING_DESCRIPTION",
                    suggested_fix="Provide detailed description for high-cost work packages",
                )
            )

        # Rule: Work packages with dependencies must have start date
        if data.get("dependencies") and not data.get("start_date"):
            errors.append(
                ValidationError(
                    field="start_date",
                    message="Work packages with dependencies must have start date",
                    severity=ValidationSeverity.ERROR,
                    code="MISSING_START_DATE",
                    suggested_fix="Provide start date for work packages with dependencies",
                )
            )

        return errors

    def _validate_status_transition(
        self, current_status: str, new_status: str
    ) -> List[ValidationError]:
        """Validate status transition"""
        errors = []

        valid_transitions = {
            "planned": ["planned", "in_progress", "cancelled"],
            "in_progress": ["in_progress", "completed", "cancelled"],
            "completed": ["completed"],  # Terminal state
            "cancelled": ["cancelled"],  # Terminal state
        }

        if current_status in valid_transitions:
            if new_status not in valid_transitions[current_status]:
                errors.append(
                    ValidationError(
                        field="status",
                        message=f"Invalid status transition from {current_status} to {new_status}",
                        severity=ValidationSeverity.ERROR,
                        code="INVALID_STATUS_TRANSITION",
                        suggested_fix=f"Valid transitions from {current_status}: {', '.join(valid_transitions[current_status])}",
                    )
                )

        return errors

    def _validate_timeline_change(
        self, work_package: ImplementationWorkPackage, new_start: datetime, new_end: datetime
    ) -> List[ValidationError]:
        """Validate timeline changes"""
        errors = []

        # Check if timeline change affects dependencies
        if work_package.dependencies:
            for dependency in work_package.dependencies:
                if dependency.end_date and new_start <= dependency.end_date:
                    errors.append(
                        ValidationError(
                            field="timeline",
                            message=f"Timeline change conflicts with dependency '{dependency.name}'",
                            severity=ValidationSeverity.ERROR,
                            code="DEPENDENCY_CONFLICT",
                            suggested_fix=f"Adjust start date to be after {dependency.end_date}",
                        )
                    )

        # Check if timeline change affects dependents
        for dependent in work_package.dependents:
            if dependent.start_date and new_end >= dependent.start_date:
                errors.append(
                    ValidationError(
                        field="timeline",
                        message=f"Timeline change conflicts with dependent '{dependent.name}'",
                        severity=ValidationSeverity.WARNING,
                        code="DEPENDENT_CONFLICT",
                        suggested_fix=f"Adjust dependent '{dependent.name}' start date",
                    )
                )

        return errors

    def _validate_cost_change(self, old_cost: float, new_cost: float) -> List[ValidationError]:
        """Validate cost changes"""
        warnings = []

        if new_cost > old_cost * 1.5:  # More than 50% increase
            warnings.append(
                ValidationError(
                    field="estimated_cost",
                    message="Cost increase exceeds 50%",
                    severity=ValidationSeverity.WARNING,
                    code="SIGNIFICANT_COST_INCREASE",
                    suggested_fix="Review cost justification and consider budget impact",
                )
            )

        return warnings

    def _validate_dependency_changes(
        self, work_package: ImplementationWorkPackage, new_dependencies: List[Dict]
    ) -> List[ValidationError]:
        """Validate dependency changes"""
        errors = []

        # Check for circular dependencies
        if new_dependencies:
            for dep in new_dependencies:
                if dep.get("id") == work_package.id:
                    errors.append(
                        ValidationError(
                            field="dependencies",
                            message="Work package cannot depend on itself",
                            severity=ValidationSeverity.ERROR,
                            code="CIRCULAR_DEPENDENCY",
                            suggested_fix="Remove self-dependency",
                        )
                    )

        return errors

    def _validate_work_package_exists(self, work_package_id: int) -> List[ValidationError]:
        """Validate work package existence"""
        errors = []

        wp = ImplementationWorkPackage.query.get(work_package_id)
        if not wp:
            errors.append(
                ValidationError(
                    field="work_package_id",
                    message="Work package does not exist",
                    severity=ValidationSeverity.ERROR,
                    code="WORK_PACKAGE_NOT_FOUND",
                    suggested_fix="Select an existing work package",
                )
            )

        return errors

    def _validate_due_date(
        self, due_date: str, work_package_id: Optional[int]
    ) -> List[ValidationError]:
        """Validate deliverable due date"""
        errors = []

        try:
            due = datetime.fromisoformat(due_date)

            if due < datetime.utcnow():
                errors.append(
                    ValidationError(
                        field="due_date",
                        message="Due date cannot be in the past",
                        severity=ValidationSeverity.ERROR,
                        code="PAST_DUE_DATE",
                        suggested_fix="Set due date to today or a future date",
                    )
                )

            # Check against work package timeline if available
            if work_package_id:
                wp = ImplementationWorkPackage.query.get(work_package_id)
                if wp and wp.end_date and due > wp.end_date:
                    errors.append(
                        ValidationError(
                            field="due_date",
                            message="Deliverable due date is after work package end date",
                            severity=ValidationSeverity.WARNING,
                            code="DUE_AFTER_WP_END",
                            suggested_fix="Adjust due date to be before work package completion",
                        )
                    )

        except ValueError as e:
            errors.append(
                ValidationError(
                    field="due_date",
                    message=f"Invalid date format: {str(e)}",
                    severity=ValidationSeverity.ERROR,
                    code="INVALID_DATE_FORMAT",
                    suggested_fix="Use ISO format (YYYY-MM-DD)",
                )
            )

        return errors

    def _validate_quality_score(self, quality_score: float) -> List[ValidationError]:
        """Validate quality score"""
        warnings = []

        if quality_score < 0 or quality_score > 10:
            warnings.append(
                ValidationError(
                    field="quality_score",
                    message="Quality score must be between 0 and 10",
                    severity=ValidationSeverity.WARNING,
                    code="INVALID_QUALITY_SCORE",
                    suggested_fix="Set quality score to a value between 0 and 10",
                )
            )

        return warnings

    def _validate_gap_type(self, gap_type: str) -> List[ValidationError]:
        """Validate gap type"""
        errors = []

        valid_types = ["technology", "process", "skill", "resource", "data", "organization"]
        if gap_type not in valid_types:
            errors.append(
                ValidationError(
                    field="gap_type",
                    message=f"Invalid gap type '{gap_type}'. Valid types: {', '.join(valid_types)}",
                    severity=ValidationSeverity.ERROR,
                    code="INVALID_GAP_TYPE",
                    suggested_fix=f"Use one of: {', '.join(valid_types)}",
                )
            )

        return errors

    def _validate_impact_assessment(self, impact_assessment: str) -> List[ValidationError]:
        """Validate impact assessment"""
        warnings = []

        if len(impact_assessment) < 50:
            warnings.append(
                ValidationError(
                    field="impact_assessment",
                    message="Impact assessment seems too brief",
                    severity=ValidationSeverity.WARNING,
                    code="BRIEF_IMPACT_ASSESSMENT",
                    suggested_fix="Provide more detailed impact assessment",
                )
            )

        return warnings

    def _validate_resolution_cost(self, cost: float) -> List[ValidationError]:
        """Validate resolution cost"""
        warnings = []

        if cost < 0:
            warnings.append(
                ValidationError(
                    field="estimated_resolution_cost",
                    message="Resolution cost cannot be negative",
                    severity=ValidationSeverity.WARNING,
                    code="NEGATIVE_COST",
                    suggested_fix="Set cost to zero or a positive value",
                )
            )

        return warnings

    def _validate_stability_period(self, stability_period: int) -> List[ValidationError]:
        """Validate stability period"""
        warnings = []

        if stability_period < 0:
            warnings.append(
                ValidationError(
                    field="stability_period",
                    message="Stability period cannot be negative",
                    severity=ValidationSeverity.WARNING,
                    code="NEGATIVE_STABILITY_PERIOD",
                    suggested_fix="Set stability period to zero or a positive value",
                )
            )

        if stability_period > 365 * 2:  # More than 2 years
            warnings.append(
                ValidationError(
                    field="stability_period",
                    message="Stability period exceeds 2 years",
                    severity=ValidationSeverity.WARNING,
                    code="LONG_STABILITY_PERIOD",
                    suggested_fix="Consider shorter stability period for more agile transitions",
                )
            )

        return warnings

    def _validate_timeline_consistency(
        self, work_packages: List[ImplementationWorkPackage]
    ) -> List[ValidationError]:
        """Validate timeline consistency across work packages"""
        errors = []

        # Check for overlapping work packages with same resources
        resource_assignments = {}
        for wp in work_packages:
            if wp.assigned_to:
                if wp.assigned_to not in resource_assignments:
                    resource_assignments[wp.assigned_to] = []
                resource_assignments[wp.assigned_to].append(wp)

        for resource, assigned_wps in resource_assignments.items():
            if len(assigned_wps) > 1:
                for i, wp1 in enumerate(assigned_wps):
                    for j, wp2 in enumerate(assigned_wps[i + 1 :], i + 1):
                        if wp1.start_date and wp1.end_date and wp2.start_date and wp2.end_date:
                            if (wp1.start_date <= wp2.end_date) and (
                                wp2.start_date <= wp1.end_date
                            ):
                                errors.append(
                                    ValidationError(
                                        field="timeline",
                                        message=f"RoadmapResource {resource} assigned to overlapping work packages",
                                        severity=ValidationSeverity.ERROR,
                                        code="RESOURCE_OVERLAP",
                                        suggested_fix="Adjust timelines or reassign resources",
                                    )
                                )

        return errors

    def _validate_resource_consistency(
        self, work_packages: List[ImplementationWorkPackage]
    ) -> List[ValidationError]:
        """Validate resource consistency"""
        warnings = []

        # Check for over-allocated resources
        resource_assignments = {}
        for wp in work_packages:
            if wp.assigned_to:
                if wp.assigned_to not in resource_assignments:
                    resource_assignments[wp.assigned_to] = []
                resource_assignments[wp.assigned_to].append(wp)

        for resource, assigned_wps in resource_assignments.items():
            if len(assigned_wps) > 5:  # More than 5 work packages
                warnings.append(
                    ValidationError(
                        field="resource_assignment",
                        message=f"RoadmapResource {resource} assigned to {len(assigned_wps)} work packages",
                        severity=ValidationSeverity.WARNING,
                        code="RESOURCE_OVERALLOCATION",
                        suggested_fix="Consider resource balancing or adding more resources",
                    )
                )

        return warnings

    def _validate_budget_consistency(
        self, work_packages: List[ImplementationWorkPackage]
    ) -> List[ValidationError]:
        """Validate budget consistency"""
        warnings = []

        total_cost = sum(wp.estimated_cost or 0 for wp in work_packages)

        if total_cost > 1000000:  # $1M
            warnings.append(
                ValidationError(
                    field="budget",
                    message=f"Total estimated cost ${total_cost:,.0f} exceeds $1M",
                    severity=ValidationSeverity.WARNING,
                    code="BUDGET_EXCEEDED",
                    suggested_fix="Review work packages and consider prioritization",
                )
            )

        return warnings

    def _validate_dependency_consistency(
        self, work_packages: List[ImplementationWorkPackage]
    ) -> List[ValidationError]:
        """Validate dependency consistency"""
        errors = []

        # Check for circular dependencies
        wp_dict = {wp.id: wp for wp in work_packages}
        visited = set()
        rec_stack = set()

        def has_cycle(wp_id):
            if wp_id in rec_stack:
                return True
            if wp_id in visited:
                return False

            visited.add(wp_id)
            rec_stack.add(wp_id)

            wp = wp_dict.get(wp_id)
            if wp:
                for dep in wp.dependencies:
                    if has_cycle(dep.id):
                        return True

            rec_stack.remove(wp_id)
            return False

        for wp in work_packages:
            if has_cycle(wp.id):
                errors.append(
                    ValidationError(
                        field="dependencies",
                        message="Circular dependency detected",
                        severity=ValidationSeverity.ERROR,
                        code="CIRCULAR_DEPENDENCY",
                        suggested_fix="Break circular dependency chain",
                    )
                )
                break

        return errors

    def _load_business_rules(self) -> Dict[str, Any]:
        """Load business rules configuration"""
        return {
            "max_work_package_duration_days": 365 * 3,
            "max_single_cost": 10000000,
            "max_total_cost": 1000000,
            "max_resource_assignments": 5,
            "required_fields_for_critical": ["risk_level", "description"],
            "required_fields_for_high_cost": ["description"],
        }

    def _load_validation_constraints(self) -> Dict[str, Any]:
        """Load validation constraints"""
        return {
            "name_max_length": 255,
            "progress_min": 0,
            "progress_max": 100,
            "quality_score_min": 0,
            "quality_score_max": 10,
            "valid_priorities": ["low", "medium", "high", "critical"],
            "valid_risk_levels": ["low", "medium", "high", "critical"],
            "valid_statuses": ["planned", "in_progress", "completed", "cancelled"],
            "valid_gap_types": [
                "technology",
                "process",
                "skill",
                "resource",
                "data",
                "organization",
            ],
        }
