"""
-> app.modules.architecture.services.archimate_service

ArchiMate Relationship Validation Service

This service validates ArchiMate relationships against the ArchiMate 3.2 specification
and logs violations to the database for governance and compliance tracking.

Week 2 Implementation:
- Core validation methods
- Integration with database rules
- Violation logging and tracking
- Helpful error messages with suggestions

Complies with:
- ArchiMate 3.2 Specification (The Open Group)
- NO_HARDCODED_DATA_POLICY.md (all rules from database)
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.constants import ArchiMateLayer, ArchiMateRelationshipType
from app.models.archimate_metamodel import ArchiMateRelationshipRule, MetamodelViolation

# ---------------------------------------------------------------------------
# ARCH-016: ArchiMate 3.2 metamodel validation constants
# ---------------------------------------------------------------------------

# Composer-QA 3.2 / 3.3: the validator compared un-normalized values and fired
# false-positive warnings/errors on entirely valid models.
#   - relationship types are stored short-form and mixed-case ("realization",
#     "Realization", "Flow"), but the rule lists use "RealizationRelationship";
#     `rel.type not in allowed` was therefore ALWAYS true.
#   - element layers are stored as "implementation & migration", but the tables
#     key on "implementation"; the lookup missed and reported the element (and
#     any cross-layer rule for it) as invalid.
# Both are fixed by normalizing before comparison, here in one place.

def _norm_layer(layer: str) -> str:
    """Canonical lowercase layer key (maps 'implementation & migration' etc.)."""
    return ArchiMateLayer.normalize(layer) or ""


def _norm_rel(rel_type: str) -> str:
    """Canonical short relationship name: lowercase, no 'Relationship' suffix.

    Maps 'RealizationRelationship', 'Realization' and 'realization' all to
    'realization', so a rules table written either way compares correctly.
    """
    return ArchiMateRelationshipType.normalize(rel_type) or ""

# Cross-layer relationship constraints (lowercase layer keys matching DB values).
# Format: {(source_layer, target_layer): [allowed_relationship_types]}
LAYER_RELATIONSHIP_RULES = {
    ('technology', 'application'): ['ServingRelationship', 'RealizationRelationship'],
    ('application', 'business'): ['ServingRelationship', 'RealizationRelationship'],
    ('strategy', 'business'): ['RealizationRelationship', 'AssociationRelationship', 'InfluenceRelationship'],
    ('strategy', 'application'): ['RealizationRelationship'],
    ('strategy', 'technology'): ['RealizationRelationship'],
    ('motivation', 'business'): ['AssociationRelationship', 'InfluenceRelationship'],
    ('motivation', 'application'): ['AssociationRelationship'],
    ('motivation', 'technology'): ['AssociationRelationship'],
    ('implementation', 'business'): ['AssociationRelationship', 'RealizationRelationship'],
    ('implementation', 'application'): ['AssociationRelationship', 'RealizationRelationship'],
    ('implementation', 'technology'): ['AssociationRelationship', 'RealizationRelationship'],
    # Business ↔ Technology direct is a violation (must go through Application)
    ('business', 'technology'): [],
    ('technology', 'business'): [],
}

# ArchiMate 3.2 element types per layer (lowercase layer keys)
LAYER_TYPES = {
    'business': [
        'BusinessActor', 'BusinessRole', 'BusinessCollaboration', 'BusinessInterface',
        'BusinessProcess', 'BusinessFunction', 'BusinessInteraction', 'BusinessEvent',
        'BusinessService', 'BusinessObject', 'Contract', 'Representation',
    ],
    'application': [
        'ApplicationComponent', 'ApplicationInterface', 'ApplicationService',
        'ApplicationFunction', 'ApplicationProcess', 'ApplicationInteraction',
        'ApplicationCollaboration', 'ApplicationEvent', 'DataObject',
    ],
    'technology': [
        'Node', 'Device', 'SystemSoftware', 'TechnologyService', 'TechnologyInterface',
        'TechnologyCollaboration', 'TechnologyFunction', 'TechnologyProcess',
        'TechnologyInteraction', 'TechnologyEvent', 'Path', 'CommunicationNetwork', 'Artifact',
    ],
    'strategy': ['Resource', 'Capability', 'CourseOfAction', 'ValueStream'],
    'motivation': [
        'Stakeholder', 'Driver', 'Assessment', 'Goal', 'Outcome',
        'Principle', 'Requirement', 'Constraint', 'Meaning', 'Value',
    ],
    'implementation': ['WorkPackage', 'Deliverable', 'ImplementationEvent', 'Plateau', 'Gap'],
    'physical': ['Equipment', 'Facility', 'DistributionNetwork', 'Material'],
}

REQUIRED_ELEMENT_FIELDS = ['name', 'layer', 'type']

# from app.services.decorators import transactional  # Temporarily disabled


class ArchiMateValidationService:
    """
    Service for validating ArchiMate relationships against metamodel rules.

    All validation rules are loaded from the database (NO hardcoded rules).
    Provides comprehensive error messages and suggestions for valid alternatives.
    """

    @staticmethod
    def validate_relationship(
        source_type: str, target_type: str, relationship_type: str
    ) -> Tuple[bool, Optional[str], Optional[ArchiMateRelationshipRule]]:
        """
        Validate if a relationship is allowed according to ArchiMate 3.2 rules.

        Args:
            source_type: Element type of the source (e.g., "BusinessActor")
            target_type: Element type of the target (e.g., "BusinessRole")
            relationship_type: Type of relationship (e.g., "assignment")

        Returns:
            Tuple containing:
                - is_valid (bool): True if relationship is allowed
                - error_message (str or None): Detailed error message if invalid
                - rule (ArchiMateRelationshipRule or None): The rule that was matched

        Example:
            >>> is_valid, error, rule = validate_relationship(
            ...     "BusinessActor", "ApplicationComponent", "composition"
            ... )
            >>> if not is_valid:
            ...     print(error)  # "Cannot create 'composition' relationship..."
        """
        try:
            # Query the database for the specific rule
            rule = ArchiMateRelationshipRule.query.filter_by(
                source_element_type=source_type,
                target_element_type=target_type,
                relationship_type=relationship_type,
            ).first()

            if rule:
                if rule.is_allowed:
                    # Valid relationship found
                    return (True, None, rule)
                else:
                    # Explicitly forbidden relationship
                    error_msg = (
                        f"Cannot create '{relationship_type}' relationship from "
                        f"{source_type} to {target_type}. This violates ArchiMate 3.2 rules."
                    )
                    if rule.description:
                        error_msg += f"\n\nReason: {rule.description}"

                    # Add suggestions
                    suggestions = ArchiMateValidationService._get_suggestions(
                        source_type, target_type, relationship_type
                    )
                    if suggestions:
                        error_msg += f"\n\n{suggestions}"

                    return (False, error_msg, rule)

            # No rule found - default to forbidden (fail-safe approach)
            error_msg = (
                f"Cannot create '{relationship_type}' relationship from "
                f"{source_type} to {target_type}. No validation rule found in database."
            )

            # Add suggestions even when no rule exists
            suggestions = ArchiMateValidationService._get_suggestions(
                source_type, target_type, relationship_type
            )
            if suggestions:
                error_msg += f"\n\n{suggestions}"
            else:
                error_msg += (
                    "\n\nPlease ensure ArchiMate relationship rules are seeded in the database. "
                    "Run: flask seed-archimate-rules"
                )

            return (False, error_msg, None)

        except SQLAlchemyError as e:
            current_app.logger.error(f"Database error during relationship validation: {e}")
            return (
                False,
                "Unable to validate relationship due to database error. Please try again.",
                None,
            )

    @staticmethod
    def _get_suggestions(
        source_type: str, target_type: str, relationship_type: str
    ) -> Optional[str]:
        """
        Get suggestions for valid alternative relationships.

        Args:
            source_type: Source element type
            target_type: Target element type
            relationship_type: Attempted relationship type

        Returns:
            String with suggestions or None if no alternatives found
        """
        suggestions = []

        # Find valid relationships from source to target (any relationship type)
        valid_to_target = (
            ArchiMateRelationshipRule.query.filter_by(
                source_element_type=source_type, target_element_type=target_type, is_allowed=True
            )
            .limit(3)
            .all()
        )

        if valid_to_target:
            rel_types = [f"'{r.relationship_type}'" for r in valid_to_target]
            suggestions.append(
                f"Valid relationships from {source_type} to {target_type}: "
                f"{', '.join(rel_types)}"
            )

        # Find valid targets for this relationship type from this source
        valid_targets = (
            ArchiMateRelationshipRule.query.filter_by(
                source_element_type=source_type,
                relationship_type=relationship_type,
                is_allowed=True,
            )
            .limit(3)
            .all()
        )

        if valid_targets:
            target_types = [f"'{r.target_element_type}'" for r in valid_targets]
            suggestions.append(
                f"Valid '{relationship_type}' targets from {source_type}: "
                f"{', '.join(target_types)}"
            )

        return "\n".join(suggestions) if suggestions else None

    @staticmethod
    def get_allowed_relationships(source_type: str) -> List[Dict[str, str]]:
        """
        Get all allowed relationships for a given source element type.

        Args:
            source_type: Element type (e.g., "BusinessActor")

        Returns:
            List of dicts containing:
                - target_type: Valid target element type
                - relationship_type: Valid relationship type
                - description: Human-readable description

        Example:
            >>> relationships = get_allowed_relationships("BusinessActor")
            >>> for rel in relationships:
            ...     print(f"{rel['relationship_type']} -> {rel['target_type']}")
        """
        try:
            rules = ArchiMateRelationshipRule.query.filter_by(
                source_element_type=source_type, is_allowed=True
            ).all()

            return [
                {
                    "target_type": rule.target_element_type,
                    "relationship_type": rule.relationship_type,
                    "description": rule.description
                    or f"{source_type} can {rule.relationship_type} {rule.target_element_type}",
                    "layer_target": rule.layer_target,
                }
                for rule in rules
            ]
        except SQLAlchemyError as e:
            current_app.logger.error(f"Database error getting allowed relationships: {e}")
            return []

    @staticmethod
    def get_allowed_targets(source_type: str, relationship_type: str) -> List[str]:
        """
        Get all allowed target element types for a specific source and relationship type.

        Args:
            source_type: Source element type
            relationship_type: Relationship type

        Returns:
            List of valid target element types

        Example:
            >>> targets = get_allowed_targets("BusinessActor", "assignment")
            >>> print(targets)  # ['BusinessRole', 'BusinessProcess', ...]
        """
        try:
            rules = ArchiMateRelationshipRule.query.filter_by(
                source_element_type=source_type,
                relationship_type=relationship_type,
                is_allowed=True,
            ).all()

            return [rule.target_element_type for rule in rules]
        except SQLAlchemyError as e:
            current_app.logger.error(f"Database error getting allowed targets: {e}")
            return []

    @staticmethod
    def get_allowed_relationship_types(source_type: str, target_type: str) -> List[str]:
        """
        Get all allowed relationship types between two specific element types.

        Args:
            source_type: Source element type
            target_type: Target element type

        Returns:
            List of valid relationship types

        Example:
            >>> rel_types = get_allowed_relationship_types("BusinessActor", "BusinessRole")
            >>> print(rel_types)  # ['assignment', 'association', ...]
        """
        try:
            rules = ArchiMateRelationshipRule.query.filter_by(
                source_element_type=source_type, target_element_type=target_type, is_allowed=True
            ).all()

            return [rule.relationship_type for rule in rules]
        except SQLAlchemyError as e:
            current_app.logger.error(f"Database error getting allowed relationship types: {e}")
            return []

    @staticmethod
    def log_violation(
        source_element_id: Optional[int],
        target_element_id: Optional[int],
        relationship_type: str,
        user_id: Optional[int] = None,
        source_type: Optional[str] = None,
        target_type: Optional[str] = None,
        violation_type: str = "invalid_relationship",
        severity: str = "warning",
        auto_corrected: bool = False,
        correction_action: Optional[str] = None,
    ) -> Optional[MetamodelViolation]:
        """
        Log a metamodel violation to the database.

        Args:
            source_element_id: ID of source ArchiMateElement (if known)
            target_element_id: ID of target ArchiMateElement (if known)
            relationship_type: Type of relationship attempted
            user_id: ID of user who triggered the violation
            source_type: Element type of source (optional if source_element_id provided)
            target_type: Element type of target (optional if target_element_id provided)
            violation_type: Type of violation (default: 'invalid_relationship')
            severity: 'info', 'warning', 'error', or 'critical' (default: 'warning')
            auto_corrected: Was the violation automatically corrected?
            correction_action: Description of correction taken

        Returns:
            MetamodelViolation object or None if logging failed

        Example:
            >>> violation = log_violation(
            ...     source_element_id=123,
            ...     target_element_id=456,
            ...     relationship_type="composition",
            ...     user_id=1,
            ...     severity="error"
            ... )
        """
        try:
            # Resolve element types from IDs if not provided
            if source_element_id and not source_type:
                source_elem = db.session.get(ArchiMateElement, source_element_id)
                source_type = source_elem.type if source_elem else "Unknown"

            if target_element_id and not target_type:
                target_elem = db.session.get(ArchiMateElement, target_element_id)
                target_type = target_elem.type if target_elem else "Unknown"

            # Generate violation message
            message = (
                f"Attempted to create invalid '{relationship_type}' relationship "
                f"from {source_type or 'Unknown'} to {target_type or 'Unknown'}. "
                f"This violates ArchiMate 3.2 metamodel rules."
            )

            # Create violation record
            violation = MetamodelViolation(
                source_element_type=source_type or "Unknown",
                target_element_type=target_type or "Unknown",
                relationship_type=relationship_type,
                source_element_id=source_element_id,
                target_element_id=target_element_id,
                violation_type=violation_type,
                severity=severity,
                message=message,
                user_id=user_id,
                auto_corrected=auto_corrected,
                correction_action=correction_action,
                detected_at=datetime.utcnow(),
            )

            db.session.add(violation)
            db.session.commit()

            current_app.logger.warning(
                f"ArchiMate violation logged: {source_type} --{relationship_type}--> {target_type}"
            )

            return violation

        except SQLAlchemyError as e:
            current_app.logger.error(f"Failed to log violation: {e}")
            db.session.rollback()
            return None

    @staticmethod
    def validate_and_log(
        source_element_id: int,
        target_element_id: int,
        relationship_type: str,
        user_id: Optional[int] = None,
        severity: str = "warning",
    ) -> Tuple[bool, Optional[str], Optional[ArchiMateRelationshipRule]]:
        """
        Validate a relationship and automatically log violation if invalid.

        Convenience method that combines validation and logging.

        Args:
            source_element_id: ID of source element
            target_element_id: ID of target element
            relationship_type: Type of relationship
            user_id: ID of user creating the relationship
            severity: Severity level for violation logging

        Returns:
            Same as validate_relationship: (is_valid, error_message, rule)

        Example:
            >>> is_valid, error, rule = validate_and_log(123, 456, "composition", user_id=1)
            >>> if not is_valid:
            ...     flash(error, 'error')
        """
        try:
            # Get element types
            source_elem = db.session.get(ArchiMateElement, source_element_id)
            target_elem = db.session.get(ArchiMateElement, target_element_id)

            if not source_elem or not target_elem:
                return (False, "Source or target element not found in database.", None)

            # Validate
            is_valid, error_msg, rule = ArchiMateValidationService.validate_relationship(
                source_elem.type, target_elem.type, relationship_type
            )

            # Log violation if invalid
            if not is_valid:
                ArchiMateValidationService.log_violation(
                    source_element_id=source_element_id,
                    target_element_id=target_element_id,
                    relationship_type=relationship_type,
                    user_id=user_id,
                    source_type=source_elem.type,
                    target_type=target_elem.type,
                    severity=severity,
                )

            return (is_valid, error_msg, rule)

        except SQLAlchemyError as e:
            current_app.logger.error(f"Database error in validate_and_log: {e}")
            return (False, "Unable to validate relationship due to database error.", None)

    @staticmethod
    def get_violation_summary(days: int = 30) -> Dict[str, any]:
        """
        Get summary statistics of violations over the past N days.

        Args:
            days: Number of days to look back (default: 30)

        Returns:
            Dict with summary statistics:
                - total_violations: Total count
                - by_severity: Count by severity level
                - by_type: Count by violation type
                - recent_violations: List of recent violations
        """
        try:
            from datetime import timedelta

            cutoff_date = datetime.utcnow() - timedelta(days=days)

            violations = MetamodelViolation.query.filter(
                MetamodelViolation.detected_at >= cutoff_date
            ).all()

            # Count by severity
            by_severity = {}
            for v in violations:
                by_severity[v.severity] = by_severity.get(v.severity, 0) + 1

            # Count by type
            by_type = {}
            for v in violations:
                by_type[v.violation_type] = by_type.get(v.violation_type, 0) + 1

            # Recent violations (last 10)
            recent = (
                MetamodelViolation.query.filter(MetamodelViolation.detected_at >= cutoff_date)
                .order_by(MetamodelViolation.detected_at.desc())
                .limit(10)
                .all()
            )

            return {
                "total_violations": len(violations),
                "by_severity": by_severity,
                "by_type": by_type,
                "recent_violations": [
                    {
                        "id": v.id,
                        "source_type": v.source_element_type,
                        "target_type": v.target_element_type,
                        "relationship_type": v.relationship_type,
                        "severity": v.severity,
                        "detected_at": v.detected_at.isoformat(),
                        "message": v.message,
                    }
                    for v in recent
                ],
            }
        except SQLAlchemyError as e:
            current_app.logger.error(f"Error getting violation summary: {e}")
            return {
                "total_violations": 0,
                "by_severity": {},
                "by_type": {},
                "recent_violations": [],
            }


    # ------------------------------------------------------------------
    # ARCH-016: Element / full-model validation (instance methods)
    # ------------------------------------------------------------------

    def validate_element(self, element: ArchiMateElement) -> list:
        """Return list of validation issue dicts for a single ArchiMate element."""
        issues = []
        for field in REQUIRED_ELEMENT_FIELDS:
            val = getattr(element, field, None)
            if not val:
                issues.append({
                    'severity': 'error',
                    'field': field,
                    'message': f'{field} is required',
                })
        layer = getattr(element, 'layer', None)
        element_type = getattr(element, 'type', None)
        if layer and element_type and not self._is_valid_type_for_layer(element_type, layer):
            issues.append({
                'severity': 'error',
                'field': 'type',
                'message': f'{element_type} is not valid for {layer} layer',
            })
        return issues

    def _check_cross_layer(self, rel: ArchiMateRelationship) -> list:
        """Return validation issues for cross-layer relationship constraints."""
        issues = []
        if not rel.source_id or not rel.target_id:
            issues.append({'severity': 'error', 'message': 'Relationship must have source and target'})
            return issues
        source = ArchiMateElement.query.get(rel.source_id)
        target = ArchiMateElement.query.get(rel.target_id)
        if not source:
            issues.append({'severity': 'error', 'message': f'Source element {rel.source_id} not found'})
        if not target:
            issues.append({'severity': 'error', 'message': f'Target element {rel.target_id} not found'})
        if source and target and _norm_layer(source.layer) != _norm_layer(target.layer):
            src_layer = _norm_layer(source.layer)
            tgt_layer = _norm_layer(target.layer)
            allowed = LAYER_RELATIONSHIP_RULES.get((src_layer, tgt_layer))
            # 3.2: compare normalized short names so "realization" matches
            # "RealizationRelationship" — the old raw membership test never did.
            allowed_norm = {_norm_rel(a) for a in (allowed or [])}
            if allowed is not None and _norm_rel(rel.type) not in allowed_norm:
                if allowed:
                    issues.append({
                        'severity': 'warning',
                        'message': (
                            f'Cross-layer relationship {src_layer}→{tgt_layer}: '
                            f'only {allowed} allowed, got {rel.type}'
                        ),
                    })
                else:
                    issues.append({
                        'severity': 'error',
                        'message': (
                            f'Direct relationship from {src_layer} to {tgt_layer} '
                            f'is not allowed in ArchiMate 3.2'
                        ),
                    })
        return issues

    def validate_all(self) -> dict:
        """Run full model validation. Returns summary + issue lists (capped at 50 each)."""
        element_issues = []
        relationship_issues = []
        for el in ArchiMateElement.query.all():
            issues = self.validate_element(el)
            if issues:
                element_issues.append({
                    'element': {'id': el.id, 'name': el.name, 'layer': el.layer},
                    'issues': issues,
                })
        for rel in ArchiMateRelationship.query.all():
            issues = self._check_cross_layer(rel)
            if issues:
                relationship_issues.append({
                    'relationship': {'id': rel.id, 'type': rel.type},
                    'issues': issues,
                })
        return {
            'element_errors': sum(
                1 for e in element_issues for i in e['issues'] if i['severity'] == 'error'
            ),
            'element_warnings': sum(
                1 for e in element_issues for i in e['issues'] if i['severity'] == 'warning'
            ),
            'relationship_errors': sum(
                1 for r in relationship_issues for i in r['issues'] if i['severity'] == 'error'
            ),
            'relationship_warnings': sum(
                1 for r in relationship_issues for i in r['issues'] if i['severity'] == 'warning'
            ),
            'element_issues': element_issues[:50],
            'relationship_issues': relationship_issues[:50],
        }

    def _is_valid_type_for_layer(self, element_type: str, layer: str) -> bool:
        """Return True if element_type belongs to the given layer per ArchiMate 3.2.

        3.3: normalize the layer name ('implementation & migration' -> the
        'implementation' table key) so a valid element like WorkPackage is not
        falsely flagged.
        """
        return element_type in LAYER_TYPES.get(_norm_layer(layer), [])


# Convenience functions for common use cases
def validate_relationship(source_type: str, target_type: str, relationship_type: str):
    """Convenience function - calls ArchiMateValidationService.validate_relationship"""
    return ArchiMateValidationService.validate_relationship(
        source_type, target_type, relationship_type
    )


def validate_and_log(
    source_element_id: int,
    target_element_id: int,
    relationship_type: str,
    user_id: Optional[int] = None,
):
    """Convenience function - calls ArchiMateValidationService.validate_and_log"""
    return ArchiMateValidationService.validate_and_log(
        source_element_id, target_element_id, relationship_type, user_id
    )
