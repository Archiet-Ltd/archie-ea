"""
ArchiMate 3.2 Implementation Element Relationship Validation Service

This service provides comprehensive validation for ArchiMate 3.2 implementation layer elements
and their relationships according to the official specification.

Features:
- WorkPackage relationship validation
- Deliverable dependency validation
- Gap relationship validation
- Plateau transition validation
- ImplementationEvent relationship validation
- Cross-layer relationship validation
- Business rule validation
- Temporal relationship validation

Complies with:
- ArchiMate 3.2 Specification (The Open Group)
- Enterprise Architecture best practices
- TOGAF ADM guidelines
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional


from ..models.implementation_migration import (
    Deliverable,
    ImplementationEvent,
    Gap as ImplementationGap,
    Plateau as ImplementationPlateau,
    WorkPackage as ImplementationWorkPackage,
)
from ..models.models import ArchiMateElement, ArchiMateRelationship

logger = logging.getLogger(__name__)


class ImplementationValidationService:
    """
    Validation service for ArchiMate 3.2 implementation elements and relationships.
    """

    # ArchiMate 3.2 Implementation Layer Element Types
    IMPLEMENTATION_ELEMENTS = {
        "ImplementationWorkPackage": {
            "description": "A unit of work that can be assigned to an actor",
            "valid_relationships": [
                "assignment",
                "composition",
                "aggregation",
                "triggering",
                "flow",
                "access",
                "association",
                "realization",
            ],
            "required_attributes": ["name"],
            "optional_attributes": [
                "description",
                "start_date",
                "end_date",
                "assigned_to",
                "priority",
            ],
        },
        "Deliverable": {
            "description": "A concrete outcome of a work package",
            "valid_relationships": [
                "assignment",
                "composition",
                "aggregation",
                "triggering",
                "flow",
                "access",
                "association",
                "realization",
            ],
            "required_attributes": ["name"],
            "optional_attributes": [
                "description",
                "deliverable_type",
                "format",
                "version",
                "due_date",
            ],
        },
        "ImplementationPlateau": {
            "description": "A relatively stable state of the architecture",
            "valid_relationships": [
                "association",
                "triggering",
                "transition" "association",
                "triggering",
                "flow",
                "realization",
            ],
            "required_attributes": ["name", "start_date", "end_date"],
            "optional_attributes": ["description", "plateau_type", "state_description"],
        },
        "ImplementationGap": {
            "description": "A statement of difference between the baseline and target",
            "valid_relationships": ["association", "influence", "realization"],
            "required_attributes": ["name", "gap_description"],
            "optional_attributes": ["description", "gap_type", "impact_level", "urgency"],
        },
    }

    # ArchiMate 3.2 Relationship Types and Rules
    RELATIONSHIP_RULES = {
        "assignment": {
            "description": "Assignment of work to actors",
            "valid_sources": ["ImplementationWorkPackage", "Deliverable"],
            "valid_targets": ["BusinessActor", "BusinessRole"],
            "directional": True,
            "cross_layer": True,
        },
        "composition": {
            "description": "Strong aggregation with ownership",
            "valid_sources": ["ImplementationWorkPackage", "Deliverable"],
            "valid_targets": ["ImplementationWorkPackage", "Deliverable"],
            "directional": True,
            "cross_layer": False,
            "same_layer_only": True,
        },
        "aggregation": {
            "description": "Aggregation without strong ownership",
            "valid_sources": ["ImplementationWorkPackage", "Deliverable"],
            "valid_targets": ["ImplementationWorkPackage", "Deliverable"],
            "directional": True,
            "cross_layer": False,
            "same_layer_only": True,
        },
        "triggering": {
            "description": "Temporal triggering relationship",
            "valid_sources": ["ImplementationEvent", "ImplementationWorkPackage"],
            "valid_targets": ["ImplementationWorkPackage", "ImplementationEvent", "Deliverable"],
            "directional": True,
            "cross_layer": True,
            "temporal": True,
        },
        "flow": {
            "description": "Flow of objects or information",
            "valid_sources": ["ImplementationWorkPackage", "Deliverable"],
            "valid_targets": ["ImplementationWorkPackage", "Deliverable"],
            "directional": True,
            "cross_layer": True,
        },
        "access": {
            "description": "Access to objects or services",
            "valid_sources": ["ImplementationWorkPackage", "Deliverable"],
            "valid_targets": ["DataObject", "ApplicationService"],
            "directional": True,
            "cross_layer": True,
        },
        "association": {
            "description": "General association",
            "valid_sources": [
                "ImplementationWorkPackage",
                "Deliverable",
                "ImplementationGap",
                "ImplementationPlateau",
                "ImplementationEvent",
            ],
            "valid_targets": [
                "ImplementationWorkPackage",
                "Deliverable",
                "ImplementationGap",
                "ImplementationPlateau",
                "ImplementationEvent",
            ],
            "directional": False,
            "cross_layer": True,
        },
        "realization": {
            "description": "Realization of concepts",
            "valid_sources": ["ImplementationWorkPackage", "Deliverable"],
            "valid_targets": ["BusinessService", "ApplicationService"],
            "directional": True,
            "cross_layer": True,
        },
        "influence": {
            "description": "Influence relationship",
            "valid_sources": ["ImplementationGap"],
            "valid_targets": ["ImplementationWorkPackage", "Deliverable", "BusinessProcess"],
            "directional": False,
            "cross_layer": True,
        },
    }

    def __init__(self):
        self.validation_errors = []
        self.validation_warnings = []

    def validate_work_package(self, work_package: ImplementationWorkPackage) -> Dict[str, Any]:
        """
        Validate a WorkPackage element according to ArchiMate 3.2 rules.

        Args:
            work_package: WorkPackage instance to validate

        Returns:
            Dictionary containing validation results
        """
        errors = []
        warnings = []

        # Validate required attributes
        if not work_package.name or not work_package.name.strip():
            errors.append("WorkPackage must have a name")

        # Validate date consistency
        if work_package.start_date and work_package.end_date:
            if work_package.start_date >= work_package.end_date:
                errors.append("Start date must be before end date")

            # Check for reasonable duration
            duration = (work_package.end_date - work_package.start_date).days
            if duration > 365 * 5:  # More than 5 years
                warnings.append(
                    "WorkPackage duration exceeds 5 years - consider breaking into smaller packages"
                )
            elif duration < 1:  # Less than 1 day
                warnings.append("WorkPackage duration is less than 1 day")

        # Validate progress percentage
        if work_package.progress_percentage < 0 or work_package.progress_percentage > 100:
            errors.append("Progress percentage must be between 0 and 100")

        # Validate status consistency with progress
        if work_package.status == "completed" and work_package.progress_percentage < 100:
            warnings.append("Completed WorkPackage should have 100% progress")
        elif work_package.status == "planned" and work_package.progress_percentage > 0:
            warnings.append("Planned WorkPackage should have 0% progress")

        # Validate cost consistency
        if work_package.actual_cost and work_package.estimated_cost:
            if work_package.actual_cost > work_package.estimated_cost * 2:
                warnings.append("Actual cost significantly exceeds estimated cost")

        # Validate dependencies
        if work_package.dependencies:
            for dep_id in work_package.dependencies:
                if not isinstance(dep_id, int):
                    errors.append(
                        "ImplementationWorkPackage dependencies must be valid ImplementationWorkPackage IDs"
                    )
                else:
                    # Check if dependency exists
                    dep_wp = ImplementationWorkPackage.query.get(dep_id)
                    if not dep_wp:
                        errors.append(
                            f"ImplementationWorkPackage dependency {dep_id} does not exist"
                        )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "element_id": work_package.id,
            "element_type": "ImplementationWorkPackage",
            "element_name": work_package.name,
        }

    def validate_deliverable(self, deliverable: Deliverable) -> Dict[str, Any]:
        """
        Validate a Deliverable element according to ArchiMate 3.2 rules.

        Args:
            deliverable: Deliverable instance to validate

        Returns:
            Dictionary containing validation results
        """
        errors = []
        warnings = []

        # Validate required attributes
        if not deliverable.name or not deliverable.name.strip():
            errors.append("Deliverable must have a name")

        # Validate date consistency
        if deliverable.due_date and deliverable.delivered_date:
            if deliverable.delivered_date > deliverable.due_date:
                warnings.append("Deliverable was delivered after due date")

        # Validate version format
        if deliverable.version:
            import re

            if not re.match(r"^\d+\.\d+(\.\d+)?$", deliverable.version):
                warnings.append(
                    "Deliverable version should follow semantic versioning (e.g., 1.0.0)"
                )

        # Validate status consistency
        if deliverable.status == "completed" and not deliverable.delivered_date:
            warnings.append("Completed deliverable should have a delivered date")
        elif deliverable.status == "approved" and not deliverable.approved_date:
            warnings.append("Approved deliverable should have an approved date")

        # Validate work package association
        if deliverable.work_package_id:
            wp = ImplementationWorkPackage.query.get(deliverable.work_package_id)
            if not wp:
                errors.append("Associated ImplementationWorkPackage does not exist")
            else:
                # Check temporal consistency
                if deliverable.due_date and wp.end_date:
                    if deliverable.due_date > wp.end_date:
                        warnings.append(
                            "Deliverable due date is after ImplementationWorkPackage end date"
                        )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "element_id": deliverable.id,
            "element_type": "Deliverable",
            "element_name": deliverable.name,
        }

    def validate_gap(self, gap: ImplementationGap) -> Dict[str, Any]:
        """
        Validate a Gap element according to ArchiMate 3.2 rules.

        Args:
            gap: Gap instance to validate

        Returns:
            Dictionary containing validation results
        """
        errors = []
        warnings = []

        # Validate required attributes
        if not gap.name or not gap.name.strip():
            errors.append("Gap must have a name")

        if not gap.gap_description or not gap.gap_description.strip():
            errors.append("Gap must have a gap description")

        # Validate gap type
        valid_gap_types = [
            "capability",
            "application",
            "technology",
            "process",
            "data",
            "organization",
            "information",
        ]
        if gap.gap_type and gap.gap_type not in valid_gap_types:
            warnings.append(f"Gap type '{gap.gap_type}' is not a standard ArchiMate gap type")

        # Validate impact and urgency consistency
        if gap.impact_level and gap.urgency:
            critical_combinations = [("low", "critical"), ("medium", "critical"), ("low", "high")]
            if (gap.impact_level, gap.urgency) in critical_combinations:
                warnings.append(
                    f"Gap impact ({gap.impact_level}) and urgency ({gap.urgency}) seem inconsistent"
                )

        # Validate status consistency
        if gap.resolution_status == "resolved" and not gap.resolved_date:
            warnings.append("Resolved gap should have a resolved date")
        elif gap.resolution_status in ["identified", "analyzed"] and gap.resolved_date:
            warnings.append("Gap should not have resolved date unless status is 'resolved'")

        # Validate plateau associations
        if gap.baseline_plateau_id:
            baseline = ImplementationPlateau.query.get(gap.baseline_plateau_id)
            if not baseline:
                errors.append("Baseline plateau does not exist")

        if gap.target_plateau_id:
            target = ImplementationPlateau.query.get(gap.target_plateau_id)
            if not target:
                errors.append("Target plateau does not exist")

        # Check temporal consistency between plateaus
        if gap.baseline_plateau_id and gap.target_plateau_id:
            baseline = ImplementationPlateau.query.get(gap.baseline_plateau_id)
            target = ImplementationPlateau.query.get(gap.target_plateau_id)
            if baseline and target and baseline.end_date and target.start_date:
                if baseline.end_date >= target.start_date:
                    warnings.append("Target plateau should start after baseline plateau ends")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "element_id": gap.id,
            "element_type": "ImplementationGap",
            "element_name": gap.name,
        }

    def validate_plateau(self, plateau: ImplementationPlateau) -> Dict[str, Any]:
        """
        Validate a ImplementationPlateau element according to ArchiMate 3.2 rules.

        Args:
            plateau: ImplementationPlateau instance to validate

        Returns:
            Dictionary containing validation results
        """
        errors = []
        warnings = []

        # Validate required attributes
        if not plateau.name or not plateau.name.strip():
            errors.append("Plateau must have a name")

        if not plateau.start_date or not plateau.end_date:
            errors.append("Plateau must have both start and end dates")

        # Validate date consistency
        if plateau.start_date and plateau.end_date:
            if plateau.start_date >= plateau.end_date:
                errors.append("Plateau start date must be before end date")

            # Check for reasonable duration
            duration = (plateau.end_date - plateau.start_date).days
            if duration < 7:  # Less than 1 week
                warnings.append(
                    "Plateau duration is less than 1 week - may be too short for stable state"
                )
            elif duration > 365 * 10:  # More than 10 years
                warnings.append(
                    "Plateau duration exceeds 10 years - may be too long for realistic planning"
                )

        # Validate plateau type
        valid_plateau_types = ["baseline", "interim", "target", "future", "current"]
        if plateau.plateau_type and plateau.plateau_type not in valid_plateau_types:
            warnings.append(
                f"Plateau type '{plateau.plateau_type}' is not a standard ArchiMate plateau type"
            )

        # Validate transition relationships
        if plateau.transition_from_plateau_id:
            from_plateau = ImplementationPlateau.query.get(plateau.transition_from_plateau_id)
            if not from_plateau:
                errors.append("Transition from plateau does not exist")
            elif from_plateau.end_date and plateau.start_date:
                if from_plateau.end_date > plateau.start_date:
                    warnings.append("Plateau should start after transition from plateau ends")

        if plateau.transition_to_plateau_id:
            to_plateau = ImplementationPlateau.query.get(plateau.transition_to_plateau_id)
            if not to_plateau:
                errors.append("Transition to plateau does not exist")
            elif to_plateau.start_date and plateau.end_date:
                if plateau.end_date > to_plateau.start_date:
                    warnings.append("Transition to plateau should start after this plateau ends")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "element_id": plateau.id,
            "element_type": "ImplementationPlateau",
            "element_name": plateau.name,
        }

    def validate_implementation_event(self, event: ImplementationEvent) -> Dict[str, Any]:
        """
        Validate an ImplementationEvent element according to ArchiMate 3.2 rules.

        Args:
            event: ImplementationEvent instance to validate

        Returns:
            Dictionary containing validation results
        """
        errors = []
        warnings = []

        # Validate required attributes
        if not event.name or not event.name.strip():
            errors.append("ImplementationEvent must have a name")

        if not event.event_date:
            errors.append("ImplementationEvent must have an event date")

        # Validate event type
        valid_event_types = [
            "milestone",
            "decision",
            "review",
            "approval",
            "issue",
            "risk",
            "change_request",
            "stakeholder_meeting",
        ]
        if event.event_type and event.event_type not in valid_event_types:
            warnings.append(
                f"Event type '{event.event_type}' is not a standard ArchiMate implementation event type"
            )

        # Validate temporal consistency with work package
        if event.work_package_id:
            wp = ImplementationWorkPackage.query.get(event.work_package_id)
            if not wp:
                errors.append("Associated ImplementationWorkPackage does not exist")
            else:
                if wp.start_date and wp.end_date and event.event_date:
                    if event.event_date < wp.start_date or event.event_date > wp.end_date:
                        warnings.append("Event date is outside ImplementationWorkPackage duration")

        # Validate follow-up consistency
        if event.follow_up_required and not event.follow_up_actions:
            warnings.append("Event requires follow-up but no follow-up actions specified")

        if event.next_event_date and event.event_date:
            if event.next_event_date <= event.event_date:
                warnings.append("Next event date should be after current event date")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "element_id": event.id,
            "element_type": "ImplementationEvent",
            "element_name": event.name,
        }

    def validate_relationship(self, relationship: ArchiMateRelationship) -> Dict[str, Any]:
        """
        Validate an ArchiMate relationship between implementation elements.

        Args:
            relationship: ArchiMateRelationship instance to validate

        Returns:
            Dictionary containing validation results
        """
        errors = []
        warnings = []

        # Get relationship rules
        rel_rules = self.RELATIONSHIP_RULES.get(relationship.type)
        if not rel_rules:
            errors.append(f"Unknown relationship type: {relationship.type}")
            return {
                "valid": len(errors) == 0,
                "errors": errors,
                "warnings": warnings,
                "relationship_id": relationship.id,
                "relationship_type": relationship.type,
            }

        # Get source and target elements
        source_element = ArchiMateElement.query.get(relationship.source_id)
        target_element = ArchiMateElement.query.get(relationship.target_id)

        if not source_element:
            errors.append("Source element does not exist")

        if not target_element:
            errors.append("Target element does not exist")

        if source_element and target_element:
            # Validate source element type
            if source_element.type not in rel_rules["valid_sources"]:
                errors.append(
                    f"Source element type '{source_element.type}' not valid for {relationship.type} relationship"
                )

            # Validate target element type
            if target_element.type not in rel_rules["valid_targets"]:
                errors.append(
                    f"Target element type '{target_element.type}' not valid for {relationship.type} relationship"
                )

            # Validate cross-layer constraints
            if rel_rules.get("same_layer_only") and source_element.layer != target_element.layer:
                errors.append(f"{relationship.type} relationship must be within the same layer")

            # Validate temporal relationships
            if rel_rules.get("temporal"):
                temporal_validation = self._validate_temporal_relationship(
                    source_element, target_element, relationship
                )
                errors.extend(temporal_validation["errors"])
                warnings.extend(temporal_validation["warnings"])

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "relationship_id": relationship.id,
            "relationship_type": relationship.type,
            "source_element": source_element.name if source_element else "Unknown",
            "target_element": target_element.name if target_element else "Unknown",
        }

    def _validate_temporal_relationship(
        self,
        source: ArchiMateElement,
        target: ArchiMateElement,
        relationship: ArchiMateRelationship,
    ) -> Dict[str, List[str]]:
        """
        Validate temporal relationship constraints.

        Args:
            source: Source element
            target: Target element
            relationship: Relationship to validate

        Returns:
            Dictionary with errors and warnings
        """
        errors = []
        warnings = []

        # Get implementation elements
        source_impl = self._get_implementation_element(source)
        target_impl = self._get_implementation_element(target)

        if source_impl and target_impl:
            # For triggering relationships, source should occur before target
            if relationship.type == "triggering":
                if hasattr(source_impl, "event_date") and hasattr(target_impl, "start_date"):
                    if source_impl.event_date >= target_impl.start_date:
                        warnings.append("Triggering event should occur before target starts")
                elif hasattr(source_impl, "start_date") and hasattr(target_impl, "start_date"):
                    if source_impl.start_date >= target_impl.start_date:
                        warnings.append("Triggering source should start before target starts")

        return {"errors": errors, "warnings": warnings}

    def _get_implementation_element(self, archimate_element: ArchiMateElement) -> Optional[Any]:
        """
        Get the implementation element corresponding to an ArchiMate element.

        Args:
            archimate_element: ArchiMateElement instance

        Returns:
            Implementation element instance or None
        """
        if archimate_element.type == "ImplementationWorkPackage":
            return ImplementationWorkPackage.query.get(archimate_element.id)
        elif archimate_element.type == "Deliverable":
            return Deliverable.query.get(archimate_element.id)
        elif archimate_element.type == "ImplementationEvent":
            return ImplementationEvent.query.get(archimate_element.id)
        elif archimate_element.type == "ImplementationPlateau":
            return ImplementationPlateau.query.get(archimate_element.id)
        elif archimate_element.type == "ImplementationGap":
            return ImplementationGap.query.filter_by(id=archimate_element.id).first()

        return None

    def validate_architecture_model(self, architecture_id: int) -> Dict[str, Any]:
        """
        Validate all implementation elements in an architecture model.

        Args:
            architecture_id: Architecture model ID

        Returns:
            Dictionary containing comprehensive validation results
        """
        all_errors = []
        all_warnings = []
        element_results = []
        relationship_results = []

        try:
            # Validate WorkPackages
            work_packages = ImplementationWorkPackage.query.filter_by(
                architecture_id=architecture_id
            ).all()
            for wp in work_packages:
                result = self.validate_work_package(wp)
                element_results.append(result)
                all_errors.extend(result["errors"])
                all_warnings.extend(result["warnings"])

            # Validate Deliverables
            deliverables = Deliverable.query.filter_by(architecture_id=architecture_id).all()
            for deliverable in deliverables:
                result = self.validate_deliverable(deliverable)
                element_results.append(result)
                all_errors.extend(result["errors"])
                all_warnings.extend(result["warnings"])

            # Validate Gaps
            gaps = ImplementationGap.query.filter_by(architecture_id=architecture_id).all()
            for gap in gaps:
                result = self.validate_gap(gap)
                element_results.append(result)
                all_errors.extend(result["errors"])
                all_warnings.extend(result["warnings"])

            # Validate Plateaus
            plateaus = ImplementationPlateau.query.filter_by(architecture_id=architecture_id).all()
            for plateau in plateaus:
                result = self.validate_plateau(plateau)
                element_results.append(result)
                all_errors.extend(result["errors"])
                all_warnings.extend(result["warnings"])

            # Validate ImplementationEvents
            events = ImplementationEvent.query.filter_by(architecture_id=architecture_id).all()
            for event in events:
                result = self.validate_implementation_event(event)
                element_results.append(result)
                all_errors.extend(result["errors"])
                all_warnings.extend(result["warnings"])

            # Validate relationships
            relationships = ArchiMateRelationship.query.filter_by(
                architecture_id=architecture_id
            ).all()
            for rel in relationships:
                # Check if relationship involves implementation elements
                source = ArchiMateElement.query.get(rel.source_id)
                target = ArchiMateElement.query.get(rel.target_id)

                if (source and source.layer == "implementation") or (
                    target and target.layer == "implementation"
                ):
                    result = self.validate_relationship(rel)
                    relationship_results.append(result)
                    all_errors.extend(result["errors"])
                    all_warnings.extend(result["warnings"])

            # Generate summary
            total_elements = len(element_results)
            valid_elements = len([r for r in element_results if r["valid"]])
            total_relationships = len(relationship_results)
            valid_relationships = len([r for r in relationship_results if r["valid"]])

            return {
                "valid": len(all_errors) == 0,
                "total_errors": len(all_errors),
                "total_warnings": len(all_warnings),
                "element_validation": {
                    "total_elements": total_elements,
                    "valid_elements": valid_elements,
                    "invalid_elements": total_elements - valid_elements,
                    "results": element_results,
                },
                "relationship_validation": {
                    "total_relationships": total_relationships,
                    "valid_relationships": valid_relationships,
                    "invalid_relationships": total_relationships - valid_relationships,
                    "results": relationship_results,
                },
                "all_errors": all_errors,
                "all_warnings": all_warnings,
                "architecture_id": architecture_id,
                "validation_timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Error validating architecture model {architecture_id}: {e}")
            return {
                "valid": False,
                "total_errors": 1,
                "total_warnings": 0,
                "all_errors": [f"Validation error: {str(e)}"],
                "all_warnings": [],
                "architecture_id": architecture_id,
                "validation_timestamp": datetime.utcnow().isoformat(),
            }

    def generate_validation_report(self, validation_results: Dict[str, Any]) -> str:
        """
        Generate a human-readable validation report.

        Args:
            validation_results: Results from validate_architecture_model

        Returns:
            Formatted validation report as string
        """
        report = []
        report.append("ARCHIMATE 3.2 IMPLEMENTATION VALIDATION REPORT")
        report.append("=" * 50)
        report.append(f"Architecture ID: {validation_results['architecture_id']}")
        report.append(f"Validation Date: {validation_results['validation_timestamp']}")
        report.append(f"Overall Status: {'VALID' if validation_results['valid'] else 'INVALID'}")
        report.append("")

        # Element validation summary
        elem_val = validation_results["element_validation"]
        report.append("ELEMENT VALIDATION SUMMARY")
        report.append("-" * 30)
        report.append(f"Total Elements: {elem_val['total_elements']}")
        report.append(f"Valid Elements: {elem_val['valid_elements']}")
        report.append(f"Invalid Elements: {elem_val['invalid_elements']}")
        report.append("")

        # Relationship validation summary
        rel_val = validation_results["relationship_validation"]
        report.append("RELATIONSHIP VALIDATION SUMMARY")
        report.append("-" * 35)
        report.append(f"Total Relationships: {rel_val['total_relationships']}")
        report.append(f"Valid Relationships: {rel_val['valid_relationships']}")
        report.append(f"Invalid Relationships: {rel_val['invalid_relationships']}")
        report.append("")

        # Errors
        if validation_results["all_errors"]:
            report.append("ERRORS")
            report.append("-" * 6)
            for i, error in enumerate(validation_results["all_errors"], 1):
                report.append(f"{i}. {error}")
            report.append("")

        # Warnings
        if validation_results["all_warnings"]:
            report.append("WARNINGS")
            report.append("-" * 8)
            for i, warning in enumerate(validation_results["all_warnings"], 1):
                report.append(f"{i}. {warning}")
            report.append("")

        # Detailed element results
        if elem_val["results"]:
            report.append("DETAILED ELEMENT RESULTS")
            report.append("-" * 25)
            for result in elem_val["results"]:
                status = "VALID" if result["valid"] else "INVALID"
                report.append(f"{result['element_type']} - {result['element_name']}: {status}")
                if result["errors"]:
                    for error in result["errors"]:
                        report.append(f"  ERROR: {error}")
                if result["warnings"]:
                    for warning in result["warnings"]:
                        report.append(f"  WARNING: {warning}")
                report.append("")

        return "\n".join(report)
