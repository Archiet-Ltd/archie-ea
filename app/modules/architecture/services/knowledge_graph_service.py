"""
Knowledge Graph Integration Service

Integrates document analysis with knowledge graphs for enhanced context.
Features:
- Entity linking to knowledge graph
- Context enrichment from graph
- Relationship inference from graph patterns
- Semantic similarity via graph embeddings
"""

import logging
from typing import Dict, List, Optional


from app.models.application_layer import ApplicationComponent
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.vendor.vendor_organization import VendorOrganization

logger = logging.getLogger(__name__)


class KnowledgeGraphService:
    """
    Service for knowledge graph integration and context enrichment.
    """

    def __init__(self):
        """Initialize knowledge graph service."""
        self.graph_cache = {}
        self.semantic_cache = {}

    def enrich_with_knowledge_graph(
        self, elements: List[Dict], relationships: List[Dict], context_depth: int = 2
    ) -> Dict:
        """
        Enrich extracted elements with knowledge graph context.

        Args:
            elements: Extracted elements
            relationships: Extracted relationships
            context_depth: How many hops to traverse in graph

        Returns:
            Enriched data with additional context
        """
        enriched_elements = []
        enriched_relationships = list(relationships)
        additional_context = []

        # Build knowledge graph from database
        kg = self._build_knowledge_graph()

        for element in elements:
            enriched = element.copy()

            # Find matching entity in knowledge graph
            kg_entity = self._find_kg_entity(element, kg)

            if kg_entity:
                # Enrich with graph context
                enriched["kg_context"] = {
                    "entity_id": kg_entity["id"],
                    "entity_type": kg_entity["type"],
                    "related_entities": kg_entity.get("related", []),
                    "graph_confidence": kg_entity.get("confidence", 0.5),
                }

                # Get related entities from graph
                related = self._get_related_entities(kg_entity["id"], kg, depth=context_depth)
                enriched["related_entities"] = related

                # Suggest relationships based on graph
                suggested_rels = self._suggest_relationships_from_graph(element, kg_entity, kg)
                enriched_relationships.extend(suggested_rels)

                # Add context notes
                if related:
                    context_note = f"Found {len(related)} related entities in knowledge graph"
                    additional_context.append(context_note)

            enriched_elements.append(enriched)

        return {
            "elements": enriched_elements,
            "relationships": enriched_relationships,
            "additional_context": additional_context,
            "kg_enrichment_applied": True,
        }

    def _build_knowledge_graph(self) -> Dict:
        """Build knowledge graph from database."""
        if "graph" in self.graph_cache:
            return self.graph_cache["graph"]

        graph = {"elements": {}, "relationships": {}, "applications": {}, "vendors": {}}

        try:
            # Load ArchiMate elements
            elements = ArchiMateElement.query.all()
            for elem in elements:
                graph["elements"][elem.id] = {
                    "id": elem.id,
                    "name": elem.name,
                    "type": elem.type,
                    "layer": elem.layer,
                    "related": [],
                }

            # Load relationships
            relationships = ArchiMateRelationship.query.all()
            for rel in relationships:
                # Use correct attribute names (source_id/target_id, not source_element_id)
                source_id = getattr(rel, "source_id", None) or getattr(
                    rel, "source_element_id", None
                )
                target_id = getattr(rel, "target_id", None) or getattr(
                    rel, "target_element_id", None
                )

                if not source_id or not target_id:
                    continue

                if source_id in graph["elements"]:
                    graph["elements"][source_id]["related"].append(
                        {
                            "id": target_id,
                            "type": rel.relationship_type,
                            "target_name": graph["elements"]
                            .get(target_id, {})
                            .get("name", "Unknown"),
                        }
                    )

                # Store relationship
                rel_key = f"{source_id}-{target_id}"
                graph["relationships"][rel_key] = {
                    "source": source_id,
                    "target": target_id,
                    "type": rel.relationship_type,
                }

            # Load applications
            applications = ApplicationComponent.query.all()
            for app in applications:
                graph["applications"][app.id] = {
                    "id": app.id,
                    "name": app.name,
                    "type": "ApplicationComponent",
                    "layer": "application",
                }

            # Load vendors
            vendors = VendorOrganization.query.all()
            for vendor in vendors:
                graph["vendors"][vendor.id] = {
                    "id": vendor.id,
                    "name": vendor.name,
                    "type": "BusinessActor",
                    "layer": "business",
                }

            self.graph_cache["graph"] = graph
            logger.info(
                f"Built knowledge graph with {len(graph['elements'])} elements, "
                f"{len(graph['relationships'])} relationships"
            )

        except Exception as e:
            logger.error(f"Error building knowledge graph: {e}")

        return graph

    def _find_kg_entity(self, element: Dict, kg: Dict) -> Optional[Dict]:
        """Find matching entity in knowledge graph."""
        element_name = element.get("name", "").lower()
        element_type = element.get("type", "")

        # Search in elements
        for elem_id, kg_elem in kg["elements"].items():
            if kg_elem["name"].lower() == element_name:
                if element_type and kg_elem["type"] == element_type:
                    return {**kg_elem, "confidence": 0.9}
                else:
                    return {**kg_elem, "confidence": 0.7}

        # Search in applications
        for app_id, app in kg["applications"].items():
            if app["name"].lower() == element_name:
                return {**app, "confidence": 0.8}

        # Search in vendors
        for vendor_id, vendor in kg["vendors"].items():
            if vendor["name"].lower() == element_name:
                return {**vendor, "confidence": 0.8}

        return None

    def _get_related_entities(self, entity_id: int, kg: Dict, depth: int = 2) -> List[Dict]:
        """Get related entities from knowledge graph."""
        related = []
        visited = set()
        queue = [(entity_id, 0)]  # (entity_id, current_depth)

        while queue:
            current_id, current_depth = queue.pop(0)

            if current_id in visited or current_depth > depth:
                continue

            visited.add(current_id)

            # Get entity from graph
            entity = None
            if current_id in kg["elements"]:
                entity = kg["elements"][current_id]
            elif current_id in kg["applications"]:
                entity = kg["applications"][current_id]
            elif current_id in kg["vendors"]:
                entity = kg["vendors"][current_id]

            if entity:
                related.append(
                    {
                        "id": entity["id"],
                        "name": entity["name"],
                        "type": entity["type"],
                        "layer": entity.get("layer", ""),
                        "depth": current_depth,
                    }
                )

                # Add related entities to queue
                if current_id in kg["elements"]:
                    for rel in kg["elements"][current_id].get("related", []):
                        if rel["id"] not in visited:
                            queue.append((rel["id"], current_depth + 1))

        return related

    def _suggest_relationships_from_graph(
        self, element: Dict, kg_entity: Dict, kg: Dict
    ) -> List[Dict]:
        """Suggest relationships based on knowledge graph patterns."""
        suggestions = []

        entity_id = kg_entity["id"]
        if entity_id in kg["elements"]:
            related = kg["elements"][entity_id].get("related", [])
            for rel_info in related:
                # Find target element in extracted elements
                target_name = rel_info.get("target_name", "")
                suggestions.append(
                    {
                        "source": element.get("name", ""),
                        "target": target_name,
                        "type": rel_info.get("type", "Association"),
                        "confidence": 0.7,
                        "source_method": "knowledge_graph",
                        "description": "Suggested from knowledge graph pattern",
                    }
                )

        return suggestions

    def get_semantic_context(self, element: Dict, max_context: int = 5) -> List[Dict]:
        """Get semantic context for an element from knowledge graph."""
        kg = self._build_knowledge_graph()
        kg_entity = self._find_kg_entity(element, kg)

        if not kg_entity:
            return []

        related = self._get_related_entities(kg_entity["id"], kg, depth=2)
        return related[:max_context]

    def get_elements(self, element_type=None, domain=None, limit=100, offset=0):
        """ArchiMate elements as KG nodes, with optional type/layer filters."""
        from app.models.archimate_core import ArchiMateElement
        q = ArchiMateElement.query
        if element_type:
            q = q.filter(ArchiMateElement.type == element_type)
        if domain and hasattr(ArchiMateElement, "layer"):
            q = q.filter(ArchiMateElement.layer == domain)
        rows = q.offset(int(offset or 0)).limit(int(limit or 100)).all()
        return [
            {"id": e.id, "name": e.name, "type": e.type,
             "layer": getattr(e, "layer", None), "description": getattr(e, "description", None)}
            for e in rows
        ]

    def search_elements(self, query, element_type=None, limit=50):
        """Elements whose name or description contains the text (case-insensitive)."""
        from app.models.archimate_core import ArchiMateElement
        text = str(query or "").strip()
        if not text:
            return []
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        q = ArchiMateElement.query.filter(
            ArchiMateElement.name.ilike(pattern, escape="\\")
            | ArchiMateElement.description.ilike(pattern, escape="\\")
        )
        if element_type:
            q = q.filter(ArchiMateElement.type == element_type)
        rows = q.order_by(ArchiMateElement.name).limit(int(limit or 50)).all()
        return [
            {"id": e.id, "name": e.name, "type": e.type,
             "layer": getattr(e, "layer", None), "description": getattr(e, "description", None)}
            for e in rows
        ]

    def get_relationships(self, relationship_type=None, source_id=None, target_id=None):
        """ArchiMate relationships as KG edges, with optional filters."""
        from app.models.archimate_core import ArchiMateRelationship
        q = ArchiMateRelationship.query
        if relationship_type:
            q = q.filter(ArchiMateRelationship.type == relationship_type)
        if source_id:
            q = q.filter(ArchiMateRelationship.source_id == source_id)
        if target_id:
            q = q.filter(ArchiMateRelationship.target_id == target_id)
        return [
            {"id": r.id, "type": r.type, "source_id": r.source_id, "target_id": r.target_id}
            for r in q.limit(1000).all()
        ]