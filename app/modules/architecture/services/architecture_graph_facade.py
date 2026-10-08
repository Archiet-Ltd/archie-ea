"""Graph-style access over relational ArchiMate storage.

Provides node/edge CRUD, idempotent upsert, typed traversal, and
type normalization (PascalCase <-> snake_case).
"""
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from app import db
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.architecture_inference_relationship import ArchitectureInferenceRelationship

logger = logging.getLogger(__name__)


@dataclass
class GraphNode:
    id: int
    element_type: str
    name: str
    layer: str
    model: Any  # underlying SQLAlchemy instance


@dataclass
class GraphRelationship:
    id: int
    source: GraphNode
    target: GraphNode
    rel_type: str
    source_tag: str
    confidence: float
    inference_pass: int


# Full bidirectional mapping: snake_case <-> PascalCase
TYPE_NORMALIZE = {
    "stakeholder": "Stakeholder", "driver": "Driver", "assessment": "Assessment",
    "goal": "Goal", "outcome": "Outcome", "principle": "Principle",
    "requirement": "Requirement", "constraint": "Constraint",
    "course_of_action": "CourseOfAction", "value_stream": "ValueStreamStage",
    "resource": "Resource",
    "business_process": "BusinessProcess", "business_function": "BusinessFunction",
    "business_service": "BusinessService", "business_event": "BusinessEvent",
    "business_role": "BusinessRole", "business_object": "BusinessObject",
    "business_actor": "BusinessActor",
    "application_service": "ApplicationService", "application_component": "ApplicationComponent",
    "application_function": "ApplicationFunction", "data_object": "DataObject",
    "technology_service": "TechnologyService", "technology_function": "TechnologyFunction",
    "technology_component": "TechnologyComponent", "node": "Node",
    "artifact": "Artifact", "communication_network": "CommunicationNetwork",
    "gap": "Gap", "work_package": "WorkPackage", "deliverable": "Deliverable",
    "plateau": "Plateau", "capability": "Capability",
}
TYPE_DENORMALIZE = {v: k for k, v in TYPE_NORMALIZE.items()}

# Layer lookup for each PascalCase type
_TYPE_LAYER = {
    "Stakeholder": "motivation", "Driver": "motivation", "Assessment": "motivation",
    "Goal": "motivation", "Outcome": "motivation", "Principle": "motivation",
    "Requirement": "motivation", "Constraint": "motivation",
    "CourseOfAction": "strategy", "Capability": "strategy",
    "ValueStreamStage": "strategy", "Resource": "strategy",
    "BusinessProcess": "business", "BusinessFunction": "business",
    "BusinessService": "business", "BusinessEvent": "business",
    "BusinessRole": "business", "BusinessObject": "business",
    "BusinessActor": "business",
    "ApplicationService": "application", "ApplicationComponent": "application",
    "ApplicationFunction": "application", "DataObject": "application",
    "TechnologyService": "technology", "TechnologyFunction": "technology",
    "TechnologyComponent": "technology", "Node": "technology",
    "Artifact": "technology", "CommunicationNetwork": "technology",
    "Gap": "implementation", "WorkPackage": "implementation",
    "Deliverable": "implementation", "Plateau": "implementation",
}


