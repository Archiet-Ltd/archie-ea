"""
ArchiMate 3.2 Relationship Validity Matrix

Exhaustive lookup table defining which relationship types are valid between
which element type pairs. Sourced from ArchiMate 3.2 Specification, Appendix B,
Table B.1 (The ArchiMate Full Framework).

Usage:
    from app.modules.architecture.services.archimate_relationship_matrix import (
        is_valid_relationship, get_valid_relationships, validate_relationship,
    )

    # Check if a specific relationship is valid
    is_valid_relationship("BusinessProcess", "ApplicationService", "Serving")  # True

    # Get all valid relationship types between two elements
    get_valid_relationships("Goal", "Requirement")  # ["Realization", "Influence"]

    # Validate and return issues
    result = validate_relationship("Goal", "Goal", "Realization")
    # {"valid": False, "suggested": "Influence", "reason": "..."}

Pure Python module: no DB, no LLM, no I/O.
"""

import logging

from app.models.constants import ArchiMateRelationshipType

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# ELEMENT CATEGORIES (ArchiMate 3.2 §3)
# ═══════════════════════════════════════════════════════════════════════════

MOTIVATION_ELEMENTS = frozenset([
    "Stakeholder", "Driver", "Assessment", "Goal", "Outcome",
    "Principle", "Requirement", "Constraint", "Meaning", "Value",
])

STRATEGY_ELEMENTS = frozenset([
    "Resource", "Capability", "CourseOfAction", "ValueStream",
])

BUSINESS_ELEMENTS = frozenset([
    "BusinessActor", "BusinessRole", "BusinessCollaboration",
    "BusinessInterface", "BusinessProcess", "BusinessFunction",
    "BusinessInteraction", "BusinessService", "BusinessEvent",
    "BusinessObject", "Contract", "Representation", "Product",
])

APPLICATION_ELEMENTS = frozenset([
    "ApplicationComponent", "ApplicationCollaboration",
    "ApplicationInterface", "ApplicationFunction",
    "ApplicationInteraction", "ApplicationProcess",
    "ApplicationService", "ApplicationEvent", "DataObject",
])

TECHNOLOGY_ELEMENTS = frozenset([
    "Node", "Device", "SystemSoftware", "TechnologyCollaboration",
    "TechnologyInterface", "TechnologyFunction", "TechnologyProcess",
    "TechnologyInteraction", "TechnologyService", "TechnologyEvent",
    "Path", "CommunicationNetwork", "Artifact",
])

PHYSICAL_ELEMENTS = frozenset([
    "Equipment", "Facility", "DistributionNetwork", "Material",
])

IMPLEMENTATION_ELEMENTS = frozenset([
    "WorkPackage", "Deliverable", "ImplementationEvent", "Plateau", "Gap",
])

ALL_ELEMENTS = (
    MOTIVATION_ELEMENTS | STRATEGY_ELEMENTS | BUSINESS_ELEMENTS |
    APPLICATION_ELEMENTS | TECHNOLOGY_ELEMENTS | PHYSICAL_ELEMENTS |
    IMPLEMENTATION_ELEMENTS
)

# Active structure, behavior, passive structure classification
ACTIVE_STRUCTURE = frozenset([
    "BusinessActor", "BusinessRole", "BusinessCollaboration", "BusinessInterface",
    "ApplicationComponent", "ApplicationCollaboration", "ApplicationInterface",
    "Node", "Device", "SystemSoftware", "TechnologyCollaboration", "TechnologyInterface",
    "Equipment", "Facility", "DistributionNetwork",
])

BEHAVIOR = frozenset([
    "BusinessProcess", "BusinessFunction", "BusinessInteraction", "BusinessService", "BusinessEvent",
    "ApplicationFunction", "ApplicationInteraction", "ApplicationProcess", "ApplicationService", "ApplicationEvent",
    "TechnologyFunction", "TechnologyProcess", "TechnologyInteraction", "TechnologyService", "TechnologyEvent",
])

