"""What the element page says about a Product: every element that makes it up, by ArchiMate layer.

The members come from the one impact walk (``impact_graph.build_impact_graph``, the data behind
``/api/element/<id>/impact-graph``); this module filters its nodes and edges and adds no traversal. A member is
an element joined to the Product by any one relationship, or joined to such a member by composition,
aggregation, assignment, realization or serving (``OUTWARD_STEPS`` step outward). Owners, cost, decisions,
diagrams, repositories and documents are read from the one store of each; nothing is worked out twice.

Everything a person reads is built here as plain words, so the template prints no model object.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from app.models.archimate_element_types import ArchiMateElementTypes, base_type
from app.modules.architecture.routes.archimate_crud import element_page
from app.services.impact_graph import build_impact_graph, canonical_relationship_type

# How many relationships out from the Product the page walks, past the elements joined to it directly.
OUTWARD_STEPS = 1
_OUTWARD_TYPES = frozenset({"composition", "aggregation", "assignment", "realization", "serving"})
_SHOWN_AS_PROVIDERS = 5

# (section key, heading, layer spellings kept in it, the types an architect can add there, the relationship the
# Product has to the new element). Every pair is one the ArchiMate 3.2 relationship matrix allows.
SECTIONS = (
    ("strategy", "Strategy", ("strategy",),
     ("Capability", "Resource", "ValueStream", "CourseOfAction"), "Association"),
    ("business", "Business", ("business",),
     ("BusinessService", "Contract", "BusinessObject"), "Composition"),
    ("application", "Application", ("application",),
     ("ApplicationService", "ApplicationComponent", "DataObject"), "Association"),
    ("technology", "Technology and physical", ("technology", "physical"),
     ("TechnologyService", "Node", "SystemSoftware", "Equipment"), "Association"),
    ("motivation", "Motivation", ("motivation",),
     ("Requirement", "Goal", "Stakeholder", "Driver"), "Association"),
    ("implementation", "Implementation and migration",
     ("implementation", "implementation & migration", "implementation and migration", "implementation_migration"),
     ("WorkPackage", "Deliverable", "Gap", "Plateau"), "Association"),
)

_EMPTY_REASON = {
    "strategy": "No capability, resource or course of action is joined to this product yet.",
    "business": "No business service, contract or process is joined to this product yet.",
    "application": "No application is joined to this product, directly or through a business service it is made of.",
    "technology": "No technology is joined to this product. Technology usually reaches a product through the applications that serve it.",
    "motivation": "No requirement, goal or driver is joined to this product yet.",
    "implementation": "No work package, deliverable or gap is joined to this product yet.",
}
_SECTION_OF_LAYER = {spelling: key for key, _label, spellings, _types, _rel in SECTIONS for spelling in spellings}
_CLOSED_GAP = ("resolved", "closed")


def filing_layer(element_type: str) -> str:
    """The layer a new element of this type is filed in, spelled as the element form spells it."""
    definition = ArchiMateElementTypes.get_element_type(element_type)
    return (definition.layer if definition else "business").capitalize()


def members(product_id: int, org_id: int) -> Dict[str, Any]:
    """The elements joined to the Product, the relationship that joins each, and the walk they came from.

    Every element is loaded by id and organisation, so an edge into another organisation lists nothing.
    """
    from app.models.archimate_core import ArchiMateElement

    graph = build_impact_graph(product_id, depth=1 + OUTWARD_STEPS)
    if graph is None:
        return {"rows": [], "graph": None, "truncated": False}
    edges = sorted(graph["edges"], key=lambda e: (e["type"] or "", e["source"], e["target"]))
    joined: Dict[int, Dict[str, Any]] = {}
    for edge in edges:
        if product_id not in (edge["source"], edge["target"]):
            continue
        other = edge["target"] if edge["source"] == product_id else edge["source"]
        if other != product_id and other not in joined:
            joined[other] = {"edge": edge, "other_is_source": other == edge["source"], "via": None}
    direct = set(joined)
    for _ in range(OUTWARD_STEPS):
        for edge in edges:
            if canonical_relationship_type(edge["type"]) not in _OUTWARD_TYPES:
                continue
            for near, far in ((edge["source"], edge["target"]), (edge["target"], edge["source"])):
                if near in direct and far != product_id and far not in joined:
                    joined[far] = {"edge": edge, "other_is_source": far == edge["source"], "via": near}
    found = {
        e.id: e for e in ArchiMateElement.query.filter(
            ArchiMateElement.id.in_(list(joined) or [0]), ArchiMateElement.organization_id == org_id
        ).all()
    }
    rows = []
    for el_id, how in joined.items():
        element = found.get(el_id)
        if element is None:
            continue
        rows.append({"element": element, **how})
    rows.sort(key=lambda r: ((r["element"].name or "").lower(), r["element"].id))
    return {"rows": rows, "graph": graph, "truncated": bool(graph.get("truncated"))}


def _relationship_words(row: Dict[str, Any], names: Dict[int, str]) -> str:
    """The relationship as the member holds it: 'Serves this product', 'Serves Remote diagnostics'."""
    direction = "outgoing" if row["other_is_source"] else "incoming"
    words = element_page.relationship_words(row["edge"]["type"], direction)
    target = names.get(row["via"]) if row["via"] else None
    return "%s %s" % (words, target or "this product")


def build(product_ae, *, org_id: int, href_for: Callable, owner_for: Callable, plain_name: Callable, user,
          add_url: Optional[str], export_url: Optional[str]) -> Dict[str, Any]:
    """Everything the Product's page adds to the element page."""
    walked = members(product_ae.id, org_id)
    names = {r["element"].id: r["element"].name for r in walked["rows"]}
    by_section: Dict[str, List[Dict[str, Any]]] = {key: [] for key, *_ in SECTIONS}
    ungrouped = 0
    for row in walked["rows"]:
        element = row["element"]
        section = _SECTION_OF_LAYER.get((element.layer or "").strip().lower())
        if section is None:
            ungrouped += 1
            continue
        applicable, owner_names = owner_for(element)
        by_section[section].append({
            "id": element.id,
            "name": element.name,
            "type": element.type,
            "type_label": plain_name(element.type, user),
            "relationship": _relationship_words(row, names),
            "hops": 1 if row["via"] is None else 1 + OUTWARD_STEPS,
            "href": href_for((element.layer or "").strip().lower(), element.type, element.id),
            "owner": ", ".join(owner_names) if owner_names else (
                "No owner recorded" if applicable else "Owners are not kept for this type"),
            "description": (element.description or "").strip(),
        })
    sections = []
    for key, label, _spellings, types, relationship in SECTIONS:
        rows = by_section[key]
        sections.append({
            "key": key,
            "label": label,
            "rows": rows,
            "count": len(rows),
            "empty_reason": None if rows else _EMPTY_REASON[key],
            "add": {
                "url": add_url,
                "relationship": relationship,
                "types": [(t, plain_name(t, user)) for t in types],
            } if add_url else None,
        })
    shown = [r for s in sections for r in s["rows"]]
    linked = linked_material(product_ae, walked["rows"], org_id)
    cost = cost_of(product_ae.id, walked["rows"], org_id)
    problems = gaps_and_risks(walked["rows"], org_id, href_for)
    from app.services.result_footer_service import product_footer

    return {
        "product_id": product_ae.id,
        "sections": sections,
        "member_total": len(shown),
        "layer_total": sum(1 for s in sections if s["rows"]),
        "ungrouped": ungrouped,
        "truncated": walked["truncated"],
        "linked": linked,
        "cost": cost,
        "problems": problems,
        "summary": summary_for(product_ae, sections, walked["graph"]),
        "footer": product_footer(org_id, product_ae.id, [r["id"] for r in shown]),
        "export_url": export_url,
    }


