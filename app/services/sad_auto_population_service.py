"""
SAD Auto-Population Service

Drafts SAD sections 03 (Application Architecture), 04 (Data Architecture),
and 06 (Integration Architecture) from live platform data so that Solution
Architects validate rather than transcribe.

SA-008
"""

import logging
from typing import Dict

logger = logging.getLogger(__name__)


class SADAutoPopulationService:
    """Auto-populate SAD Phase C sections from existing platform data."""

    def draft_phase_c_sections(self, solution_instance_id: int) -> dict:
        """
        Draft SAD sections 03, 04, and 06 from live ORM data.

        Args:
            solution_instance_id: ID of the solution instance being documented.

        Returns:
            {
                "sad_03_application_architecture": {...},
                "sad_04_data_architecture": {...},
                "sad_06_integration_architecture": {...},
            }
            Each sub-dict is populated from real DB data; empty dicts are
            returned for individual sections when a DB error occurs.
        """
        return {
            "sad_03_application_architecture": self._draft_sad_03(),
            "sad_04_data_architecture": self._draft_sad_04(),
            "sad_06_integration_architecture": self._draft_sad_06(),
        }

    # ------------------------------------------------------------------
    # Section helpers
    # ------------------------------------------------------------------

    def _draft_sad_03(self) -> dict:
        """Application Architecture — arch_pattern breakdown + rationalisation summary."""
        try:
            from app.services.application_kind import applications_query

            components = applications_query().all()

            arch_pattern_breakdown: Dict[str, int] = {}
            rationalization_summary = []

            for comp in components:
                pattern = (comp.arch_pattern or "unknown").strip()
                arch_pattern_breakdown[pattern] = arch_pattern_breakdown.get(pattern, 0) + 1

                if comp.target_disposition and comp.target_disposition != "tbd":
                    rationalization_summary.append(
                        {
                            "app_id": comp.id,
                            "app_name": comp.name,
                            "disposition": comp.target_disposition,
                        }
                    )

            return {
                "arch_pattern_breakdown": arch_pattern_breakdown,
                "rationalization_summary": rationalization_summary,
                "total_apps": len(components),
            }
        except Exception:
            logger.exception("SAD-03 draft failed")
            return {}

    def _draft_sad_04(self) -> dict:
        """Data Architecture — DataObject catalogue grouped by classification."""
        try:
            from app.models.application_layer import DataObject

            objects = DataObject.query.all()

            by_classification: Dict[str, int] = {}
            objects_list = []

            for obj in objects:
                classification = (obj.data_classification or "unclassified").strip().lower()
                by_classification[classification] = by_classification.get(classification, 0) + 1
                objects_list.append(
                    {
                        "id": obj.id,
                        "name": obj.name,
                        "classification": classification,
                    }
                )

            return {
                "data_object_count": len(objects),
                "by_classification": by_classification,
                "objects": objects_list,
            }
        except Exception:
            logger.exception("SAD-04 draft failed")
            return {}

    def _draft_sad_06(self) -> dict:
        """Integration Architecture — ArchiMate relationship counts by type."""
        try:
            from app.models.models import ArchiMateRelationship

            INTEGRATION_TYPES = {"serving", "flow", "association"}

            relationships = ArchiMateRelationship.query.filter(
                ArchiMateRelationship.type.in_(INTEGRATION_TYPES)
            ).all()

            by_type: Dict[str, int] = {}
            for rel in relationships:
                rel_type = (rel.type or "unknown").strip()
                by_type[rel_type] = by_type.get(rel_type, 0) + 1

            dominant_pattern = ""
            if by_type:
                dominant_pattern = max(by_type, key=lambda t: by_type[t])

            return {
                "integration_count": len(relationships),
                "by_type": by_type,
                "dominant_pattern": dominant_pattern,
            }
        except Exception:
            logger.exception("SAD-06 draft failed")
            return {}
