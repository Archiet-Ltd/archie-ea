"""The one list of genome domain slices, and the one way to read one.

ADR-genome-01 decision 1. Every consumer that needs organisation facts for AI
context or a generated document reads them through ``get_slice``; nothing reads
the stores beside it. The registry imports the existing builders where they
live and moves nothing:

    business        app/modules/genome/services/coverage_slice.py
    implementation  app/modules/genome/services/roadmap_slice.py
    security        app/modules/enterprise_genome/services/security_slice.py
    ai_systems      app/modules/enterprise_genome/services/ai_systems_slice.py

Each builder keeps its own output and ``spec_hash``. The registry turns that
output into one envelope with a flat ``nodes`` list and an ``edges`` list, in
which every node carries a citation id ``<domain>:<kind>:<store_id>``, a
``provenance`` record (a real ``archimate_element_id``, or ``origin:
synthetic`` with a reason) and the address of the page that shows it. A node
without provenance is a build error, never a gap filled in. ``as_of`` (when the
facts were read) sits outside the hash, so two builds of an unchanged
organisation give the same ``spec_hash``.

The organisation is always an argument from the caller's session or token,
never from a tool argument.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

ENVELOPE_VERSION = "1.0.0"


class SliceNodeError(ValueError):
    """A node reached the envelope without provenance."""


@dataclass(frozen=True)
class SliceSpec:
    domain: str
    store: str
    builder: Callable[[int, object], dict]
    to_nodes: Callable[[dict], tuple]
    filter_keys: tuple = ()
    rank_key: str = "id"
    optional_fields: tuple = field(default_factory=tuple)
    empty_reason: str = ""


# --------------------------------------------------------------------------- #
# Page addresses                                                               #
# --------------------------------------------------------------------------- #


def _path(endpoint: str, **values) -> Optional[str]:
    """The site-relative address of an endpoint, built without a request (the chat
    runs tools on a worker thread that has only an application context)."""
    from flask import current_app

    try:
        return current_app.url_map.bind("").build(endpoint, values)
    except Exception:
        return None


def element_page(element_id) -> Optional[str]:
    """The page that shows one model element, titled with its name."""
    if not isinstance(element_id, int):
        return None
    return _path("archimate.element_impact_graph_page", element_id=element_id)


def _link(provenance: dict, fallback_endpoint: str) -> Optional[str]:
    element_id = (provenance or {}).get("archimate_element_id")
    return element_page(element_id) or _path(fallback_endpoint)


def capability_page(capability_id) -> Optional[str]:
    """Where a capability's support and planned work are shown: its row on the Capability Coverage page."""
    base = _path("genome_coverage.generate")
    return "%s#capability-%s" % (base, capability_id) if base else None


def work_package_page(work_package_id) -> Optional[str]:
    """The work package's own page, which names the capability it changes."""
    return _path("implementation_planning.work_package_detail", work_package_id=work_package_id)


def node_id(domain: str, kind: str, store_id) -> str:
    return "%s:%s:%s" % (domain, kind, store_id)


def _structural(element_id, archimate_type: str = "") -> dict:
    if element_id is None:
        return {
            "origin": "synthetic",
            "archimate_element_id": None,
            "archimate_type": archimate_type,
            "reason": "recorded without a model element",
        }
    return {"origin": "structural", "archimate_element_id": element_id, "archimate_type": archimate_type}


# --------------------------------------------------------------------------- #
# Builder output -> nodes and edges                                            #
# --------------------------------------------------------------------------- #


def _business_nodes(raw: dict) -> tuple:
    supported_by: Dict[int, List[str]] = {}
    edges = []
    for cell in raw.get("cells", []):
        cap = node_id("business", "cap", cell["capability_id"])
        app = node_id("business", "app", cell["application_id"])
        supported_by.setdefault(cell["capability_id"], []).append(app)
        mapping = cell.get("mapping") or {}
        edges.append({
            "source": cap,
            "target": app,
            "type": "supported_by",
            "support_level": mapping.get("support_level"),
            "coverage_percentage": mapping.get("coverage_percentage"),
        })
    nodes = []
    for cap in raw.get("capabilities", []):
        prov = _structural(cap.get("archimate_element_id"), "Capability")
        apps = supported_by.get(cap["id"], [])
        nodes.append({
            "id": node_id("business", "cap", cap["id"]),
            "kind": "cap",
            "name": cap["name"],
            "code": cap.get("code"),
            "supported_by": apps,
            "supported": bool(apps),
            "provenance": prov,
            "link": capability_page(cap["id"]) or _link(prov, "genome_coverage.index"),
        })
    for app in raw.get("applications", []):
        prov = _structural(app.get("archimate_element_id"), "ApplicationComponent")
        nodes.append({
            "id": node_id("business", "app", app["id"]),
            "kind": "app",
            "name": app["name"],
            "provenance": prov,
            "link": _link(prov, "genome_coverage.index"),
        })
    return nodes, edges


