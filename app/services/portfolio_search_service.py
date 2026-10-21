"""Portfolio search service using text-based similarity.

Provides cross-entity search across the enterprise portfolio — applications,
capabilities, ArchiMate elements, vendors, and solutions — using SQL ILIKE
pattern matching with relevance scoring.

This service is the text-based fallback layer.  When pgvector embeddings
are available, the ``_vector_search`` hook can be overridden in a subclass
to blend vector similarity results with the text results produced here.
"""

import importlib
import logging
import re

from app import db
from app.utils.tenant import current_organization_id

logger = logging.getLogger(__name__)


class PortfolioSearchService:
    """Portfolio search using text similarity (ILIKE + scoring).

    Searches across five entity types:
      - application  (ApplicationComponent)
      - capability   (BusinessCapability)
      - archimate_element (ArchiMateElement)
      - vendor       (VendorOrganization)
      - solution     (Solution)

    Extension point: override ``_vector_search`` to add pgvector results.
    """

    SEARCHABLE_TYPES = [
        "application",
        "capability",
        "archimate_element",
        "vendor",
        "solution",
    ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(self, query, entity_type=None, limit=20, threshold=0.3):
        """Search across portfolio entities by text similarity.

        Parameters
        ----------
        query : str
            Free-text search query.
        entity_type : str | None
            Restrict to a single entity type (one of ``SEARCHABLE_TYPES``).
            ``None`` searches all types.
        limit : int
            Maximum results to return (across all types).
        threshold : float
            Minimum relevance score (0–1).

        Returns
        -------
        list[dict]
            Sorted by descending relevance score.
        """
        if not query or not query.strip():
            return []

        query = query.strip()
        results = []

        type_dispatchers = {
            "application": self._search_applications,
            "capability": self._search_capabilities,
            "archimate_element": self._search_archimate,
            "vendor": self._search_vendors,
            "solution": self._search_solutions,
        }

        types_to_search = (
            [entity_type]
            if entity_type in type_dispatchers
            else list(type_dispatchers.keys())
        )

        for etype in types_to_search:
            try:
                hits = type_dispatchers[etype](query, limit)
                results.extend(hits)
            except Exception:
                logger.exception("Error searching %s", etype)

        # Blend any vector results (extension point — empty by default)
        try:
            vector_hits = self._vector_search(query, entity_type, limit)
            results.extend(vector_hits)
        except Exception:
            logger.debug("Vector search unavailable, using text results only")

        # Filter by threshold, sort by score descending, truncate
        results = [r for r in results if r.get("score", 0) >= threshold]
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def find_similar(self, entity_type, entity_id, limit=10):
        """Find entities similar to a given entity.

        Resolves the entity's name/description, then searches for
        similar items of the **same** type (excluding the source entity).

        Parameters
        ----------
        entity_type : str
            One of ``SEARCHABLE_TYPES``.
        entity_id : int
            Primary key of the source entity.
        limit : int
            Maximum results to return.

        Returns
        -------
        list[dict]
        """
        if entity_type not in self.SEARCHABLE_TYPES:
            return []

        text_query = self._get_entity_text(entity_type, entity_id)
        if not text_query:
            return []

        results = self.search(
            text_query, entity_type=entity_type, limit=limit + 1, threshold=0.1
        )
        # Exclude the source entity itself
        results = [
            r
            for r in results
            if not (r.get("id") == entity_id and r.get("type") == entity_type)
        ]
        return results[:limit]

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_score(query, name, description=None):
        """Compute a 0–1 relevance score for a (name, description) pair.

        Scoring heuristic (deterministic, no randomness):
          - Exact name match (case-insensitive): 1.0
          - Name starts with query: 0.9
          - Query is a substring of name: 0.8
          - Word overlap in name: 0.5–0.7
          - Query found in description: 0.5
          - Word overlap in description: 0.3–0.5
          - No match at all: 0.0
        """
        if not name:
            return 0.0

        q = query.lower().strip()
        n = name.lower().strip()
        d = (description or "").lower().strip()

        if q == n:
            return 1.0
        if n.startswith(q):
            return 0.9
        if q in n:
            return 0.8

        q_words = set(re.split(r"\W+", q)) - {""}
        n_words = set(re.split(r"\W+", n)) - {""}

        if q_words and n_words:
            overlap = len(q_words & n_words) / len(q_words)
            if overlap > 0:
                return round(0.5 + 0.2 * overlap, 3)

        if d and q in d:
            return 0.5

        if d and q_words:
            d_words = set(re.split(r"\W+", d)) - {""}
            if d_words:
                overlap = len(q_words & d_words) / len(q_words)
                if overlap > 0:
                    return round(0.3 + 0.2 * overlap, 3)

        return 0.0

    # ------------------------------------------------------------------
    # Per-type search implementations
    # ------------------------------------------------------------------

    def _search_applications(self, query, limit):
        """Search ApplicationComponent by name / description."""
        from app.models.application_portfolio import ApplicationComponent

        pattern = f"%{query}%"
        rows = (
            ApplicationComponent.query.filter(
                db.or_(
                    ApplicationComponent.name.ilike(pattern),
                    ApplicationComponent.description.ilike(pattern),
                )
            )
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "type": "application",
                "name": r.name,
                "description": (r.description or "")[:200],
                "score": round(self._compute_score(query, r.name, r.description), 3),
                "metadata": {
                    "application_type": getattr(r, "application_type", None),
                    "business_domain": getattr(r, "business_domain", None),
                },
            }
            for r in rows
        ]

    def _search_capabilities(self, query, limit):
        """Search BusinessCapability by name / description."""
        from app.models.business_capabilities import BusinessCapability

        pattern = f"%{query}%"
        rows = (
            BusinessCapability.query.filter(
                db.or_(
                    BusinessCapability.name.ilike(pattern),
                    BusinessCapability.description.ilike(pattern),
                )
            )
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "type": "capability",
                "name": r.name,
                "description": (getattr(r, "description", None) or "")[:200],
                "score": round(
                    self._compute_score(query, r.name, getattr(r, "description", None)),
                    3,
                ),
                "metadata": {
                    "level": getattr(r, "level", None),
                    "category": getattr(r, "category", None),
                },
            }
            for r in rows
        ]

    def _search_archimate(self, query, limit):
        """Search ArchiMateElement by name / description."""
        from app.models.archimate_core import ArchiMateElement

        pattern = f"%{query}%"
        rows = (
            ArchiMateElement.query.filter(
                db.or_(
                    ArchiMateElement.name.ilike(pattern),
                    ArchiMateElement.description.ilike(pattern),
                )
            )
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "type": "archimate_element",
                "name": r.name,
                "description": (getattr(r, "description", None) or "")[:200],
                "score": round(
                    self._compute_score(query, r.name, getattr(r, "description", None)),
                    3,
                ),
                "metadata": {
                    "element_type": getattr(r, "type", None),
                    "layer": getattr(r, "layer", None),
                },
            }
            for r in rows
        ]

    def _search_vendors(self, query, limit):
        """Search VendorOrganization by name / display_name."""
        from app.models.vendor.vendor_organization import VendorOrganization

        pattern = f"%{query}%"
        rows = (
            VendorOrganization.query.filter(
                db.or_(
                    VendorOrganization.name.ilike(pattern),
                    VendorOrganization.display_name.ilike(pattern),
                )
            )
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "type": "vendor",
                "name": r.name,
                "description": (getattr(r, "display_name", None) or "")[:200],
                "score": round(
                    self._compute_score(query, r.name, getattr(r, "display_name", None)),
                    3,
                ),
                "metadata": {
                    "vendor_type": getattr(r, "vendor_type", None),
                    "headquarters": getattr(r, "headquarters_location", None),
                },
            }
            for r in rows
        ]

    def _search_solutions(self, query, limit):
        """Search Solution by name / description."""
        from app.models.solution_models import Solution

        pattern = f"%{query}%"
        rows = (
            Solution.query.filter(
                db.or_(
                    Solution.name.ilike(pattern),
                    Solution.description.ilike(pattern),
                )
            )
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "type": "solution",
                "name": r.name,
                "description": (r.description or "")[:200],
                "score": round(self._compute_score(query, r.name, r.description), 3),
                "metadata": {
                    "solution_type": getattr(r, "solution_type", None),
                    "business_domain": getattr(r, "business_domain", None),
                },
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Entity text resolver (for find_similar)
    # ------------------------------------------------------------------

    _MODEL_MAP = {
        "application": ("app.models.application_portfolio", "ApplicationComponent"),
        "capability": ("app.models.business_capabilities", "BusinessCapability"),
        "archimate_element": ("app.models.archimate_core", "ArchiMateElement"),
        "vendor": ("app.models.vendor.vendor_organization", "VendorOrganization"),
        "solution": ("app.models.solution_models", "Solution"),
    }

    def _get_entity_text(self, entity_type, entity_id):
        """Retrieve searchable text for an entity to use as similarity query."""
        entry = self._MODEL_MAP.get(entity_type)
        if not entry:
            return None
        module_path, class_name = entry
        try:
            mod = importlib.import_module(module_path)
            model_cls = getattr(mod, class_name)
            stmt = db.select(model_cls).where(model_cls.id == entity_id)
            if hasattr(model_cls, "organization_id"):
                # Tenant-owned types carry an explicit predicate: session.get
                # returns an identity-map hit without emitting SQL, so the
                # do_orm_execute tenant filter never runs for it.
                org_id = current_organization_id()
                if org_id is None:
                    return None
                stmt = stmt.where(model_cls.organization_id == org_id)
            entity = db.session.execute(stmt).scalars().first()
            if not entity:
                return None
            name = entity.name or ""
            desc = entity.description or ""
            return f"{name} {desc}".strip() or None
        except Exception:
            logger.exception(
                "Failed to resolve entity text for %s/%s", entity_type, entity_id
            )
            return None

    # ------------------------------------------------------------------
    # Extension point for pgvector
    # ------------------------------------------------------------------

    def _vector_search(self, query, entity_type=None, limit=20):
        """Hook for pgvector-based semantic search.

        Override in a subclass to blend vector similarity results with
        text-based results.  The base implementation returns an empty
        list (text search only).
        """
        return []