class ArchitectureGraphFacade:
    """Graph-style access over relational ArchiMate storage."""

    def __init__(self, architecture_id: int, organization_id: int = None):
        self.architecture_id = architecture_id
        if organization_id is not None:
            self.organization_id = organization_id
        else:
            # Derive from the architecture's linked solution
            try:
                from app.models.archimate_core import ArchitectureModel
                from app.models.solution_models import Solution
                arch = ArchitectureModel.query.get(architecture_id)
                if arch and arch.solution_id:
                    sol = Solution.query.get(arch.solution_id)
                    self.organization_id = sol.organization_id if sol else None
                else:
                    self.organization_id = None
            except Exception:
                self.organization_id = None

    def _arch_filter(self, query, model_class):
        """Filter by architecture_id, including NULL values."""
        return query.filter(
            db.or_(
                model_class.architecture_id == self.architecture_id,
                model_class.architecture_id.is_(None),
            )
        )

    # ---- Type normalization ----

    def _normalize_type(self, raw_type: str) -> str:
        """Accept either convention, always return PascalCase."""
        return TYPE_NORMALIZE.get(raw_type, raw_type)

    def _denormalize_type(self, engine_type: str) -> str:
        """Convert PascalCase back to snake_case for DB queries."""
        return TYPE_DENORMALIZE.get(engine_type, engine_type)

    # ---- Node API ----

    @staticmethod
    def _tokenize(name: str) -> set:
        """Extract meaningful tokens from an element name, stripping filler.

        Normalizes hyphens, strips basic plurals ('s'), and removes stop words
        so that "Real-time Claim" and "Realtime Claims" produce overlapping tokens.
        """
        # Remove common generated prefixes like "Goal for", "Requirement for"
        cleaned = re.sub(
            r'^(Goal|Driver|Requirement|Assessment|Outcome|Capability|'
            r'BusinessProcess|ApplicationService|ApplicationComponent|'
            r'TechnologyService|Node|WorkPackage|Deliverable|Plateau|'
            r'Stakeholder|Constraint|Principle)\s+(for|of|from)\s+',
            '', name, flags=re.IGNORECASE,
        )
        # Collapse hyphens so "real-time" → "realtime"
        cleaned = cleaned.replace('-', '')
        tokens = set(re.findall(r'[a-z]{3,}', cleaned.lower()))
        # Basic plural stemming: "claims" → "claim", "systems" → "system"
        stemmed = set()
        for t in tokens:
            if t.endswith('s') and len(t) > 4:
                stemmed.add(t[:-1])
            else:
                stemmed.add(t)
        # Remove very common stop words
        stemmed -= {'the', 'and', 'for', 'from', 'with', 'that', 'this', 'are', 'was'}
        return stemmed

    def _find_similar_element(self, element_type: str, name: str, threshold: float = 0.68) -> Optional[ArchiMateElement]:
        """Find an existing element of the same type with similar name.

        Uses Jaccard similarity on stemmed word tokens with SequenceMatcher
        as a tiebreaker. Requires BOTH Jaccard >= 0.5 AND combined >= threshold
        to avoid false positives in large catalogs.

        Returns best match above threshold, or None.
        """
        from difflib import SequenceMatcher

        target_tokens = self._tokenize(name)
        if len(target_tokens) < 2:
            return None  # Too few tokens for reliable matching

        name_lower = name.lower().strip()

        # Query ALL candidates of the same type (cross-architecture dedup)
        candidates = ArchiMateElement.query.filter(
            db.or_(
                ArchiMateElement.type == element_type,
                ArchiMateElement.type == self._denormalize_type(element_type),
            )
        ).all()

        best_match = None
        best_score = threshold

        for candidate in candidates:
            candidate_tokens = self._tokenize(candidate.name)
            if not candidate_tokens:
                continue
            # Jaccard on stemmed tokens — primary signal
            intersection = target_tokens & candidate_tokens
            union = target_tokens | candidate_tokens
            jaccard = len(intersection) / len(union) if union else 0

            # Gate: Jaccard must show meaningful overlap (>= 50%)
            if jaccard < 0.5:
                continue

            # SequenceMatcher as refinement for near-matches
            seq_score = SequenceMatcher(None, name_lower, candidate.name.lower().strip()).ratio()

            # Combined: average of both signals
            score = (jaccard + seq_score) / 2

            if score > best_score:
                best_score = score
                best_match = candidate

        if best_match:
            logger.info(
                "Smart match: '%s' → existing '%s' (score=%.2f)",
                name, best_match.name, best_score,
            )
        return best_match

    def get_or_create_node(self, element_type: str, key: dict, defaults: dict = None) -> GraphNode:
        """Idempotent: find by (architecture_id, type, name), else create."""
        element_type = self._normalize_type(element_type)
        name = key.get("name", "")
        defaults = defaults or {}

        # Try both PascalCase and snake_case for the type field
        existing = ArchiMateElement.query.filter_by(
            architecture_id=self.architecture_id,
            name=name,
        ).filter(
            db.or_(
                ArchiMateElement.type == element_type,
                ArchiMateElement.type == self._denormalize_type(element_type),
            )
        ).first()

        # Fallback: also check NULL architecture_id (production data has all NULLs)
        if not existing and self.architecture_id is not None:
            try:
                existing = ArchiMateElement.query.filter(
                    ArchiMateElement.architecture_id.is_(None),
                    ArchiMateElement.name == name,
                ).filter(
                    db.or_(
                        ArchiMateElement.type == element_type,
                        ArchiMateElement.type == self._denormalize_type(element_type),
                    )
                ).first()
                # Verify it's a real model instance, not a mock
                if existing and not hasattr(existing, '__tablename__'):
                    existing = None
            except Exception as e:
                logger.debug("NULL arch_id fallback skipped: %s", e)
                existing = None

        # Smart matching: fuzzy token-based deduplication
        if not existing:
            try:
                existing = self._find_similar_element(element_type, name)
            except Exception as e:
                logger.debug("Smart match skipped: %s", e)

        if existing:
            for k, v in defaults.items():
                if v is not None and hasattr(existing, k) and not getattr(existing, k):
                    setattr(existing, k, v)
            return self._wrap_element(existing)

        # Create new — use NULL architecture_id if self.architecture_id is 0
        # (production data has all NULLs; 0 violates FK constraint)
        layer = _TYPE_LAYER.get(element_type, "business")
        arch_id = self.architecture_id if self.architecture_id else None
        elem = ArchiMateElement(
            name=name,
            type=element_type,
            layer=layer,
            description=defaults.get("description"),
            architecture_id=arch_id,
            organization_id=getattr(self, "organization_id", None),
        )
        db.session.add(elem)
        db.session.flush()
        return self._wrap_element(elem)

    def get_node(self, node_id: int) -> Optional[GraphNode]:
        """Lookup by ID."""
        elem = ArchiMateElement.query.get(node_id)
        if elem and (elem.architecture_id == self.architecture_id or elem.architecture_id is None):
            return self._wrap_element(elem)
        return None

    def find_nodes(self, element_type: Optional[str], filters: dict) -> list:
        """Filtered query. element_type=None returns all. Includes NULL architecture_id."""
        # Query both exact arch_id and NULL (production has all NULLs)
        results = ArchiMateElement.query.filter_by(architecture_id=self.architecture_id)
        if element_type:
            element_type = self._normalize_type(element_type)
            results = results.filter(
                db.or_(
                    ArchiMateElement.type == element_type,
                    ArchiMateElement.type == self._denormalize_type(element_type),
                )
            )
        found = results.all()

        # Fallback: also include NULL architecture_id elements
        null_query = ArchiMateElement.query.filter(ArchiMateElement.architecture_id.is_(None))
        if element_type:
            null_query = null_query.filter(
                db.or_(
                    ArchiMateElement.type == element_type,
                    ArchiMateElement.type == self._denormalize_type(element_type),
                )
            )
        try:
            null_found = null_query.all()
            seen_ids = {e.id for e in found}
            for e in null_found:
                if e.id not in seen_ids:
                    found.append(e)
        except Exception as e:
            logger.debug("NULL arch_id fallback in find_nodes skipped: %s", e)

        return [self._wrap_element(e) for e in found]

    # ---- Relationship API ----

    def get_or_create_relationship(
        self, source_id: int, target_id: int, rel_type: str,
        metadata: dict = None,
    ) -> GraphRelationship:
        """Idempotent: no duplicate edges of same type between same nodes."""
        metadata = metadata or {}
        source_node = self.get_node(source_id)
        target_node = self.get_node(target_id)
        if not source_node or not target_node:
            raise ValueError("Source {} or target {} not found".format(source_id, target_id))

        source_type = source_node.element_type
        target_type = target_node.element_type

        existing = ArchitectureInferenceRelationship.query.filter_by(
            architecture_id=self.architecture_id,
            source_type=source_type,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            rel_type=rel_type,
        ).first()

        if existing:
            return self._wrap_relationship(existing, source_node, target_node)

        rel = ArchitectureInferenceRelationship(
            architecture_id=self.architecture_id,
            source_type=source_type,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            rel_type=rel_type,
            source_tag=metadata.get("source_tag", "rule"),
            confidence=metadata.get("confidence", 1.0),
            inference_pass=metadata.get("inference_pass", 1),
            rule_name=metadata.get("rule_name"),
            # Resolved at facade construction from architecture_id -> Solution,
            # not from request context, so this is correct whether or not the
            # call runs inside a request (see __init__ above).
            organization_id=self.organization_id,
        )
        db.session.add(rel)
        db.session.flush()
        return self._wrap_relationship(rel, source_node, target_node)

    def find_relationships(
        self,
        source_id: int = None,
        target_id: int = None,
        rel_type: str = None,
    ) -> list:
        """Filtered query on relationships."""
        query = ArchitectureInferenceRelationship.query.filter_by(
            architecture_id=self.architecture_id
        )
        if source_id is not None:
            query = query.filter_by(source_id=source_id)
        if target_id is not None:
            query = query.filter_by(target_id=target_id)
        if rel_type is not None:
            query = query.filter_by(rel_type=rel_type)

        results = []
        for rel in query.all():
            src = self.get_node(rel.source_id)
            tgt = self.get_node(rel.target_id)
            if src and tgt:
                results.append(self._wrap_relationship(rel, src, tgt))
        return results

    # ---- Traversal ----

    def get_neighbors(self, node_id: int, direction: str = "both", rel_type: str = None) -> list:
        """Get connected nodes. Reads from inference table + legacy ArchiMateRelationship."""
        neighbors = []
        seen_ids = set()

        if direction in ("out", "both"):
            # Inference relationships
            query = ArchitectureInferenceRelationship.query.filter_by(
                architecture_id=self.architecture_id, source_id=node_id
            )
            if rel_type:
                query = query.filter_by(rel_type=rel_type)
            for rel in query.all():
                if rel.target_id not in seen_ids:
                    n = self.get_node(rel.target_id)
                    if n:
                        neighbors.append(n)
                        seen_ids.add(rel.target_id)
            # Legacy relationships
            self._collect_legacy_neighbors(node_id, "source_id", "target_id", seen_ids, neighbors)

        if direction in ("in", "both"):
            query = ArchitectureInferenceRelationship.query.filter_by(
                architecture_id=self.architecture_id, target_id=node_id
            )
            if rel_type:
                query = query.filter_by(rel_type=rel_type)
            for rel in query.all():
                if rel.source_id not in seen_ids:
                    n = self.get_node(rel.source_id)
                    if n:
                        neighbors.append(n)
                        seen_ids.add(rel.source_id)
            self._collect_legacy_neighbors(node_id, "target_id", "source_id", seen_ids, neighbors)

        return neighbors

    def _collect_legacy_neighbors(self, node_id, filter_field, other_field, seen_ids, neighbors):
        """Safely query ArchiMateRelationship (legacy table) for neighbors.
        Skips silently if table is unavailable or in mock context.
        """
        try:
            filter_col = getattr(ArchiMateRelationship, filter_field)
            query = ArchiMateRelationship.query.filter(filter_col == node_id)
            for rel in query.all():
                # Verify this is a real ORM result, not a mock
                if not hasattr(rel, '__tablename__') and not hasattr(rel, 'source_id'):
                    break
                other_id = getattr(rel, other_field, None)
                if other_id and other_id not in seen_ids:
                    n = self.get_node(other_id)
                    if n:
                        neighbors.append(n)
                        seen_ids.add(other_id)
        except Exception as e:
            logger.debug("Legacy relationship query skipped: %s", e)

    # ---- Helpers ----

    def _wrap_element(self, elem: ArchiMateElement) -> GraphNode:
        element_type = self._normalize_type(elem.type or "Unknown")
        layer = _TYPE_LAYER.get(element_type, elem.layer or "business")
        return GraphNode(
            id=elem.id,
            element_type=element_type,
            name=elem.name or "",
            layer=layer,
            model=elem,
        )

    def _wrap_relationship(
        self, rel: ArchitectureInferenceRelationship,
        source: GraphNode, target: GraphNode,
    ) -> GraphRelationship:
        return GraphRelationship(
            id=rel.id,
            source=source,
            target=target,
            rel_type=rel.rel_type,
            source_tag=rel.source_tag or "rule",
            confidence=rel.confidence or 1.0,
            inference_pass=rel.inference_pass or 1,
        )
