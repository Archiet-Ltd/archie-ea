"""Solution Context Assembler (BPP-008).

Gathers all relevant existing data for a solution before calling the
LLM suggestion generator.  This is pure database querying — no AI.

The assembled context powers the pre-population pipeline by giving the
LLM a rich view of how the solution connects to the enterprise
ArchiMate graph.
"""

import logging
from typing import Any, Dict, List

from sqlalchemy import or_

from app import db

logger = logging.getLogger(__name__)


class SolutionContextAssembler:
    """Assemble enterprise context for a solution's pre-population pipeline."""

    def assemble(self, solution_id: int) -> Dict[str, Any]:
        """Gather all relevant data for the given solution.

        Queries:
        1. The solution itself (business_domain, solution_type, description).
        2. Linked applications via ``solution_applications`` junction.
        3. Linked capabilities via ``solution_capability_mappings``.
        4. Linked ArchiMate elements via ``solution_archimate_elements``
           AND ``ApplicationComponent.archimate_element_id`` FKs.
        5. First-degree ArchiMate relationships from gathered elements.
        6. Second-degree elements (one hop further).
        7. Similar solutions in the same business domain (max 10).

        Args:
            solution_id: The solution to assemble context for.

        Returns:
            Dict with keys: solution, linked_apps, linked_capabilities,
            linked_elements, first_degree_relationships,
            second_degree_elements, similar_solutions.
        """
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
        from app.models.solution_models import Solution

        # 1. Load the solution
        solution = Solution.query.get(solution_id)
        if solution is None:
            logger.warning("Solution %d not found", solution_id)
            return self._empty_context()

        solution_data = {
            "id": solution.id,
            "name": getattr(solution, "name", ""),
            "business_domain": getattr(solution, "business_domain", ""),
            "solution_type": getattr(solution, "solution_type", ""),
            "description": getattr(solution, "description", ""),
        }

        # 2. Linked applications
        linked_apps = self._get_linked_apps(solution)

        # 3. Linked capabilities
        linked_capabilities = self._get_linked_capabilities(solution_id)

        # 4. Linked ArchiMate elements (junction + app FK)
        linked_element_ids = self._get_linked_element_ids(solution_id, linked_apps)
        linked_elements = []
        if linked_element_ids:
            linked_elements = ArchiMateElement.query.filter(
                ArchiMateElement.id.in_(linked_element_ids)
            ).all()

        # 5. First-degree relationships
        first_degree_rels = []
        second_degree_element_ids = set()
        if linked_element_ids:
            first_degree_rels = ArchiMateRelationship.query.filter(
                or_(
                    ArchiMateRelationship.source_id.in_(linked_element_ids),
                    ArchiMateRelationship.target_id.in_(linked_element_ids),
                )
            ).all()

            # Collect the "other side" element IDs for second-degree lookup
            for rel in first_degree_rels:
                if rel.source_id not in linked_element_ids:
                    second_degree_element_ids.add(rel.source_id)
                if rel.target_id not in linked_element_ids:
                    second_degree_element_ids.add(rel.target_id)

        # 6. Second-degree elements
        second_degree_elements = []
        if second_degree_element_ids:
            second_degree_elements = ArchiMateElement.query.filter(
                ArchiMateElement.id.in_(second_degree_element_ids)
            ).all()

        # 7. Similar solutions (same business_domain, different ID)
        similar_solutions = []
        if solution_data["business_domain"]:
            similar_solutions = (
                Solution.query
                .filter(
                    Solution.business_domain == solution_data["business_domain"],
                    Solution.id != solution_id,
                )
                .limit(10)
                .all()
            )

        return {
            "solution": solution_data,
            "linked_apps": self._serialize_apps(linked_apps),
            "linked_capabilities": linked_capabilities,
            "linked_elements": self._serialize_elements(linked_elements),
            "first_degree_relationships": self._serialize_relationships(first_degree_rels),
            "second_degree_elements": self._serialize_elements(second_degree_elements),
            "similar_solutions": [
                {"id": s.id, "name": getattr(s, "name", "")}
                for s in similar_solutions
            ],
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_linked_apps(self, solution) -> List:
        """Get ApplicationComponent records linked to a solution."""
        from app.models.application_portfolio import ApplicationComponent

        # Try solution.applications backref first
        if hasattr(solution, "applications") and solution.applications:
            return list(solution.applications)

        # Fallback: query junction table directly
        rows = db.session.execute(db.text(  # tenant-filtered: scoped via solution_id FK
            "SELECT application_component_id FROM solution_applications "
            "WHERE solution_id = :sid"
        ), {"sid": solution.id}).fetchall()
        app_ids = [r[0] for r in rows]
        if not app_ids:
            return []
        return ApplicationComponent.query.filter(
            ApplicationComponent.id.in_(app_ids)
        ).all()

    def _get_linked_capabilities(self, solution_id: int) -> List[Dict]:
        """Get capability dicts linked via solution_capability_mappings."""
        from app.utils.tenant_sql import org_scope
        _org_clause, _org_params = org_scope(prefix="bcm.")
        rows = db.session.execute(db.text(
            # solution_capability_mappings names its FK capability_id; it has
            # no business_capability_id column (that spelling belongs to
            # application_capability_mapping), so this raised UndefinedColumn.
            "SELECT bcm.capability_id "
            "FROM solution_capability_mappings bcm "
            "WHERE bcm.solution_id = :sid" + _org_clause
        ), {"sid": solution_id, **_org_params}).fetchall()
        cap_ids = [r[0] for r in rows]
        if not cap_ids:
            return []

        from app.models.business_capabilities import BusinessCapability
        caps = BusinessCapability.query.filter(
            BusinessCapability.id.in_(cap_ids)
        ).all()
        return [
            {"id": c.id, "name": c.name, "level": getattr(c, "level", None)}
            for c in caps
        ]

    def _get_linked_element_ids(self, solution_id: int, linked_apps: List) -> set:
        """Collect ArchiMate element IDs from junction + app FKs."""
        element_ids = set()

        # From solution_archimate_elements junction
        from app.utils.tenant_sql import org_scope
        _org_clause, _org_params = org_scope()
        rows = db.session.execute(db.text(
            "SELECT element_id FROM solution_archimate_elements "
            "WHERE solution_id = :sid" + _org_clause
        ), {"sid": solution_id, **_org_params}).fetchall()
        for r in rows:
            if r[0]:
                element_ids.add(r[0])

        # From ApplicationComponent.archimate_element_id FK
        for app in linked_apps:
            ae_id = getattr(app, "archimate_element_id", None)
            if ae_id:
                element_ids.add(ae_id)

        return element_ids

    def _serialize_apps(self, apps: List) -> List[Dict]:
        return [
            {
                "id": a.id,
                "name": a.name,
                "lifecycle_status": getattr(a, "lifecycle_status", None),
                "archimate_element_id": getattr(a, "archimate_element_id", None),
            }
            for a in apps
        ]

    def _serialize_elements(self, elements: List) -> List[Dict]:
        return [
            {
                "id": e.id,
                "name": e.name,
                "type": e.type,
                "layer": e.layer,
            }
            for e in elements
        ]

    def _serialize_relationships(self, rels: List) -> List[Dict]:
        return [
            {
                "id": r.id,
                "source_id": r.source_id,
                "target_id": r.target_id,
                "type": r.type,
            }
            for r in rels
        ]

    def _empty_context(self) -> Dict[str, Any]:
        return {
            "solution": None,
            "linked_apps": [],
            "linked_capabilities": [],
            "linked_elements": [],
            "first_degree_relationships": [],
            "second_degree_elements": [],
            "similar_solutions": [],
        }
