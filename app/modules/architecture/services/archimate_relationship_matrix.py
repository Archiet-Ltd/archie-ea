"""
ArchiMate relationship validity: a thin facade over the one canonical matrix.

The matrix itself lives in app/config/archimate_relationship_matrix.py (the importer,
the validator and the canvas all read it). This module keeps the Pascal-case function
API its callers use and answers every question by delegating to that matrix; it holds
no validity table of its own.

    is_valid_relationship("BusinessProcess", "ApplicationService", "Serving")
    get_valid_relationships("Goal", "Requirement")
    validate_relationship("Goal", "Goal", "Realization")

Pure Python module: no DB, no LLM, no I/O.
"""

from app.config import archimate_relationship_matrix as _matrix
from app.models.constants import ArchiMateRelationshipType

ALL_ELEMENTS = frozenset(_matrix.ALL_ELEMENTS)


def _normalize_rel_type(rel_type):
    """Normalize relationship type to canonical (Pascal-case) form."""
    if not rel_type:
        return "Association"
    return ArchiMateRelationshipType.normalize(rel_type, pascal_case=True) or rel_type


def is_valid_relationship(source_type, target_type, relationship_type):
    """True if the config matrix accepts this relationship between these types."""
    return _matrix.is_valid_relationship(
        source_type, target_type, _normalize_rel_type(relationship_type)
    )


def get_valid_relationships(source_type, target_type):
    """Sorted Pascal-case relationship types the config matrix accepts for the pair."""
    return sorted(
        _normalize_rel_type(r) for r in _matrix.get_valid_relationships(source_type, target_type)
    )


def validate_relationship(source_type, target_type, relationship_type):
    """Validate a relationship and return a detailed result.

    Returns dict with: valid (bool), relationship_type (normalized), suggested (str or
    None, an alternative if invalid), reason (str).
    """
    rel_norm = _normalize_rel_type(relationship_type)
    result = {
        "source_type": source_type,
        "target_type": target_type,
        "relationship_type": rel_norm,
        "valid": False,
        "suggested": None,
        "reason": "",
    }

    if source_type not in ALL_ELEMENTS:
        result["reason"] = f"Unknown source type: {source_type}"
        return result
    if target_type not in ALL_ELEMENTS:
        result["reason"] = f"Unknown target type: {target_type}"
        return result

    if is_valid_relationship(source_type, target_type, rel_norm):
        result["valid"] = True
        result["reason"] = "Valid per ArchiMate 3.2"
        return result

    valid_alts = get_valid_relationships(source_type, target_type)
    non_assoc = [r for r in valid_alts if r != "Association"]
    if non_assoc:
        result["suggested"] = non_assoc[0]
        joined = ", ".join(valid_alts)
        result["reason"] = (
            f"{rel_norm} is not valid between {source_type} to {target_type}. Valid: {joined}"
        )
    else:
        result["suggested"] = "Association"
        result["reason"] = f"Only Association is valid between {source_type} to {target_type}"
    return result
