"""ArchiMate 3.2 Relationship Validity Service.

Thin adapter over the one relationship validity authority,
``app/config/archimate_relationship_matrix.py``. Every verdict this service
returns comes from that matrix's ``is_valid_relationship``,
``get_valid_relationships`` and ``get_element_layer`` -- this module carries
no rule tables of its own. It exists so the picker route, the create route's
hardening check, the OEF export path and the composer validator keep the
call shape they already have (``ArchimateValidityService().is_valid(...)``,
``.get_valid_relationships(...)``, ``.get_practitioner_warnings(...)``).

Returns results with a tier indicator:
  - 'standard': directly valid per ArchiMate 3.2 specification
  - 'fallback': Association is always technically valid but discouraged

Reference: The Open Group ArchiMate 3.2 Specification, Appendix B.
"""

from app.config.archimate_relationship_matrix import (
    ALL_ELEMENTS,
    LAYER_NAME_TO_KEY as _LAYER_NAME_MAP,
    RELATIONSHIP_TYPE_DEFINITIONS,
    get_element_aspect,
    get_element_layer,
    get_valid_relationships as _matrix_get_valid_relationships,
    is_valid_relationship as _matrix_is_valid_relationship,
    normalize_element_type,
)

# -- Adapters over the matrix's own type/layer/aspect vocabulary ------------
#
# The matrix names layers and aspects in Title Case ("Strategy",
# "Implementation & Migration", "Active Structure"). This service's callers
# (get_practitioner_warnings' internal logic, and archimate_routes.py's
# composer validator, which imports ``_layer`` directly) expect the
# lowercase single-word vocabulary this service has always used
# ("strategy", "implementation", "active"). ``_LAYER_NAME_MAP`` above is the
# matrix module's one definition of the layer-name translation (also used by
# app.models.archimate_core); the aspect map below has no other caller, so
# it stays local.

_ASPECT_NAME_MAP = {
    "Active Structure": "active",
    "Behavior": "behaviour",
    "Passive Structure": "passive",
}


def _normalize_type(element_type):
    """Convert snake_case DB type to PascalCase for matrix lookup.

    Kept under this name for the one existing caller
    (``archimate_routes.py``'s composer validator) that imports it directly;
    the implementation now lives in the matrix module as
    ``normalize_element_type``.
    """
    return normalize_element_type(element_type)


def _layer(element_type):
    return _LAYER_NAME_MAP.get(get_element_layer(normalize_element_type(element_type)), "unknown")


def _aspect(element_type):
    return _ASPECT_NAME_MAP.get(get_element_aspect(normalize_element_type(element_type)), "unknown")


class ArchimateValidityService:
    """Relationship validity checking, delegated to the ArchiMate 3.2 matrix."""

    def get_valid_relationships(self, source_type, target_type):
        """Return list of valid relationship types for a source-target pair.

        Returns list of dicts:
            [{"type": "serving", "tier": "standard", "description": "..."}]

        association carries tier "fallback" (the picker's existing hint
        that it is always available but not the most specific choice);
        every other permitted type carries tier "standard".
        """
        source_type = normalize_element_type(source_type)
        target_type = normalize_element_type(target_type)

        results = []
        for rel_type in _matrix_get_valid_relationships(source_type, target_type):
            definition = RELATIONSHIP_TYPE_DEFINITIONS.get(rel_type)
            results.append({
                "type": rel_type,
                "tier": "fallback" if rel_type == "association" else "standard",
                "description": definition.description if definition else "",
            })
        return results

    def is_valid(self, source_type, target_type, relationship_type):
        """Check if a specific relationship type is valid between two element types."""
        src = normalize_element_type(source_type)
        tgt = normalize_element_type(target_type)
        return _matrix_is_valid_relationship(src, tgt, relationship_type)

    def get_practitioner_warnings(self, source_type, target_type, relationship_type):
        """Return warnings for common practitioner mistakes.

        Advisory only -- these never change a verdict, only add guidance
        alongside one the matrix already gave.
        """
        source_type = normalize_element_type(source_type)
        target_type = normalize_element_type(target_type)
        warnings = []
        src_aspect = _aspect(source_type)
        tgt_aspect = _aspect(target_type)
        src_layer = _layer(source_type)
        tgt_layer = _layer(target_type)

        # Mistake 1: Assignment from passive to active (should be Access)
        if relationship_type == "assignment" and src_aspect == "passive":
            warnings.append(
                f"Assignment from a passive element ({source_type}) is unusual. "
                f"Did you mean Access (read/write)?"
            )
        if relationship_type == "assignment" and tgt_aspect == "passive":
            warnings.append(
                f"Assignment to a passive element ({target_type}) is invalid. "
                f"Use Access instead."
            )

        # Mistake 2: Cross-layer Composition
        if relationship_type == "composition" and src_layer != tgt_layer:
            if {src_layer, tgt_layer} == {"physical", "technology"}:
                warnings.append(
                    f"Composition cannot cross layers ({src_layer} -> {tgt_layer}). "
                    f"Physical and Technology are peer layers but Composition still "
                    f"requires same-layer ownership. Use Serving or Realization instead."
                )
            else:
                warnings.append(
                    f"Composition cannot cross layers ({src_layer} -> {tgt_layer}). "
                    f"Composition means ownership within the same layer."
                )

        # Mistake 3: Backwards Serving
        if relationship_type == "serving":
            if src_aspect == "active" and tgt_aspect == "behaviour":
                pass  # This is actually fine via assignment chain
            # The common mistake is drawing serving from consumer to provider
            # We can't detect direction intent, but we can remind
            if src_layer == "business" and tgt_layer == "application":
                warnings.append(
                    "Serving goes from provider to consumer. If the application "
                    "serves the business process, the arrow should go FROM "
                    "application TO business."
                )

        # Mistake 4: Cross-layer Triggering without intermediary
        if relationship_type == "triggering" and src_layer != tgt_layer:
            if {src_layer, tgt_layer} == {"physical", "technology"}:
                warnings.append(
                    f"Triggering between Physical and Technology layers "
                    f"({src_layer} -> {tgt_layer}) may be valid for peer "
                    f"interactions, but consider using Serving instead."
                )
            else:
                warnings.append(
                    f"Triggering across layers ({src_layer} -> {tgt_layer}) is "
                    f"unusual. Consider adding an intermediary element at the "
                    f"layer boundary (e.g., a Service)."
                )

        return warnings

    # -- Unified entry points ------------------------------------------------

    def validate(self, source_type, target_type, relationship_type=None):
        """Unified validation entry point.

        Returns dict with:
            valid: bool — whether the relationship/element combination is valid
            relationships: list — valid relationship types (if no specific type given)
            warnings: list — practitioner warnings
            tier: str — 'standard' or 'fallback'
        """
        result = {
            "valid": False,
            "relationships": [],
            "warnings": [],
            "tier": "unknown",
        }

        if relationship_type:
            result["valid"] = self.is_valid(source_type, target_type, relationship_type)
            result["warnings"] = self.get_practitioner_warnings(
                source_type, target_type, relationship_type
            )
            # Determine tier
            valid_rels = self.get_valid_relationships(source_type, target_type)
            for r in valid_rels:
                if r["type"] == relationship_type:
                    result["tier"] = r["tier"]
                    break
        else:
            result["relationships"] = self.get_valid_relationships(source_type, target_type)
            result["valid"] = len(result["relationships"]) > 0

        return result

    def validate_element_type(self, element_type):
        """Check if an element type is a known ArchiMate 3.2 element."""
        return normalize_element_type(element_type) in ALL_ELEMENTS