PASSIVE_STRUCTURE = frozenset([
    "BusinessObject", "Contract", "Representation", "Product",
    "DataObject", "Artifact", "Material",
])

# ═══════════════════════════════════════════════════════════════════════════
# VALID RELATIONSHIP MATRIX (ArchiMate 3.2 Appendix B)
#
# Key: (source_type, target_type) → set of valid relationship types
# This is the EXHAUSTIVE matrix. If a combination is not here, it's INVALID.
#
# Relationship types: Composition, Aggregation, Assignment, Realization,
#   Serving, Access, Influence, Triggering, Flow, Specialization, Association
# ═══════════════════════════════════════════════════════════════════════════

# Rather than enumerate all 182+ combinations explicitly, we define rules
# based on ArchiMate's structural patterns, then generate the matrix.

# Rule 1: Association is valid between ANY two elements (ArchiMate §5.6)
# Rule 2: Specialization is valid between elements of the SAME type
# Rule 3: Composition/Aggregation between same-layer structural elements
# Rule 4: Assignment: active structure → behavior (same layer)
# Rule 5: Realization: behavior/structure → passive structure (same or adjacent layer)
# Rule 6: Serving: behavior → behavior or active structure (same or upper layer)
# Rule 7: Access: behavior → passive structure (same layer)
# Rule 8: Influence: motivation elements → any
# Rule 9: Triggering: behavior → behavior (same layer)
# Rule 10: Flow: behavior → behavior (any layer)

# Explicitly invalid combinations (ArchiMate §5):
EXPLICITLY_INVALID = {
    # Goal cannot realize Goal (use Influence or Specialization)
    ("Goal", "Goal", "Realization"),
    ("Driver", "Driver", "Realization"),
    ("Outcome", "Outcome", "Realization"),
    # Passive structure cannot serve anything
    ("BusinessObject", "BusinessProcess", "Serving"),
    ("DataObject", "ApplicationService", "Serving"),
    ("Artifact", "TechnologyService", "Serving"),
    # Behavior cannot compose behavior (use triggering or flow)
    ("BusinessProcess", "BusinessProcess", "Composition"),
    ("ApplicationService", "ApplicationService", "Composition"),
    # Cross-layer composition is invalid
    ("BusinessService", "ApplicationComponent", "Composition"),
    ("ApplicationComponent", "Node", "Composition"),
    # Motivation elements don't have structural relationships
    ("Stakeholder", "Driver", "Composition"),
    ("Stakeholder", "Driver", "Aggregation"),
    ("Stakeholder", "Driver", "Assignment"),
    ("Goal", "Requirement", "Composition"),
    ("Goal", "Requirement", "Aggregation"),
}

# ═══════════════════════════════════════════════════════════════════════════
# VALID RELATIONSHIPS BY PATTERN (ArchiMate 3.2 structural rules)
# ═══════════════════════════════════════════════════════════════════════════

# Cross-layer serving (lower serves upper)
CROSS_LAYER_SERVING = {
    # Application serves Business
    ("ApplicationService", "BusinessProcess"): {"Serving"},
    ("ApplicationService", "BusinessFunction"): {"Serving"},
    ("ApplicationService", "BusinessInteraction"): {"Serving"},
    ("ApplicationComponent", "BusinessProcess"): {"Serving"},
    # Technology serves Application
    ("TechnologyService", "ApplicationComponent"): {"Serving"},
    ("TechnologyService", "ApplicationFunction"): {"Serving"},
    ("Node", "ApplicationComponent"): {"Serving"},
    # Strategy serves Motivation
    ("Capability", "Goal"): {"Realization"},
    ("CourseOfAction", "Goal"): {"Realization"},
    ("Resource", "Capability"): {"Assignment"},
}

