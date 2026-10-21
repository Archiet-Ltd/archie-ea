"""
ArchiMate Relationship Validator Service
PRD - 009.2: Comprehensive relationship validation

Provides:
- Relationship validation against ArchiMate 3.2 spec
- Cardinality constraint checking
- Cross-layer relationship warnings
- Suggestions for valid alternatives
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.config.archimate_relationship_matrix import (
    ALL_ELEMENTS,
    APPLICATION_ELEMENTS,
    EXTENSION_ELEMENTS,
    BUSINESS_ELEMENTS,
    IMPLEMENTATION_ELEMENTS,
    MOTIVATION_ELEMENTS,
    PHYSICAL_ELEMENTS,
    RELATIONSHIP_CARDINALITY,
    RELATIONSHIP_TYPES,
    STRATEGY_ELEMENTS,
    TECHNOLOGY_ELEMENTS,
    VALID_RELATIONSHIPS,
    get_all_valid_sources_for_target,
    get_all_valid_targets_for_source,
    get_cardinality,
    get_valid_relationships,
)

from app.models.archimate_element_types import base_type  # noqa: E402

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of relationship validation"""

    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation"""
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "suggestions": self.suggestions,
        }


@dataclass
class BatchValidationResult:
    """Result of batch relationship validation"""

    total: int
    valid_count: int
    invalid_count: int
    results: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation"""
        return {
            "total": self.total,
            "valid_count": self.valid_count,
            "invalid_count": self.invalid_count,
            "results": self.results,
            "summary": self.summary,
        }