def _implementation_nodes(raw: dict) -> tuple:
    nodes, edges = [], []
    for plateau in raw.get("plateaus", []):
        prov = plateau["provenance"]
        nodes.append({
            "id": node_id("implementation", "plateau", plateau["id"]),
            "kind": "plateau",
            "name": plateau["name"],
            "sequence_order": plateau.get("sequence_order"),
            "target_date": plateau.get("target_date"),
            "provenance": prov,
            "link": _path("genome_roadmap.index") or _link(prov, "genome_roadmap.index"),
        })
    for wp in raw.get("work_packages", []):
        prov = wp["provenance"]
        wid = node_id("implementation", "wp", wp["id"])
        plateau = node_id("implementation", "plateau", wp["plateau_id"]) if wp.get("plateau_id") else None
        capability = node_id("business", "cap", wp["capability_id"]) if wp.get("capability_id") else None
        nodes.append({
            "id": wid,
            "kind": "wp",
            "name": wp["name"],
            "status": wp.get("status"),
            "priority": wp.get("priority"),
            "start_date": wp.get("start_date"),
            "target_date": wp.get("target_date"),
            "percent_complete": wp.get("percent_complete"),
            "plateau": plateau,
            "capability": capability,
            "closed_gap_ids": wp.get("closed_gap_ids", []),
            "togaf_phase": wp.get("togaf_phase"),
            "provenance": prov,
            "link": work_package_page(wp["id"]) or _link(prov, "genome_roadmap.index"),
        })
        if plateau:
            edges.append({"source": wid, "target": plateau, "type": "realises"})
        if capability:
            edges.append({"source": wid, "target": capability, "type": "changes"})
    return nodes, edges


def _security_nodes(raw: dict) -> tuple:
    nodes = []
    for row in raw.get("controls", []):
        control = row.get("control") or {}
        requirement = row.get("requirement") or {}
        framework = row.get("framework") or {}
        prov = row["provenance"]
        nodes.append({
            "id": node_id("security", "control", row["node_id"]),
            "kind": "control",
            "name": control.get("title") or control.get("code"),
            "code": control.get("code"),
            "framework": framework.get("code"),
            "requirement": requirement.get("title"),
            "status": requirement.get("status"),
            "implementation_status": requirement.get("implementation_status"),
            "description": control.get("official_reference"),
            "provenance": prov,
            "link": _link(prov, "genome_coverage.index"),
        })
    return nodes, []


def _ai_systems_nodes(raw: dict) -> tuple:
    nodes = []
    for system in raw.get("systems", []):
        prov = _structural(system.get("archimate_element_id"), "ApplicationComponent")
        nodes.append({
            "id": node_id("ai_systems", "system", system.get("archimate_element_id")),
            "kind": "system",
            "name": system.get("name"),
            "risk_class": system.get("risk_class"),
            "model_currency": system.get("model_currency"),
            "release_state": system.get("release_state"),
            "provenance": prov,
            "link": _link(prov, "genome_coverage.index"),
        })
    return nodes, []


# --------------------------------------------------------------------------- #
# Builders, called where they live                                             #
# --------------------------------------------------------------------------- #


def _build_business(org_id, session):
    from app.modules.genome.services.coverage_slice import build_coverage_slice

    return build_coverage_slice(org_id)


def _build_implementation(org_id, session):
    from app.modules.genome.services.roadmap_slice import build_roadmap_slice

    return build_roadmap_slice(org_id)


def _build_security(org_id, session):
    from app.modules.enterprise_genome.services.security_slice import build_security_slice

    return build_security_slice(org_id, session)


def _build_ai_systems(org_id, session):
    from app.modules.enterprise_genome.services.ai_systems_slice import build_ai_systems_slice

    return build_ai_systems_slice(org_id, session)


SLICES: Dict[str, SliceSpec] = {
    "business": SliceSpec(
        domain="business",
        store="unified_capabilities",
        builder=_build_business,
        to_nodes=_business_nodes,
        filter_keys=("kind", "supported"),
        rank_key="name",
        optional_fields=("code",),
        empty_reason="Your model records no capabilities or applications yet.",
    ),
    "implementation": SliceSpec(
        domain="implementation",
        store="unified_work_packages",
        builder=_build_implementation,
        to_nodes=_implementation_nodes,
        filter_keys=("kind", "status", "plateau"),
        rank_key="name",
        optional_fields=("togaf_phase", "closed_gap_ids", "priority"),
        empty_reason="Your model records no plateaus or work packages yet.",
    ),
    "security": SliceSpec(
        domain="security",
        store="compliance_requirements",
        builder=_build_security,
        to_nodes=_security_nodes,
        filter_keys=("kind", "framework", "status"),
        rank_key="name",
        optional_fields=("description", "requirement"),
        empty_reason="Your model records no controls bound to requirements yet.",
    ),
    "ai_systems": SliceSpec(
        domain="ai_systems",
        store="archimate_elements",
        builder=_build_ai_systems,
        to_nodes=_ai_systems_nodes,
        filter_keys=("kind", "risk_class"),
        rank_key="name",
        optional_fields=("release_state",),
        empty_reason="Your model records no AI systems yet.",
    ),
}


def registered_domains() -> List[str]:
    return sorted(SLICES)


def get_slice(domain: str, organization_id: int, session=None) -> dict:
    """The envelope of one registered domain for one organisation.

    Raises KeyError for an unregistered domain (the catalogue tool turns that
    into the list of registered domains) and SliceNodeError when a builder
    hands back a node with no provenance.
    """
    from app.extensions import db

    spec = SLICES[domain]
    if not isinstance(organization_id, int) or organization_id <= 0:
        raise ValueError("an organisation is required")
    session = session or db.session
    raw = spec.builder(organization_id, session)
    nodes, edges = spec.to_nodes(raw)
    for node in nodes:
        prov = node.get("provenance")
        if not isinstance(prov, dict) or not (
            prov.get("archimate_element_id") or (prov.get("origin") == "synthetic" and prov.get("reason"))
        ):
            raise SliceNodeError("%s reached the %s slice without provenance" % (node.get("id"), domain))
    return {
        "slice_version": ENVELOPE_VERSION,
        "organization_id": organization_id,
        "domain": domain,
        "store": spec.store,
        "nodes": nodes,
        "edges": edges,
        "spec_hash": raw.get("spec_hash"),
        "reason": None if nodes else spec.empty_reason,
        # Outside the hash: when the facts were read, not part of what they are.
        "as_of": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