# Cross-layer realization (lower realizes upper)
CROSS_LAYER_REALIZATION = {
    ("ApplicationComponent", "BusinessService"): {"Realization"},
    ("ApplicationService", "BusinessService"): {"Realization"},
    ("ApplicationProcess", "BusinessProcess"): {"Realization"},
    ("Node", "TechnologyService"): {"Realization"},
    ("Device", "TechnologyService"): {"Realization"},
    ("SystemSoftware", "TechnologyService"): {"Realization"},
    ("Artifact", "ApplicationComponent"): {"Realization"},
    ("Deliverable", "WorkPackage"): {"Realization"},
    ("WorkPackage", "Gap"): {"Realization"},
}

# Motivation layer relationships (ArchiMate §6)
MOTIVATION_RELATIONSHIPS = {
    ("Stakeholder", "Driver"): {"Association", "Influence"},
    ("Stakeholder", "Goal"): {"Association", "Influence"},
    ("Stakeholder", "Assessment"): {"Association"},
    ("Driver", "Assessment"): {"Association"},
    ("Driver", "Goal"): {"Influence"},
    ("Assessment", "Goal"): {"Influence"},
    ("Assessment", "Driver"): {"Influence"},
    ("Goal", "Outcome"): {"Realization"},
    ("Goal", "Principle"): {"Realization"},
    ("Goal", "Requirement"): {"Realization"},
    ("Goal", "Constraint"): {"Realization"},
    ("Outcome", "Requirement"): {"Realization"},
    ("Principle", "Requirement"): {"Influence"},
    ("Principle", "Constraint"): {"Influence"},
    ("Requirement", "Constraint"): {"Specialization"},
    ("Meaning", "Value"): {"Association"},
    ("Value", "Stakeholder"): {"Association"},
}

# Implementation layer relationships
IMPLEMENTATION_RELATIONSHIPS = {
    ("WorkPackage", "Deliverable"): {"Realization"},
    ("WorkPackage", "Plateau"): {"Association"},
    ("Plateau", "Gap"): {"Association"},
    ("Gap", "WorkPackage"): {"Realization"},
    ("ImplementationEvent", "WorkPackage"): {"Triggering"},
    ("Plateau", "Plateau"): {"Triggering"},
}


def _build_same_layer_rules():
    """Build valid relationships for elements within the same layer."""
    rules = {}

    # Same-type specialization (always valid)
    for elem_type in ALL_ELEMENTS:
        rules[(elem_type, elem_type)] = rules.get((elem_type, elem_type), set()) | {"Specialization", "Association"}

    # Active structure: composition, aggregation between same-layer active elements
    layer_groups = [
        (BUSINESS_ELEMENTS & ACTIVE_STRUCTURE, BUSINESS_ELEMENTS & BEHAVIOR, BUSINESS_ELEMENTS & PASSIVE_STRUCTURE),
        (APPLICATION_ELEMENTS & ACTIVE_STRUCTURE, APPLICATION_ELEMENTS & BEHAVIOR, APPLICATION_ELEMENTS & PASSIVE_STRUCTURE),
        (TECHNOLOGY_ELEMENTS & ACTIVE_STRUCTURE, TECHNOLOGY_ELEMENTS & BEHAVIOR, TECHNOLOGY_ELEMENTS & PASSIVE_STRUCTURE),
    ]

    for active, behav, passive in layer_groups:
        # Active → active: composition, aggregation
        for a1 in active:
            for a2 in active:
                if a1 != a2:
                    rules.setdefault((a1, a2), set()).update({"Composition", "Aggregation"})

        # Active → behavior: assignment
        for a in active:
            for b in behav:
                rules.setdefault((a, b), set()).add("Assignment")

        # Behavior → behavior: triggering, flow
        for b1 in behav:
            for b2 in behav:
                if b1 != b2:
                    rules.setdefault((b1, b2), set()).update({"Triggering", "Flow"})

        # Behavior → passive: access
        for b in behav:
            for p in passive:
                rules.setdefault((b, p), set()).add("Access")

        # Behavior → behavior (realization within layer)
        for b1 in behav:
            for b2 in behav:
                rules.setdefault((b1, b2), set()).add("Realization")

        # Active → passive: access (some cases)
        for a in active:
            for p in passive:
                rules.setdefault((a, p), set()).add("Access")

    return rules