class RelationshipValidator:
    """
    Validates ArchiMate relationships against 3.2 specification.

    This service provides comprehensive validation for ArchiMate relationships,
    including:
    - Basic relationship type validation
    - Element type compatibility checking
    - Cross-layer relationship analysis
    - Cardinality constraint validation
    - Suggestions for valid alternatives

    Usage:
        validator = RelationshipValidator()
        result = validator.validate_relationship(
            source_type='ApplicationComponent',
            target_type='BusinessProcess',
            relationship_type='serving'
        )
        if not result.is_valid:
            print(result.errors)
            print(result.suggestions)
    """

    # Layer mapping for element types
    LAYER_MAPPING = {
        "strategy": STRATEGY_ELEMENTS,
        "business": BUSINESS_ELEMENTS,
        "application": APPLICATION_ELEMENTS,
        "technology": TECHNOLOGY_ELEMENTS,
        "physical": PHYSICAL_ELEMENTS,
        "motivation": MOTIVATION_ELEMENTS,
        "implementation": IMPLEMENTATION_ELEMENTS,
    }

    def __init__(self):
        """Initialize the relationship validator"""
        self._build_reverse_layer_mapping()

    def _build_reverse_layer_mapping(self):
        """Build reverse mapping from element type to layer"""
        self._element_to_layer = {}
        for layer, elements in self.LAYER_MAPPING.items():
            for element in elements:
                self._element_to_layer[element] = layer

    def validate_relationship(
        self, source_type: str, target_type: str, relationship_type: str
    ) -> ValidationResult:
        """
        Validate a single relationship against ArchiMate 3.2 specification.

        Args:
            source_type: The source ArchiMate element type (e.g., 'ApplicationComponent')
            target_type: The target ArchiMate element type (e.g., 'BusinessProcess')
            relationship_type: The relationship type (e.g., 'serving', 'realization')

        Returns:
            ValidationResult with is_valid, errors, warnings, and suggestions
        """
        errors = []
        warnings = []
        suggestions = []

        # Normalize inputs
        source_type = self._normalize_element_type(source_type)
        target_type = self._normalize_element_type(target_type)
        relationship_type = relationship_type.lower() if relationship_type else ""

        # Validate element types exist
        if source_type not in ALL_ELEMENTS and source_type not in EXTENSION_ELEMENTS:
            errors.append(f"Unknown source element type: '{source_type}'")
            similar = self._find_similar_element_types(source_type)
            if similar:
                suggestions.append(f"Did you mean: {', '.join(similar)}?")

        if target_type not in ALL_ELEMENTS and target_type not in EXTENSION_ELEMENTS:
            errors.append(f"Unknown target element type: '{target_type}'")
            similar = self._find_similar_element_types(target_type)
            if similar:
                suggestions.append(f"Did you mean: {', '.join(similar)}?")

        # Validate relationship type exists
        if relationship_type not in RELATIONSHIP_TYPES:
            errors.append(f"Unknown relationship type: '{relationship_type}'")
            suggestions.append(f"Valid relationship types: {', '.join(RELATIONSHIP_TYPES)}")

        # If basic validation failed, return early
        if errors:
            return ValidationResult(
                is_valid=False, errors=errors, warnings=warnings, suggestions=suggestions
            )

        # Check if relationship type is valid for this element pair
        valid_types = get_valid_relationships(source_type, target_type)

        if not valid_types:
            errors.append(
                f"No valid relationships defined between '{source_type}' and '{target_type}' "
                f"in ArchiMate 3.2 specification"
            )
            # Suggest reverse direction
            reverse_types = get_valid_relationships(target_type, source_type)
            if reverse_types:
                suggestions.append(
                    f"Try reversing direction: {target_type} -> {source_type} "
                    f"allows: {', '.join(reverse_types)}"
                )
            # Suggest valid targets for this source
            valid_targets = self._get_sample_valid_targets(source_type, 5)
            if valid_targets:
                suggestions.append(f"'{source_type}' can connect to: {', '.join(valid_targets)}")
        elif relationship_type not in valid_types:
            errors.append(
                f"Relationship type '{relationship_type}' is not valid between "
                f"'{source_type}' and '{target_type}'"
            )
            suggestions.append(f"Valid relationship types for this pair: {', '.join(valid_types)}")

        # Cross-layer analysis (warnings only, not errors)
        source_layer = self._get_element_layer(source_type)
        target_layer = self._get_element_layer(target_type)

        if source_layer and target_layer and source_layer != target_layer:
            warnings.append(f"Cross-layer relationship: {source_layer} -> {target_layer}")
            # Add specific cross-layer guidance
            cross_layer_guidance = self._get_cross_layer_guidance(
                source_layer, target_layer, relationship_type
            )
            if cross_layer_guidance:
                warnings.append(cross_layer_guidance)

        return ValidationResult(
            is_valid=len(errors) == 0, errors=errors, warnings=warnings, suggestions=suggestions
        )

    def validate_cardinality(
        self, source_element_id: int, relationship_type: str, existing_count: int
    ) -> ValidationResult:
        """
        Validate cardinality constraints for a relationship type.

        Args:
            source_element_id: ID of the source element
            relationship_type: The relationship type being validated
            existing_count: Current count of this relationship type from source

        Returns:
            ValidationResult with cardinality validation status
        """
        errors = []
        warnings = []
        suggestions = []

        relationship_type = relationship_type.lower() if relationship_type else ""

        if relationship_type not in RELATIONSHIP_CARDINALITY:
            errors.append(f"Unknown relationship type for cardinality check: '{relationship_type}'")
            return ValidationResult(
                is_valid=False, errors=errors, warnings=warnings, suggestions=suggestions
            )

        min_card, max_card = get_cardinality(relationship_type)

        # Check maximum cardinality
        if max_card is not None and existing_count >= max_card:
            errors.append(
                f"Maximum cardinality ({max_card}) exceeded for '{relationship_type}' relationships. "
                f"Current count: {existing_count}"
            )
            suggestions.append(
                "Consider using a different relationship type or restructuring the model"
            )

        # Warning for minimum cardinality (informational)
        if existing_count < min_card:
            warnings.append(
                f"Minimum cardinality ({min_card}) not met for '{relationship_type}'. "
                f"Current count: {existing_count}"
            )

        return ValidationResult(
            is_valid=len(errors) == 0, errors=errors, warnings=warnings, suggestions=suggestions
        )

    def validate_batch(self, relationships: List[Dict[str, str]]) -> BatchValidationResult:
        """
        Validate a batch of relationships.

        Args:
            relationships: List of dicts with 'source_type', 'target_type', 'relationship_type'

        Returns:
            BatchValidationResult with aggregated results
        """
        results = []
        valid_count = 0
        invalid_count = 0
        error_summary = {}

        for idx, rel in enumerate(relationships):
            source_type = rel.get("source_type", "")
            target_type = rel.get("target_type", "")
            relationship_type = rel.get("relationship_type", "")

            result = self.validate_relationship(source_type, target_type, relationship_type)

            results.append(
                {
                    "index": idx,
                    "source_type": source_type,
                    "target_type": target_type,
                    "relationship_type": relationship_type,
                    "is_valid": result.is_valid,
                    "errors": result.errors,
                    "warnings": result.warnings,
                    "suggestions": result.suggestions,
                }
            )

            if result.is_valid:
                valid_count += 1
            else:
                invalid_count += 1
                # Aggregate errors
                for error in result.errors:
                    error_key = error[:50]  # Truncate for grouping
                    error_summary[error_key] = error_summary.get(error_key, 0) + 1

        return BatchValidationResult(
            total=len(relationships),
            valid_count=valid_count,
            invalid_count=invalid_count,
            results=results,
            summary=error_summary,
        )

    def get_valid_relationship_types(self, source_type: str, target_type: str) -> List[str]:
        """
        Get all valid relationship types for an element pair.

        Args:
            source_type: The source ArchiMate element type
            target_type: The target ArchiMate element type

        Returns:
            List of valid relationship type names
        """
        source_type = self._normalize_element_type(source_type)
        target_type = self._normalize_element_type(target_type)
        return get_valid_relationships(source_type, target_type)

    def get_valid_targets(self, source_type: str, relationship_type: str) -> List[str]:
        """
        Get all valid target types for a source + relationship combination.

        Args:
            source_type: The source ArchiMate element type
            relationship_type: The relationship type

        Returns:
            List of valid target element types
        """
        source_type = self._normalize_element_type(source_type)
        relationship_type = relationship_type.lower() if relationship_type else ""

        valid_targets = []
        for (src, tgt), rel_types in VALID_RELATIONSHIPS.items():
            if src == source_type and relationship_type in rel_types:
                valid_targets.append(tgt)
        return sorted(valid_targets)

    def get_valid_sources(self, target_type: str, relationship_type: str) -> List[str]:
        """
        Get all valid source types for a target + relationship combination.

        Args:
            target_type: The target ArchiMate element type
            relationship_type: The relationship type

        Returns:
            List of valid source element types
        """
        target_type = self._normalize_element_type(target_type)
        relationship_type = relationship_type.lower() if relationship_type else ""

        valid_sources = []
        for (src, tgt), rel_types in VALID_RELATIONSHIPS.items():
            if tgt == target_type and relationship_type in rel_types:
                valid_sources.append(src)
        return sorted(valid_sources)

    def get_element_types_by_layer(self, layer: str) -> List[str]:
        """
        Get all element types for a specific layer.

        Args:
            layer: Layer name (strategy, business, application, technology,
                   physical, motivation, implementation)

        Returns:
            List of element types in that layer
        """
        layer = layer.lower()
        return list(self.LAYER_MAPPING.get(layer, []))

    def get_all_relationship_types(self) -> List[Dict[str, str]]:
        """
        Get all ArchiMate relationship types with descriptions.

        Returns:
            List of dicts with name and description for each relationship type
        """
        from app.config.archimate_relationship_matrix import RELATIONSHIP_TYPE_DEFINITIONS

        result = []
        for rel_type, definition in RELATIONSHIP_TYPE_DEFINITIONS.items():
            result.append(
                {
                    "name": rel_type,
                    "category": definition.category.value,
                    "description": definition.description,
                    "notation": definition.notation,
                    "is_directed": definition.is_directed,
                }
            )
        return result

    def suggest_relationships(self, source_type: str, target_type: str) -> Dict[str, Any]:
        """
        Suggest valid relationships and alternatives for an element pair.

        Args:
            source_type: The source ArchiMate element type
            target_type: The target ArchiMate element type

        Returns:
            Dict with direct relationships, reverse relationships, and alternatives
        """
        source_type = self._normalize_element_type(source_type)
        target_type = self._normalize_element_type(target_type)

        # Direct relationships
        direct = get_valid_relationships(source_type, target_type)

        # Reverse relationships
        reverse = get_valid_relationships(target_type, source_type)

        # Alternative targets for source
        alt_targets = {}
        source_targets = get_all_valid_targets_for_source(source_type)
        for tgt, rels in source_targets.items():
            if tgt != target_type:
                alt_targets[tgt] = rels

        # Alternative sources for target
        alt_sources = {}
        target_sources = get_all_valid_sources_for_target(target_type)
        for src, rels in target_sources.items():
            if src != source_type:
                alt_sources[src] = rels

        return {
            "direct_relationships": direct,
            "reverse_relationships": reverse,
            "reverse_direction": f"{target_type} -> {source_type}" if reverse else None,
            "alternative_targets": dict(list(alt_targets.items())[:10]),  # Limit for readability
            "alternative_sources": dict(list(alt_sources.items())[:10]),
            "source_layer": self._get_element_layer(source_type),
            "target_layer": self._get_element_layer(target_type),
        }

    def _get_element_layer(self, element_type: str) -> str:
        """Get layer for element type using internal mapping"""
        return self._element_to_layer.get(base_type(element_type), "unknown")

    def _normalize_element_type(self, element_type: str) -> str:
        """
        Normalize element type name to standard ArchiMate format.
        Handles common variations like camelCase, snake_case, etc.
        """
        if not element_type:
            return ""

        # Remove spaces and convert to consistent format
        normalized = element_type.replace(" ", "").replace("_", "").replace("-", "")

        # Try to find exact match first
        for elem in ALL_ELEMENTS:
            if elem.lower() == normalized.lower():
                return elem

        return element_type  # Return original if no match found

    def _find_similar_element_types(self, element_type: str, max_results: int = 3) -> List[str]:
        """Find similar element type names for suggestions"""
        if not element_type:
            return []

        element_lower = element_type.lower()
        similar = []

        for elem in ALL_ELEMENTS:
            elem_lower = elem.lower()
            # Check if the input is a substring or has common prefix
            if element_lower in elem_lower or elem_lower in element_lower:
                similar.append(elem)
            elif element_lower[:3] == elem_lower[:3]:  # Common prefix
                similar.append(elem)

        return similar[:max_results]

    def _get_sample_valid_targets(self, source_type: str, max_results: int = 5) -> List[str]:
        """Get a sample of valid target types for a source type"""
        targets = get_all_valid_targets_for_source(source_type)
        return list(targets.keys())[:max_results]

    def _get_cross_layer_guidance(
        self, source_layer: str, target_layer: str, relationship_type: str
    ) -> Optional[str]:
        """Provide guidance for cross-layer relationships"""
        # Define typical cross-layer relationship patterns
        layer_order = {
            "motivation": 0,
            "strategy": 1,
            "business": 2,
            "application": 3,
            "technology": 4,
            "physical": 5,
            "implementation": 6,
        }

        source_idx = layer_order.get(source_layer, -1)
        target_idx = layer_order.get(target_layer, -1)

        if source_idx == -1 or target_idx == -1:
            return None

        # Serving typically flows upward (lower serves higher)
        if relationship_type == "serving":
            if source_idx < target_idx:
                return "Note: 'serving' typically flows from lower layers to higher layers"

        # Realization typically flows upward (lower realizes higher)
        if relationship_type == "realization":
            if source_idx < target_idx:
                return f"Note: 'realization' indicates {source_layer} layer realizes {target_layer} layer concepts"

        # Large layer jumps
        if abs(source_idx - target_idx) > 2:
            return f"Consider intermediate elements for clearer traceability across {abs(source_idx - target_idx)} layers"

        return None


# Singleton instance
_validator: Optional[RelationshipValidator] = None


def get_relationship_validator() -> RelationshipValidator:
    """
    Get the singleton RelationshipValidator instance.

    Returns:
        RelationshipValidator instance
    """
    global _validator
    if _validator is None:
        _validator = RelationshipValidator()
    return _validator
