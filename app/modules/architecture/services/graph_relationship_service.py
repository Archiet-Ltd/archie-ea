"""
Graph-Based Relationship Inference Service

Uses graph traversal to discover relationships by analyzing existing relationship patterns
and inferring new connections based on graph structure.
"""

import logging
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional

from sqlalchemy import or_

from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.archimate_element_types import StandardTypeTable, is_application_kind

logger = logging.getLogger(__name__)


class GraphRelationshipService:
    """
    Service for graph-based relationship discovery and inference.

    Features:
    - Graph traversal to find related elements
    - Relationship pattern analysis
    - Multi-hop relationship discovery
    - Relationship inference based on graph structure
    """

    def __init__(self):
        self.max_traversal_depth = 3
        self.relationship_cache = {}

    def discover_relationships_via_graph(
        self,
        extracted_elements: List[Dict],
        existing_elements: Optional[List[ArchiMateElement]] = None,
    ) -> List[Dict]:
        """
        Discover relationships by traversing the relationship graph.

        Args:
            extracted_elements: List of extracted elements from document
            existing_elements: Optional list of existing elements to match against

        Returns:
            List of discovered relationship suggestions
        """
        relationships = []

        if not existing_elements:
            # Load existing elements that might be related
            existing_elements = self._load_relevant_elements(extracted_elements)

        # Build relationship graph
        graph = self._build_relationship_graph(existing_elements)

        for extracted_elem in extracted_elements:
            elem_name = extracted_elem.get("name", "").lower()
            elem_type = extracted_elem.get("type", "")

            # Find matching existing element
            matched_element = self._find_matching_element(elem_name, elem_type, existing_elements)

            if matched_element:
                # Traverse graph from matched element
                discovered = self._traverse_and_discover(matched_element, graph, extracted_elements)
                relationships.extend(discovered)

            # Also check for indirect relationships via graph patterns
            pattern_relationships = self._discover_via_patterns(
                extracted_elem, graph, existing_elements
            )
            relationships.extend(pattern_relationships)

        # Deduplicate and rank relationships
        return self._deduplicate_and_rank(relationships)

    def _build_relationship_graph(self, elements: List[ArchiMateElement]) -> Dict[int, List[Dict]]:
        """
        Build adjacency list representation of relationship graph.

        Returns:
            Dictionary mapping element_id -> list of connected elements with relationship info
        """
        graph = defaultdict(list)

        if not elements:
            return graph

        element_ids = [e.id for e in elements]

        # Load all relationships involving these elements
        relationships = ArchiMateRelationship.query.filter(
            or_(
                ArchiMateRelationship.source_element_id.in_(element_ids),
                ArchiMateRelationship.target_element_id.in_(element_ids),
            )
        ).all()

        for rel in relationships:
            source_id = rel.source_element_id
            target_id = rel.target_element_id

            graph[source_id].append(
                {
                    "target_id": target_id,
                    "relationship_type": rel.relationship_type,
                    "description": rel.description,
                }
            )

            # Also add reverse for undirected traversal (for some relationship types)
            if rel.relationship_type in ["Association", "Flow", "Serving"]:
                graph[target_id].append(
                    {
                        "target_id": source_id,
                        "relationship_type": rel.relationship_type,
                        "description": rel.description,
                        "reverse": True,
                    }
                )

        return graph

    def _traverse_and_discover(
        self,
        start_element: ArchiMateElement,
        graph: Dict[int, List[Dict]],
        extracted_elements: List[Dict],
        max_depth: int = 2,
    ) -> List[Dict]:
        """
        Traverse graph from start element and discover relationships to extracted elements.

        Uses BFS to find paths between elements.
        """
        discovered = []
        visited = set()
        queue = deque([(start_element.id, 0, [])])  # (element_id, depth, path)

        # Build map of extracted element names for quick lookup
        extracted_names = {e.get("name", "").lower(): e for e in extracted_elements}

        while queue:
            current_id, depth, path = queue.popleft()

            if depth >= max_depth or current_id in visited:
                continue

            visited.add(current_id)

            # Check neighbors
            for neighbor in graph.get(current_id, []):
                neighbor_id = neighbor["target_id"]

                # Get neighbor element
                neighbor_elem = ArchiMateElement.query.get(neighbor_id)
                if not neighbor_elem:
                    continue

                # Check if neighbor matches any extracted element
                neighbor_name_lower = neighbor_elem.name.lower() if neighbor_elem.name else ""
                if neighbor_name_lower in extracted_names:
                    extracted_elem = extracted_names[neighbor_name_lower]

                    # Found a path! Create relationship suggestion
                    relationship_type = self._infer_relationship_type(
                        path + [neighbor], start_element, neighbor_elem
                    )

                    discovered.append(
                        {
                            "source_name": start_element.name,
                            "source_id": start_element.id,
                            "target_name": extracted_elem.get("name"),
                            "target_type": extracted_elem.get("type"),
                            "relationship_type": relationship_type,
                            "confidence": self._calculate_path_confidence(depth, len(path)),
                            "evidence": f"Discovered via graph traversal (depth {depth})",
                            "path_length": depth + 1,
                            "discovery_method": "graph_traversal",
                        }
                    )

                # Continue traversal
                if depth < max_depth - 1:
                    queue.append((neighbor_id, depth + 1, path + [neighbor]))

        return discovered

    def _discover_via_patterns(
        self,
        extracted_elem: Dict,
        graph: Dict[int, List[Dict]],
        existing_elements: List[ArchiMateElement],
    ) -> List[Dict]:
        """
        Discover relationships based on common relationship patterns.

        Patterns analyzed:
        - If A → B and B → C, and C matches extracted element, suggest A → C
        - If multiple elements connect to same target, suggest similar relationship
        - If element type matches pattern (e.g., all ApplicationComponents connect to same interface)
        """
        relationships = []
        extracted_elem.get("name", "").lower()
        elem_type = extracted_elem.get("type", "")

        # Pattern 1: Find elements that connect to similar targets
        for existing_elem in existing_elements:
            if existing_elem.element_type != elem_type:
                continue

            # Get relationships from this element
            outgoing = graph.get(existing_elem.id, [])

            # Analyze relationship patterns
            for rel in outgoing:
                target_elem = ArchiMateElement.query.get(rel["target_id"])
                if not target_elem:
                    continue

                # If this element type typically has this relationship type,
                # suggest it for extracted element too
                if self._is_common_pattern(existing_elem.element_type, rel["relationship_type"]):
                    relationships.append(
                        {
                            "source_name": extracted_elem.get("name"),
                            "target_name": target_elem.name,
                            "target_id": target_elem.id,
                            "relationship_type": rel["relationship_type"],
                            "confidence": 0.7,
                            "evidence": f"Pattern match: {existing_elem.name} has {rel['relationship_type']} to {target_elem.name}",
                            "discovery_method": "pattern_matching",
                        }
                    )

        return relationships

    def _is_common_pattern(self, element_type: str, relationship_type: str) -> bool:
        """
        Check if relationship type is common for element type.

        Based on ArchiMate 3.2 patterns.
        """
        common_patterns = StandardTypeTable({
            "ApplicationComponent": ["Composition", "Serving", "Realization"],
            "ApplicationInterface": ["Serving", "Composition"],
            "ApplicationService": ["Serving", "Composition", "Realization"],
            "BusinessProcess": ["Serving", "Realization", "Flow"],
            "BusinessCapability": ["Realization", "Composition"],
            "Goal": ["Realization", "Influence"],
            "Requirement": ["Realization", "Association"],
        })

        return relationship_type in common_patterns.get(element_type, [])

    def _infer_relationship_type(
        self, path: List[Dict], source: ArchiMateElement, target: ArchiMateElement
    ) -> str:
        """
        Infer relationship type based on path and element types.

        Uses ArchiMate 3.2 metamodel rules.
        """
        if not path:
            return "Association"

        # If direct path exists, use the relationship type
        if len(path) == 1:
            return path[0].get("relationship_type", "Association")

        # For multi-hop paths, infer based on element types
        source_type = source.type
        target_type = target.type

        # Common patterns
        if is_application_kind(source_type) and target_type == "ApplicationInterface":
            return "Composition"
        elif source_type == "ApplicationInterface" and target_type == "ApplicationService":
            return "Serving"
        elif source_type == "BusinessProcess" and target_type == "ApplicationService":
            return "Serving"
        elif source_type == "Goal" and target_type == "Requirement":
            return "Realization"
        elif source_type == "Requirement" and target_type == "BusinessCapability":
            return "Association"
        else:
            return "Association"

    def _calculate_path_confidence(self, depth: int, path_length: int) -> float:
        """
        Calculate confidence based on path length.

        Shorter paths = higher confidence.
        """
        if depth == 0:
            return 0.95
        elif depth == 1:
            return 0.85
        elif depth == 2:
            return 0.70
        else:
            return max(0.5, 1.0 - (depth * 0.15))

    def _find_matching_element(
        self, name: str, element_type: str, existing_elements: List[ArchiMateElement]
    ) -> Optional[ArchiMateElement]:
        """Find existing element that matches extracted element."""
        name_lower = name.lower()

        for elem in existing_elements:
            if elem.name and elem.name.lower() == name_lower:
                if not element_type or elem.type == element_type:
                    return elem

        # Fuzzy match
        from difflib import SequenceMatcher

        best_match = None
        best_score = 0.0

        for elem in existing_elements:
            if not elem.name:
                continue

            score = SequenceMatcher(None, name_lower, elem.name.lower()).ratio()
            if score > best_score and score >= 0.85:
                best_score = score
                best_match = elem

        return best_match

    def _load_relevant_elements(self, extracted_elements: List[Dict]) -> List[ArchiMateElement]:
        """Load existing elements that might be related to extracted elements."""
        # Get element types from extracted elements
        element_types = list(set(e.get("type", "") for e in extracted_elements if e.get("type")))

        # Load elements of same types
        query = ArchiMateElement.query
        if element_types:
            query = query.filter(ArchiMateElement.type.in_(element_types))

        return query.limit(200).all()

    def _deduplicate_and_rank(self, relationships: List[Dict]) -> List[Dict]:
        """Remove duplicates and rank by confidence."""
        seen = set()
        unique = []

        for rel in relationships:
            key = (
                rel.get("source_name", ""),
                rel.get("target_name", ""),
                rel.get("relationship_type", ""),
            )

            if key not in seen:
                seen.add(key)
                unique.append(rel)

        # Sort by confidence descending
        return sorted(unique, key=lambda x: x.get("confidence", 0), reverse=True)

    def expand_context_with_graph(self, element_ids: List[int], depth: int = 1) -> Dict[str, Any]:
        """
        Expand context by loading related elements via graph traversal.

        Args:
            element_ids: Starting element IDs
            depth: How many hops to traverse

        Returns:
            Expanded context with related elements
        """
        if not element_ids:
            return {"elements": [], "relationships": []}

        visited = set(element_ids)
        related_elements = []
        related_relationships = []

        # Load starting elements
        current_level = ArchiMateElement.query.filter(ArchiMateElement.id.in_(element_ids)).all()

        related_elements.extend([e.id for e in current_level])

        # Traverse graph
        for _ in range(depth):
            next_level_ids = []

            # Get relationships from current level
            relationships = ArchiMateRelationship.query.filter(
                ArchiMateRelationship.source_element_id.in_(related_elements)
            ).all()

            for rel in relationships:
                if rel.target_element_id not in visited:
                    visited.add(rel.target_element_id)
                    next_level_ids.append(rel.target_element_id)
                    related_relationships.append(
                        {
                            "source_id": rel.source_element_id,
                            "target_id": rel.target_element_id,
                            "type": rel.relationship_type,
                        }
                    )

            if not next_level_ids:
                break

            related_elements.extend(next_level_ids)

        # Load all related elements
        all_elements = ArchiMateElement.query.filter(
            ArchiMateElement.id.in_(related_elements)
        ).all()

        return {
            "elements": [
                {
                    "id": e.id,
                    "name": e.name,
                    "type": e.type,
                    "layer": e.layer,
                    "description": e.description,
                }
                for e in all_elements
            ],
            "relationships": related_relationships,
            "total_elements": len(all_elements),
            "traversal_depth": depth,
        }
