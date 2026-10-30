"""Service for Phase D Technology Architecture operations.

Manages technology-layer ArchiMate element linkage for solutions using the
existing SolutionArchiMateElement junction table, filtered by layer='Technology'.
"""

import logging

from app import db
from app.models.solution_archimate_element import SolutionArchiMateElement

logger = logging.getLogger(__name__)


class SolutionTechnologyService:
    """Manages technology element linkage for solutions (Phase D)."""

    TECHNOLOGY_ELEMENT_TYPES = [
        "Node",
        "Device",
        "SystemSoftware",
        "TechnologyCollaboration",
        "TechnologyInterface",
        "Path",
        "CommunicationNetwork",
        "TechnologyFunction",
        "TechnologyProcess",
        "TechnologyInteraction",
        "TechnologyEvent",
        "TechnologyService",
        "Artifact",
    ]

    TECHNOLOGY_LAYERS = ["technology", "infrastructure", "physical"]

    def get_technology_elements(self, solution_id):
        """Get all technology-layer ArchiMate elements linked to a solution.

        Returns a list of dicts with element details, filtered to only
        include elements whose layer is in TECHNOLOGY_LAYERS or whose
        type is in TECHNOLOGY_ELEMENT_TYPES.
        """
        try:
            rows = SolutionArchiMateElement.query.filter_by(
                solution_id=solution_id
            ).all()
            if not rows:
                return []

            from app.models.archimate_core import ArchiMateElement

            element_ids = [r.element_id for r in rows]
            elements = ArchiMateElement.query.filter(
                ArchiMateElement.id.in_(element_ids)
            ).all()
            element_map = {e.id: e for e in elements}

            result = []
            for row in rows:
                elem = element_map.get(row.element_id)
                if elem is None:
                    continue
                elem_layer = (getattr(elem, "layer", "") or "").lower()
                elem_type = getattr(elem, "type", "") or ""
                if (
                    elem_layer in self.TECHNOLOGY_LAYERS
                    or elem_type in self.TECHNOLOGY_ELEMENT_TYPES
                ):
                    result.append(
                        {
                            "id": elem.id,
                            "junction_id": row.id,
                            "name": elem.name,
                            "type": elem_type,
                            "layer": getattr(elem, "layer", None),
                            "description": getattr(elem, "description", None),
                            "plateau": getattr(elem, "togaf_plateau", None),
                            "building_block_type": getattr(
                                elem, "building_block_type", None
                            ),
                            "element_role": row.element_role,
                            "created_at": (
                                row.created_at.isoformat() if row.created_at else None
                            ),
                        }
                    )
            return result
        except Exception:
            logger.exception(
                "get_technology_elements(%s) failed", solution_id
            )
            return []

    def link_technology_element(
        self, solution_id, element_id, element_role="primary"
    ):
        """Link a technology ArchiMate element to a solution.

        Validates that the element exists and is in the technology layer.
        Returns a dict with the result or raises ValueError on bad input.
        """
        from app.models.archimate_core import ArchiMateElement

        elem = ArchiMateElement.query.get(element_id)
        if elem is None:
            raise ValueError(f"ArchiMate element {element_id} not found")

        elem_layer = (getattr(elem, "layer", "") or "").lower()
        elem_type = getattr(elem, "type", "") or ""
        if (
            elem_layer not in self.TECHNOLOGY_LAYERS
            and elem_type not in self.TECHNOLOGY_ELEMENT_TYPES
        ):
            raise ValueError(
                f"Element {element_id} is not a technology-layer element "
                f"(layer={elem_layer}, type={elem_type})"
            )

        existing = SolutionArchiMateElement.query.filter_by(
            solution_id=solution_id, element_id=element_id
        ).first()
        if existing:
            existing.element_role = element_role
            db.session.commit()
            return {
                "id": elem.id,
                "junction_id": existing.id,
                "name": elem.name,
                "type": elem_type,
                "layer": getattr(elem, "layer", None),
                "element_role": existing.element_role,
                "already_linked": True,
            }

        row = SolutionArchiMateElement(
            solution_id=solution_id,
            element_id=element_id,
            element_role=element_role,
        )
        db.session.add(row)
        db.session.commit()

        return {
            "id": elem.id,
            "junction_id": row.id,
            "name": elem.name,
            "type": elem_type,
            "layer": getattr(elem, "layer", None),
            "element_role": row.element_role,
            "already_linked": False,
        }

    def unlink_technology_element(self, solution_id, element_id):
        """Remove a technology element link from a solution.

        Returns True if the link was found and removed, False otherwise.
        """
        try:
            row = SolutionArchiMateElement.query.filter_by(
                solution_id=solution_id, element_id=element_id
            ).first()
            if row:
                db.session.delete(row)
                db.session.commit()
                return True
            return False
        except Exception:
            db.session.rollback()
            logger.exception(
                "unlink_technology_element(%s, %s) failed",
                solution_id,
                element_id,
            )
            return False

    def get_technology_summary(self, solution_id):
        """Return a summary of technology elements by type for a solution."""
        elements = self.get_technology_elements(solution_id)
        type_counts = {}
        for elem in elements:
            elem_type = elem.get("type", "Unknown")
            type_counts[elem_type] = type_counts.get(elem_type, 0) + 1
        return {
            "total": len(elements),
            "by_type": type_counts,
        }
