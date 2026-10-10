"""Plain-language names for ArchiMate element types and workstream types
(US-16, R1-08). One label map, used by every screen in this bucket -- no
second map in a template or in JS (R1-08 constraint).

Created in R1-03 because the template validator and the journey/preview/
confirm screens all need to show a deliverable's declared element types and
a workstream's type as words a non-architect reads without translation, not
as an ArchiMate type name. A key with no plain-language entry falls back to
a title-cased version of the raw ArchiMate name, so a 59th element type
added later degrades to something readable rather than a KeyError.
"""

from __future__ import annotations

import re

# Every key in ALL_ELEMENT_TYPES (app/models/archimate_element_types.py).
ELEMENT_TYPE_LABELS: dict[str, str] = {
    "Resource": "Resource",
    "Capability": "Capability",
    "CourseOfAction": "Strategy",
    "ValueStream": "Value stream",
    "BusinessActor": "Team or unit",
    "BusinessRole": "Role",
    "BusinessCollaboration": "Working group",
    "BusinessInterface": "Access point",
    "BusinessProcess": "Process",
    "BusinessFunction": "Function",
    "BusinessInteraction": "Interaction",
    "BusinessEvent": "Business event",
    "BusinessService": "Service",
    "BusinessObject": "Business record",
    "Contract": "Contract",
    "Representation": "Document",
    "Product": "Product",
    "ApplicationComponent": "System",
    "ApplicationCollaboration": "Integrated systems",
    "ApplicationInterface": "Integration",
    "ApplicationFunction": "System capability",
    "ApplicationProcess": "System process",
    "ApplicationInteraction": "System interaction",
    "ApplicationEvent": "System event",
    "ApplicationService": "Application service",
    "DataObject": "Data",
    "Node": "Server or platform",
    "Device": "Device",
    "SystemSoftware": "Platform software",
    "TechnologyCollaboration": "Connected infrastructure",
    "TechnologyInterface": "Technical access point",
    "Path": "Network path",
    "CommunicationNetwork": "Network",
    "TechnologyFunction": "Infrastructure capability",
    "TechnologyProcess": "Infrastructure process",
    "TechnologyInteraction": "Infrastructure interaction",
    "TechnologyEvent": "Infrastructure event",
    "TechnologyService": "Infrastructure service",
    "Artifact": "File or artifact",
    "Equipment": "Equipment",
    "Facility": "Facility",
    "DistributionNetwork": "Distribution network",
    "Material": "Material",
    "Stakeholder": "Stakeholder",
    "Driver": "Driver",
    "Assessment": "Assessment",
    "Goal": "Goal",
    "Outcome": "Outcome",
    "Principle": "Principle",
    "Requirement": "Requirement",
    "Constraint": "Constraint",
    "Meaning": "Meaning",
    "Value": "Value",
    "WorkPackage": "Handover",
    "Deliverable": "Deliverable",
    "ImplementationEvent": "Milestone",
    "Plateau": "State",
    "Gap": "Gap",
}

# Every key in WORKSTREAM_TYPES (app/models/transformation_programme.py).
WORKSTREAM_TYPE_LABELS: dict[str, str] = {
    "application_rationalisation": "Systems",
    "process": "Process",
    "organisation_skills": "People and skills",
    "policy_control": "Policy and control",
    "data": "Data",
    "supplier": "Suppliers",
    "technology": "Technology",
    "other": "Other",
}

# Every key in JOURNEY_STAGES (app/models/architecture_journey.py).
JOURNEY_STAGE_LABELS: dict[str, str] = {
    "frame": "Frame",
    "discover": "Discover",
    "shape": "Shape",
    "decide": "Decide",
    "deliver": "Deliver",
}

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def _fallback_label(key: str) -> str:
    return _CAMEL_BOUNDARY.sub(" ", key).strip()


def element_type_label(key: str) -> str:
    return ELEMENT_TYPE_LABELS.get(key) or _fallback_label(key)


def workstream_type_label(key: str) -> str:
    return WORKSTREAM_TYPE_LABELS.get(key) or _fallback_label(key).title()


def journey_stage_label(key: str) -> str:
    return JOURNEY_STAGE_LABELS.get(key) or _fallback_label(key).title()


__all__ = [
    "ELEMENT_TYPE_LABELS",
    "WORKSTREAM_TYPE_LABELS",
    "JOURNEY_STAGE_LABELS",
    "element_type_label",
    "workstream_type_label",
    "journey_stage_label",
]