def summary_for(product_ae, sections, graph) -> str:
    """What the product is and what it depends on, from the same walk as the sections."""
    total = sum(s["count"] for s in sections)
    if not total:
        made = "No element is joined to %s yet." % product_ae.name
    else:
        parts = ["%d in %s" % (s["count"], s["label"]) for s in sections if s["count"]]
        made = "%s has %d connected element%s across %d layer%s: %s." % (
            product_ae.name, total, "" if total == 1 else "s", len(parts), "" if len(parts) == 1 else "s",
            ", ".join(parts))
    providers = [p["name"] for p in (graph or {}).get("providers", [])]
    if providers:
        shown = providers[:_SHOWN_AS_PROVIDERS]
        more = len(providers) - len(shown)
        depends = "It depends on %s%s." % (", ".join(shown), " and %d more" % more if more else "")
    else:
        depends = "No element it depends on is recorded."
    return "%s %s" % (made, depends)


def linked_material(product_ae, rows, org_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """Diagrams, decision records, repositories and documents that touch the product or its members."""
    from flask import url_for

    from app.models.architecture_decision import ArchitectureDecision
    from app.models.archimate_core import SavedDiagram, SavedDiagramElement
    from app.models.miscellaneous import ArchitectureDocument

    ids = [product_ae.id] + [r["element"].id for r in rows]
    diagrams = (
        SavedDiagram.query.join(SavedDiagramElement, SavedDiagramElement.diagram_id == SavedDiagram.id)
        .filter(SavedDiagramElement.element_id.in_(ids), SavedDiagram.organization_id == org_id)
        .distinct().order_by(SavedDiagram.name).all()
    )
    wanted = set(ids)
    decisions = [
        d for d in ArchitectureDecision.query.filter(ArchitectureDecision.organization_id == org_id).order_by(
            ArchitectureDecision.id).all()
        if wanted & {int(x) for x in (d.archimate_element_ids or []) if str(x).isdigit()}
    ]
    repositories: Dict[str, Dict[str, Any]] = {}
    from app.services.result_footer_service import connector_repository

    for element in [product_ae] + [r["element"] for r in rows]:
        found = connector_repository(element)
        if found and found["label"] not in repositories:
            repositories[found["label"]] = {**found, "via": None if element.id == product_ae.id else element.name}
    from app.models.application_portfolio import ApplicationComponent

    element_ids = [r["element"].id for r in rows if base_type(r["element"].type) == "ApplicationComponent"]
    apps = ApplicationComponent.query.filter(
        ApplicationComponent.archimate_element_id.in_(element_ids or [0]),
        ApplicationComponent.organization_id == org_id).all()
    documents = ArchitectureDocument.query.filter(
        ArchitectureDocument.application_id.in_([a.id for a in apps] or [0])).order_by(
        ArchitectureDocument.filename).all()
    app_name = {a.id: a.name for a in apps}
    return {
        "diagrams": [{"name": d.name, "href": url_for("archimate.composer_page", viewpoint_id=d.id)} for d in diagrams],
        "decisions": [{
            "name": d.title, "status": (d.status or "proposed").replace("_", " ").capitalize(),
            "href": url_for("arch_decisions.view_decision", decision_id=d.id)} for d in decisions],
        "repositories": list(repositories.values()),
        "documents": [{
            "name": doc.filename, "via": app_name.get(doc.application_id),
            "href": url_for("application_mgmt.api_download_architecture_document",
                            id=doc.application_id, doc_id=doc.id)} for doc in documents],
    }


def cost_of(product_id: int, rows, org_id: int) -> Dict[str, Any]:
    """The yearly cost of the applications and technology among the members, each from its one store.

    ``total`` is None unless every cost it would add is recorded: a missing cost is named, never counted as 0.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.services import technology_cost
    from app.services.application_cost_accessor import get_annual_cost, get_reporting_currency
    from app.modules.architecture.services.application_technology_links import is_technology_element

    element_ids = [r["element"].id for r in rows if base_type(r["element"].type) == "ApplicationComponent"]
    apps = {a.archimate_element_id: a for a in ApplicationComponent.query.filter(
        ApplicationComponent.archimate_element_id.in_(element_ids or [0]),
        ApplicationComponent.organization_id == org_id).all()}
    lines, missing, total = [], [], 0
    for row in rows:
        element = row["element"]
        amount = None
        if element.id in apps:
            amount = get_annual_cost(apps[element.id])
            what = "Yearly cost"
        elif is_technology_element(element):
            amount = technology_cost.allocation(org_id, element.id)["total"]
            what = "Yearly technology cost"
        else:
            continue
        if amount is None:
            missing.append(element.name)
        else:
            total += amount
            lines.append({"name": element.name, "what": what, "amount": amount})
    return {
        "currency": get_reporting_currency(org_id),
        "lines": lines,
        "missing": missing,
        "total": total if lines and not missing else None,
        "counted": len(lines) + len(missing),
    }


def gaps_and_risks(rows, org_id: int, href_for: Callable) -> List[Dict[str, Any]]:
    """Open gaps and open risks recorded against a member, each one linking to its element's page."""
    from app.models.implementation_migration import Gap
    from app.models.risk import Risk, RiskStatus

    by_id = {r["element"].id: r["element"] for r in rows}
    if not by_id:
        return []
    out = []
    gaps = Gap.query.filter(Gap.archimate_element_id.in_(list(by_id)), Gap.organization_id == org_id).order_by(Gap.name).all()
    for gap in gaps:
        if (gap.resolution_status or "identified").lower() in _CLOSED_GAP:
            continue
        element = by_id[gap.archimate_element_id]
        out.append({"kind": "Gap", "name": gap.name, "status": (gap.resolution_status or "identified").capitalize(),
                    "href": href_for((element.layer or "").lower(), element.type, element.id)})
    risks = Risk.query.filter(Risk.archimate_element_id.in_(list(by_id)), Risk.organization_id == org_id,
                              Risk.status == RiskStatus.OPEN).order_by(Risk.title).all()
    for risk in risks:
        element = by_id[risk.archimate_element_id]
        out.append({"kind": "Risk", "name": risk.title, "status": "Open",
                    "href": href_for((element.layer or "").lower(), element.type, element.id)})
    return out


def export_payload(product_ae, page: Dict[str, Any]) -> Dict[str, Any]:
    """The page as one file: every section, the linked material, the cost and the problems."""
    return {
        "product": {"id": product_ae.id, "name": product_ae.name, "description": product_ae.description},
        "summary": page["summary"],
        "layers": [{"layer": s["label"], "elements": [
            {k: r[k] for k in ("name", "type", "relationship", "owner")} for r in s["rows"]]} for s in page["sections"]],
        "diagrams": [d["name"] for d in page["linked"]["diagrams"]],
        "decisions": [{"title": d["name"], "status": d["status"]} for d in page["linked"]["decisions"]],
        "repositories": [r["label"] for r in page["linked"]["repositories"]],
        "documents": [d["name"] for d in page["linked"]["documents"]],
        "cost": {"currency": page["cost"]["currency"], "total": None if page["cost"]["total"] is None else str(page["cost"]["total"]),
                 "lines": [{"name": c["name"], "amount": str(c["amount"])} for c in page["cost"]["lines"]],
                 "without_recorded_cost": page["cost"]["missing"]},
        "gaps_and_risks": [{k: p[k] for k in ("kind", "name", "status")} for p in page["problems"]],
    }