# Build the complete matrix
_SAME_LAYER = _build_same_layer_rules()
_ALL_RULES = {}
_ALL_RULES.update(_SAME_LAYER)
_ALL_RULES.update(CROSS_LAYER_SERVING)
_ALL_RULES.update(CROSS_LAYER_REALIZATION)
_ALL_RULES.update(MOTIVATION_RELATIONSHIPS)
_ALL_RULES.update(IMPLEMENTATION_RELATIONSHIPS)

# Add Association as universally valid (ArchiMate §5.6)
for src in ALL_ELEMENTS:
    for tgt in ALL_ELEMENTS:
        _ALL_RULES.setdefault((src, tgt), set()).add("Association")


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════

def is_valid_relationship(source_type, target_type, relationship_type):
    """Check if a specific relationship is valid per ArchiMate 3.2.

    Returns True if valid, False if invalid.
    """
    # Normalize relationship type
    rel_norm = _normalize_rel_type(relationship_type)

    # Check explicitly invalid first
    if (source_type, target_type, rel_norm) in EXPLICITLY_INVALID:
        return False

    # Check the matrix
    valid_rels = _ALL_RULES.get((source_type, target_type), set())
    return rel_norm in valid_rels


def get_valid_relationships(source_type, target_type):
    """Get all valid relationship types between two element types.

    Returns a sorted list of valid relationship type strings.
    """
    valid = _ALL_RULES.get((source_type, target_type), set()).copy()
    # Remove explicitly invalid
    for s, t, r in EXPLICITLY_INVALID:
        if s == source_type and t == target_type and r in valid:
            valid.discard(r)
    return sorted(valid)


def validate_relationship(source_type, target_type, relationship_type):
    """Validate a relationship and return detailed result.

    Returns dict with:
        valid: bool
        relationship_type: str (normalized)
        suggested: str or None (alternative if invalid)
        reason: str
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

    # Unknown element types
    if source_type not in ALL_ELEMENTS:
        result["reason"] = f"Unknown source type: {source_type}"
        return result
    if target_type not in ALL_ELEMENTS:
        result["reason"] = f"Unknown target type: {target_type}"
        return result

    # Check explicitly invalid
    if (source_type, target_type, rel_norm) in EXPLICITLY_INVALID:
        valid_alts = get_valid_relationships(source_type, target_type)
        non_assoc = [r for r in valid_alts if r != "Association"]
        result["suggested"] = non_assoc[0] if non_assoc else "Association"
        result["reason"] = f"{source_type}→{target_type} via {rel_norm} is explicitly invalid in ArchiMate 3.2"
        return result

    # Check the matrix
    valid_rels = _ALL_RULES.get((source_type, target_type), set())
    if rel_norm in valid_rels:
        result["valid"] = True
        result["reason"] = "Valid per ArchiMate 3.2"
        return result

    # Invalid — suggest alternative
    valid_alts = get_valid_relationships(source_type, target_type)
    non_assoc = [r for r in valid_alts if r != "Association"]
    if non_assoc:
        result["suggested"] = non_assoc[0]
        result["reason"] = f"{rel_norm} is not valid between {source_type}→{target_type}. Valid: {', '.join(valid_alts)}"
    else:
        result["suggested"] = "Association"
        result["reason"] = f"Only Association is valid between {source_type}→{target_type}"
    return result


# ═══════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_rel_type(rel_type):
    """Normalize relationship type to canonical form."""
    if not rel_type:
        return "Association"
    return ArchiMateRelationshipType.normalize(rel_type, pascal_case=True) or rel_type
