"""SAFe (Scaled Agile Framework) profile — kinds and allowed element types.

Each framework profile declares:
- ``framework_id`` — short identifier used in the property value.
- ``framework_label`` — human-readable name.
- ``kinds`` — a dict mapping each kind name to the standard ArchiMate types
  it may be applied to, and the typed properties it carries.

This is the one registry for SAFe kinds. A new framework adds a sibling file.
"""

from __future__ import annotations

from typing import Dict, List

FRAMEWORK_ID = "safe"
FRAMEWORK_LABEL = "SAFe"

# Kinds allowed per standard ArchiMate type.
# The key is the kind name; the value is the list of standard ArchiMate types
# that may carry this kind.
KINDS: Dict[str, Dict] = {
    "Epic": {
        "allowed_types": ["WorkPackage"],
        "properties": {
            "business_value": {"type": "integer", "label": "Business Value", "min": 1},
            "time_criticality": {"type": "integer", "label": "Time Criticality", "min": 1},
            "risk_reduction": {"type": "integer", "label": "Risk Reduction", "min": 1},
            "job_size": {"type": "integer", "label": "Job Size", "min": 1},
            "kanban_state": {
                "type": "enum",
                "label": "Kanban State",
                "values": ["Funnel", "Reviewing", "Analyzing", "Portfolio Backlog", "Implementing", "Done"],
                "default": "Funnel",
            },
        },
    },
    "Feature": {
        "allowed_types": ["WorkPackage"],
        "properties": {
            "business_value": {"type": "integer", "label": "Business Value", "min": 1},
            "time_criticality": {"type": "integer", "label": "Time Criticality", "min": 1},
            "risk_reduction": {"type": "integer", "label": "Risk Reduction", "min": 1},
            "job_size": {"type": "integer", "label": "Job Size", "min": 1},
        },
    },
    "Capability (SAFe)": {
        "allowed_types": ["WorkPackage"],
        "properties": {
            "business_value": {"type": "integer", "label": "Business Value", "min": 1},
            "time_criticality": {"type": "integer", "label": "Time Criticality", "min": 1},
            "risk_reduction": {"type": "integer", "label": "Risk Reduction", "min": 1},
            "job_size": {"type": "integer", "label": "Job Size", "min": 1},
        },
    },
    "Enabler": {
        "allowed_types": ["WorkPackage"],
        "properties": {
            "business_value": {"type": "integer", "label": "Business Value", "min": 1},
            "time_criticality": {"type": "integer", "label": "Time Criticality", "min": 1},
            "risk_reduction": {"type": "integer", "label": "Risk Reduction", "min": 1},
            "job_size": {"type": "integer", "label": "Job Size", "min": 1},
        },
    },
    "Agile Release Train": {
        "allowed_types": ["BusinessCollaboration"],
        "properties": {},
    },
    "Solution Train": {
        "allowed_types": ["BusinessCollaboration"],
        "properties": {},
    },
    "Strategic Theme": {
        "allowed_types": ["Goal"],
        "properties": {},
    },
}


def allowed_kinds_for_type(archimate_type: str) -> List[str]:
    """Return the kind names allowed for a given standard ArchiMate type."""
    return [kind for kind, info in KINDS.items() if archimate_type in info["allowed_types"]]


def kind_properties(kind: str) -> Dict:
    """Return the typed property definitions for a kind, or empty dict."""
    info = KINDS.get(kind)
    return info["properties"] if info else {}


def kind_allowed_for_type(kind: str, archimate_type: str) -> bool:
    """True if *kind* may be applied to an element of *archimate_type*."""
    info = KINDS.get(kind)
    if not info:
        return False
    return archimate_type in info["allowed_types"]