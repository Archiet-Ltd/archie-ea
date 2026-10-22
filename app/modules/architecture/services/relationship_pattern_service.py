"""
Relationship Pattern Learning Service

Learns common relationship patterns from existing data and uses them
to suggest new relationships.
"""

import logging
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional


from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.archimate_element_types import StandardTypeTable

logger = logging.getLogger(__name__)


class RelationshipPatternService:
    """
    Service for learning and applying relationship patterns.

    Features:
    - Pattern extraction from existing relationships
    - Pattern frequency analysis
    - Pattern-based relationship suggestions
    - ArchiMate metamodel validation
    """

    def __init__(self):
        self.pattern_cache = None
        self.metamodel_rules = self._load_metamodel_rules()

    def learn_patterns(self, min_frequency: int = 3) -> Dict[str, Any]:
        """
        Learn relationship patterns from existing data.

        Args:
            min_frequency: Minimum occurrences to consider a pattern

        Returns:
            Dictionary of learned patterns
        """
        patterns = {
            "type_pairs": defaultdict(Counter),
            "layer_transitions": defaultdict(Counter),
            "common_relationships": Counter(),
            "element_relationship_map": defaultdict(set),
        }

        # Load all relationships
        relationships = ArchiMateRelationship.query.all()

        for rel in relationships:
            source = ArchiMateElement.query.get(rel.source_element_id)
            target = ArchiMateElement.query.get(rel.target_element_id)

            if not source or not target:
                continue

            # Pattern 1: Type pair patterns
            type_pair = (source.type, target.type)
            patterns["type_pairs"][type_pair][rel.relationship_type] += 1

            # Pattern 2: Layer transitions
            if source.layer and target.layer:
                layer_transition = (source.layer, target.layer)
                patterns["layer_transitions"][layer_transition][rel.relationship_type] += 1

            # Pattern 3: Common relationships
            patterns["common_relationships"][rel.relationship_type] += 1

            # Pattern 4: Element type to relationship type mapping
            patterns["element_relationship_map"][source.type].add(rel.relationship_type)

        # Filter by minimum frequency
        filtered_patterns = {
            "type_pairs": {
                pair: dict(rel_types)
                for pair, rel_types in patterns["type_pairs"].items()
                if sum(rel_types.values()) >= min_frequency
            },
            "layer_transitions": {
                transition: dict(rel_types)
                for transition, rel_types in patterns["layer_transitions"].items()
                if sum(rel_types.values()) >= min_frequency
            },
            "common_relationships": dict(patterns["common_relationships"]),
            "element_relationship_map": {
                elem_type: list(rel_types)
                for elem_type, rel_types in patterns["element_relationship_map"].items()
            },
        }

        self.pattern_cache = filtered_patterns
        return filtered_patterns

    def suggest_relationships_from_patterns(
        self,
        source_type: str,
        target_type: str,
        source_layer: Optional[str] = None,
        target_layer: Optional[str] = None,
    ) -> List[Dict]:
        """
        Suggest relationships based on learned patterns.

        Args:
            source_type: Source element type
            target_type: Target element type
            source_layer: Source element layer
            target_layer: Target element layer

        Returns:
            List of suggested relationship types with confidence
        """
        if not self.pattern_cache:
            self.learn_patterns()

        suggestions = []

        # Check type pair patterns
        type_pair = (source_type, target_type)
        if type_pair in self.pattern_cache["type_pairs"]:
            rel_types = self.pattern_cache["type_pairs"][type_pair]
            total = sum(rel_types.values())

            for rel_type, count in rel_types.items():
                confidence = count / total
                suggestions.append(
                    {
                        "relationship_type": rel_type,
                        "confidence": confidence,
                        "evidence": f"Pattern: {count} occurrences of {rel_type} between {source_type} and {target_type}",
                        "pattern_source": "type_pair",
                    }
                )

        # Check layer transition patterns
        if source_layer and target_layer:
            layer_transition = (source_layer, target_layer)
            if layer_transition in self.pattern_cache["layer_transitions"]:
                rel_types = self.pattern_cache["layer_transitions"][layer_transition]
                total = sum(rel_types.values())

                for rel_type, count in rel_types.items():
                    confidence = count / total
                    suggestions.append(
                        {
                            "relationship_type": rel_type,
                            "confidence": confidence,
                            "evidence": f"Pattern: {count} occurrences of {rel_type} from {source_layer} to {target_layer}",
                            "pattern_source": "layer_transition",
                        }
                    )

        # Validate against ArchiMate metamodel
        validated_suggestions = []
        for suggestion in suggestions:
            if self._validate_relationship(
                source_type, target_type, suggestion["relationship_type"]
            ):
                validated_suggestions.append(suggestion)

        # Sort by confidence
        return sorted(validated_suggestions, key=lambda x: x["confidence"], reverse=True)

    def _validate_relationship(
        self, source_type: str, target_type: str, relationship_type: str
    ) -> bool:
        """
        Validate relationship against ArchiMate 3.2 metamodel rules.

        Returns:
            True if relationship is valid, False otherwise
        """
        # Check against metamodel rules
        if source_type in self.metamodel_rules:
            allowed_targets = self.metamodel_rules[source_type].get(relationship_type, [])
            if allowed_targets and target_type not in allowed_targets:
                # Check if 'any' is allowed
                if "any" not in allowed_targets:
                    return False

        return True

    def _load_metamodel_rules(self) -> Dict[str, Dict[str, List[str]]]:
        """
        Load ArchiMate 3.2 metamodel rules.

        Defines which relationship types are valid between which element types.
        """
        return StandardTypeTable({
            "ApplicationComponent": {
                "Composition": [
                    "ApplicationInterface",
                    "ApplicationService",
                    "ApplicationFunction",
                    "ApplicationComponent",
                ],
                "Serving": ["BusinessProcess", "BusinessService", "ApplicationComponent"],
                "Realization": ["BusinessService", "BusinessProcess"],
                "Access": ["DataObject"],
                "Flow": ["ApplicationComponent"],
            },
            "ApplicationInterface": {
                "Serving": ["ApplicationService", "BusinessService"],
                "Composition": ["ApplicationComponent"],
                "Flow": ["ApplicationInterface"],
            },
            "ApplicationService": {
                "Composition": ["ApplicationComponent"],
                "Serving": ["BusinessProcess", "BusinessService"],
                "Realization": ["BusinessService"],
                "Flow": ["ApplicationService"],
            },
            "Goal": {
                "Realization": ["Requirement", "Outcome", "Goal"],
                "Influence": ["Goal", "Requirement"],
                "Association": ["Stakeholder", "Driver"],
            },
            "Requirement": {
                "Realization": ["BusinessCapability", "ApplicationComponent"],
                "Association": ["BusinessCapability", "Goal"],
                "Influence": ["Requirement"],
            },
            "BusinessCapability": {
                "Realization": ["ApplicationComponent", "BusinessProcess"],
                "Composition": ["BusinessCapability"],
                "Association": ["Requirement"],
            },
            "BusinessProcess": {
                "Serving": ["BusinessService"],
                "Realization": ["BusinessCapability", "Outcome"],
                "Flow": ["BusinessProcess"],
                "Composition": ["BusinessFunction"],
            },
        })

    def get_most_common_patterns(
        self, element_type: Optional[str] = None, limit: int = 10
    ) -> List[Dict]:
        """
        Get most common relationship patterns.

        Args:
            element_type: Optional filter by element type
            limit: Maximum number of patterns to return

        Returns:
            List of common patterns
        """
        if not self.pattern_cache:
            self.learn_patterns()

        patterns = []

        # Get type pair patterns
        for type_pair, rel_types in self.pattern_cache["type_pairs"].items():
            if element_type and element_type not in type_pair:
                continue

            total = sum(rel_types.values())
            for rel_type, count in rel_types.items():
                patterns.append(
                    {
                        "source_type": type_pair[0],
                        "target_type": type_pair[1],
                        "relationship_type": rel_type,
                        "frequency": count,
                        "percentage": round((count / total) * 100, 1),
                        "pattern_type": "type_pair",
                    }
                )

        # Sort by frequency
        patterns.sort(key=lambda x: x["frequency"], reverse=True)

        return patterns[:limit]
