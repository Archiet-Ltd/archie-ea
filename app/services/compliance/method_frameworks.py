"""The method frameworks a company can choose at sign-up, as entries of the one framework catalogue.

Each one is held the way the compliance frameworks are: a framework with its own list of items
(called controls in the catalogue). TOGAF lists the ADM phases, ArchiMate its layers, SAFe the
portfolio Kanban states, ITIL 4 its practices, COBIT 2019 its governance and management objectives
and the NIST Cybersecurity Framework its functions and categories. A company that adopts one sees
that list on the Frameworks page and the compliance dashboard, like any other adopted framework.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from app.models.adm_kanban import ADM_PHASES
from app.services.compliance.standard_catalogues import nist_csf_function_items, nist_csf_subcategory_items
from app.services.profiles.safe import KINDS as SAFE_KINDS

_Item = Tuple[str, str, str, str]  # code, title, group, what it is


def _framework(code: str, name: str, description: str, url: str, version: str, items: List[_Item]) -> Dict:
    return {
        "code": code,
        "name": name,
        "description": description,
        "category": "method",
        "jurisdiction": "Global",
        "enforcement_level": "recommended",
        "penalty_risk": "low",
        "applies_to_manufacturing": True,
        "applies_to_region": "Global",
        "industry_specific": "general",
        "official_url": url,
        "standard_version": version,
        "controls": [
            {"control_code": item_code, "title": title, "description": text, "category": group, "priority": "medium"}
            for item_code, title, group, text in items
        ],
    }


_TOGAF_GROUPS = {
    "PRELIM": "Set-up", "A": "Architecture development", "B": "Architecture development",
    "C": "Architecture development", "D": "Architecture development", "E": "Planning", "F": "Planning",
    "G": "Governance", "H": "Governance", "REQ": "Throughout",
}
_TOGAF = [
    (phase["code"].capitalize() if phase["code"] == "PRELIM" else phase["code"],
     phase["name"].replace("Phase %s: " % phase["code"], ""),
     _TOGAF_GROUPS[phase["code"]], phase["description"] + ".")
    for phase in sorted(ADM_PHASES, key=lambda p: p["order"])
]

_ARCHIMATE = [
    ("Strategy", "Strategy layer", "Layers", "Capabilities, resources, value streams and courses of action."),
    ("Business", "Business layer", "Layers", "Actors, roles, processes, functions, services and information."),
    ("Application", "Application layer", "Layers", "Application components, services, interfaces and data objects."),
    ("Technology", "Technology layer", "Layers", "Nodes, devices, system software, networks and technology services."),
    ("Physical", "Physical elements", "Layers", "Equipment, facilities, distribution networks and materials."),
    ("Motivation", "Motivation elements", "Cross-layer", "Stakeholders, drivers, goals, requirements, principles and constraints."),
    ("Implementation", "Implementation and migration", "Cross-layer", "Work packages, deliverables, plateaus and gaps."),
]

_SAFE = [
    ("K%d" % i, state, "Portfolio Kanban", "Epics in this state are shown in the %s column of the portfolio board." % state)
    for i, state in enumerate(SAFE_KINDS["Epic"]["properties"]["kanban_state"]["values"], start=1)
]

_ITIL_GENERAL = [
    "Architecture management", "Continual improvement", "Information security management", "Knowledge management",
    "Measurement and reporting", "Organizational change management", "Portfolio management", "Project management",
    "Relationship management", "Risk management", "Service financial management", "Strategy management",
    "Supplier management", "Workforce and talent management",
]
_ITIL_SERVICE = [
    "Availability management", "Business analysis", "Capacity and performance management", "Change enablement",
    "Incident management", "IT asset management", "Monitoring and event management", "Problem management",
    "Release management", "Service catalogue management", "Service configuration management",
    "Service continuity management", "Service design", "Service desk", "Service level management",
    "Service request management", "Service validation and testing",
]
_ITIL_TECHNICAL = ["Deployment management", "Infrastructure and platform management", "Software development and management"]
_ITIL = [
    ("P%02d" % i, name, group, "The %s practice of ITIL 4." % name.lower())
    for i, (name, group) in enumerate(
        [(n, "General management practices") for n in _ITIL_GENERAL]
        + [(n, "Service management practices") for n in _ITIL_SERVICE]
        + [(n, "Technical management practices") for n in _ITIL_TECHNICAL],
        start=1,
    )
]

_COBIT = [
    ("EDM01", "Ensured governance framework setting and maintenance"), ("EDM02", "Ensured benefits delivery"),
    ("EDM03", "Ensured risk optimization"), ("EDM04", "Ensured resource optimization"),
    ("EDM05", "Ensured stakeholder engagement"),
    ("APO01", "Managed I&T management framework"), ("APO02", "Managed strategy"),
    ("APO03", "Managed enterprise architecture"), ("APO04", "Managed innovation"), ("APO05", "Managed portfolio"),
    ("APO06", "Managed budget and costs"), ("APO07", "Managed human resources"), ("APO08", "Managed relationships"),
    ("APO09", "Managed service agreements"), ("APO10", "Managed vendors"), ("APO11", "Managed quality"),
    ("APO12", "Managed risk"), ("APO13", "Managed security"), ("APO14", "Managed data"),
    ("BAI01", "Managed programs"), ("BAI02", "Managed requirements definition"),
    ("BAI03", "Managed solutions identification and build"), ("BAI04", "Managed availability and capacity"),
    ("BAI05", "Managed organizational change"), ("BAI06", "Managed IT changes"),
    ("BAI07", "Managed IT change acceptance and transitioning"), ("BAI08", "Managed knowledge"),
    ("BAI09", "Managed assets"), ("BAI10", "Managed configuration"), ("BAI11", "Managed projects"),
    ("DSS01", "Managed operations"), ("DSS02", "Managed service requests and incidents"),
    ("DSS03", "Managed problems"), ("DSS04", "Managed continuity"), ("DSS05", "Managed security services"),
    ("DSS06", "Managed business process controls"),
    ("MEA01", "Managed performance and conformance monitoring"), ("MEA02", "Managed system of internal control"),
    ("MEA03", "Managed compliance with external requirements"), ("MEA04", "Managed assurance"),
]
_COBIT_DOMAINS = {
    "EDM": "Evaluate, Direct and Monitor (governance)", "APO": "Align, Plan and Organize (management)",
    "BAI": "Build, Acquire and Implement (management)", "DSS": "Deliver, Service and Support (management)",
    "MEA": "Monitor, Evaluate and Assess (management)",
}
_COBIT_ITEMS = [
    (code, title, _COBIT_DOMAINS[code[:3]], "The COBIT 2019 objective: %s." % title.lower()) for code, title in _COBIT
]

_NIST = [
    ("GV.OC", "Organizational context", "Govern"), ("GV.RM", "Risk management strategy", "Govern"),
    ("GV.RR", "Roles, responsibilities and authorities", "Govern"), ("GV.PO", "Policy", "Govern"),
    ("GV.OV", "Oversight", "Govern"), ("GV.SC", "Cybersecurity supply chain risk management", "Govern"),
    ("ID.AM", "Asset management", "Identify"), ("ID.RA", "Risk assessment", "Identify"),
    ("ID.IM", "Improvement", "Identify"),
    ("PR.AA", "Identity management, authentication and access control", "Protect"),
    ("PR.AT", "Awareness and training", "Protect"), ("PR.DS", "Data security", "Protect"),
    ("PR.PS", "Platform security", "Protect"), ("PR.IR", "Technology infrastructure resilience", "Protect"),
    ("DE.CM", "Continuous monitoring", "Detect"), ("DE.AE", "Adverse event analysis", "Detect"),
    ("RS.MA", "Incident management", "Respond"), ("RS.AN", "Incident analysis", "Respond"),
    ("RS.CO", "Incident response reporting and communication", "Respond"), ("RS.MI", "Incident mitigation", "Respond"),
    ("RC.RP", "Incident recovery plan execution", "Recover"), ("RC.CO", "Incident recovery communication", "Recover"),
]
_NIST_ITEMS = (
    nist_csf_function_items()
    + [
        (code, title, "%s function" % group, "The %s category of the NIST Cybersecurity Framework." % title.lower())
        for code, title, group in _NIST
    ]
    + nist_csf_subcategory_items(_NIST)
)

METHOD_FRAMEWORKS: List[Dict] = [
    _framework("TOGAF", "TOGAF — Architecture Development Method",
               "The phases of the TOGAF Architecture Development Method, from the preliminary phase to architecture change management.",
               "https://www.opengroup.org/togaf", "10", _TOGAF),
    _framework("ARCHIMATE", "ArchiMate — layers and views",
               "The layers of the ArchiMate modelling language that your model is drawn in.",
               "https://www.opengroup.org/archimate-forum", "3.2", _ARCHIMATE),
    _framework("SAFE", "SAFe — portfolio Kanban",
               "The states an epic moves through on the SAFe portfolio Kanban.",
               "https://scaledagileframework.com", "6.0", _SAFE),
    _framework("ITIL-4", "ITIL 4 — practices",
               "The 34 management practices of ITIL 4.", "https://www.axelos.com/certifications/itil-service-management",
               "4", _ITIL),
    _framework("COBIT-2019", "COBIT 2019 — governance and management objectives",
               "The 40 governance and management objectives of COBIT 2019.", "https://www.isaca.org/resources/cobit",
               "2019", _COBIT_ITEMS),
    _framework("NIST-CSF", "NIST Cybersecurity Framework",
               "The six functions of the NIST Cybersecurity Framework 2.0, their 22 categories and 106 subcategories.",
               "https://www.nist.gov/cyberframework", "2.0", _NIST_ITEMS),
]
