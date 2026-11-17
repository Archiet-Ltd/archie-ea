"""
SolutionArchiMateService — SA-001
Manages the many-to-many binding between Solutions and ArchiMate elements.
All methods are read-safe (return [] not raise) for missing IDs.
"""

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class SolutionArchiMateService:
    """Bind/unbind solutions to ArchiMate elements and query the linkage."""

    # ------------------------------------------------------------------ #
    # Write                                                                #
    # ------------------------------------------------------------------ #

    def bind(self, solution_id: int, element_id: int, role: str = "primary") -> bool:
        """
        Link an ArchiMate element to a solution.
        Idempotent: if the binding already exists, update the role and return True.
        Returns True on success, False if solution or element not found.
        """
        from app import db
        from app.models.solution_archimate_element import SolutionArchiMateElement

        try:
            existing = SolutionArchiMateElement.query.filter_by(
                solution_id=solution_id, element_id=element_id
            ).first()
            if existing:
                existing.element_role = role
            else:
                row = SolutionArchiMateElement(
                    solution_id=solution_id,
                    element_id=element_id,
                    element_role=role,
                )
                db.session.add(row)
            db.session.commit()
            return True
        except Exception:
            db.session.rollback()
            logger.exception("bind(%s, %s) failed", solution_id, element_id)
            return False

    def unbind(self, solution_id: int, element_id: int) -> bool:
        """
        Remove the link between a solution and an ArchiMate element.
        No-op if the binding does not exist. Returns True on success.
        """
        from app import db
        from app.models.solution_archimate_element import SolutionArchiMateElement

        try:
            row = SolutionArchiMateElement.query.filter_by(
                solution_id=solution_id, element_id=element_id
            ).first()
            if row:
                db.session.delete(row)
                db.session.commit()
            return True
        except Exception:
            db.session.rollback()
            logger.exception("unbind(%s, %s) failed", solution_id, element_id)
            return False

    # ------------------------------------------------------------------ #
    # Read                                                                 #
    # ------------------------------------------------------------------ #

    def get_elements_for_solution(self, solution_id: int) -> list[dict]:
        """
        Return all ArchiMate elements linked to a solution as a list of dicts.
        Each dict includes id, name, type, layer, plateau, building_block_type, element_role.
        """
        from app.models.solution_archimate_element import SolutionArchiMateElement

        try:
            rows = SolutionArchiMateElement.query.filter_by(
                solution_id=solution_id
            ).all()
            if not rows:
                return []

            from app.models.archimate_core import ArchiMateElement

            element_map = {
                e.id: e
                for e in ArchiMateElement.query.filter(
                    ArchiMateElement.id.in_([r.element_id for r in rows])
                ).all()
            }

            result = []
            for row in rows:
                elem = element_map.get(row.element_id)
                if elem is None:
                    continue
                result.append(
                    {
                        "id": elem.id,
                        "name": elem.name,
                        "type": getattr(elem, "type", None),
                        "layer": getattr(elem, "layer", None),
                        "plateau": getattr(elem, "togaf_plateau", None),
                        "building_block_type": getattr(
                            elem, "building_block_type", None
                        ),
                        "element_role": row.element_role,
                    }
                )
            return result
        except Exception:
            logger.exception("get_elements_for_solution(%s) failed", solution_id)
            return []

    def get_solutions_for_element(self, element_id: int) -> list[dict]:
        """
        Return all solutions linked to an ArchiMate element.
        Each dict includes solution_id, solution_name, element_role.
        """
        from app.models.solution_archimate_element import SolutionArchiMateElement

        try:
            rows = SolutionArchiMateElement.query.filter_by(
                element_id=element_id
            ).all()
            if not rows:
                return []

            from app.models.solution_models import Solution

            sol_map = {
                s.id: s
                for s in Solution.query.filter(
                    Solution.id.in_([r.solution_id for r in rows])
                ).all()
            }

            result = []
            for row in rows:
                sol = sol_map.get(row.solution_id)
                result.append(
                    {
                        "solution_id": row.solution_id,
                        "solution_name": sol.name if sol else None,
                        "element_role": row.element_role,
                    }
                )
            return result
        except Exception:
            logger.exception("get_solutions_for_element(%s) failed", element_id)
            return []

    def get_layer_summary_for_solution(self, solution_id: int) -> dict:
        """
        Return element count grouped by ArchiMate layer for a solution.
        E.g. {'business': 3, 'application': 2, 'technology': 1}
        """
        elements = self.get_elements_for_solution(solution_id)
        summary: dict[str, int] = defaultdict(int)
        for elem in elements:
            layer = elem.get("layer") or "unknown"
            summary[layer] += 1
        return dict(summary)
