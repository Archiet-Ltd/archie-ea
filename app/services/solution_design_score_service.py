"""The design score of a solution: ten plain checks, shown as "n of 10".

This is the one place a solution's design score is worked out. The solution
list, the solution page, the review packet, the blueprint export, the solution
document and the AI chat all read it, so the same solution shows the same
figure everywhere. It replaces the completeness report, the list's twelve
weighted maturity checks, the blueprint section percentages shown as a score,
the side panel's readiness and compliance percentages and the conformance
score.

Every check reads the solution's own record:

* the design elements linked to it (``solution_archimate_elements``, the table
  every writer uses; ``solution_elements`` is never written and is not read);
* the risk register (``risks`` through ``risk_entity_links``) plus any older
  solution risk the merge has not yet retired;
* recorded options, decision records and cost models.

A solution that belongs to another organisation is not found: the solution is
loaded through the tenant-filtered ORM query, so ``design_score`` returns None.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app import db

logger = logging.getLogger(__name__)

TOTAL_CHECKS = 10

# Element types that answer each design question.
GOAL_TYPES = {"Goal", "Gap", "Outcome"}
REQUIREMENT_TYPES = {"Requirement"}
APPLICATION_TYPES = {"ApplicationComponent"}
DATA_TYPES = {"DataObject"}
RUNTIME_TYPES = {"Node", "TechnologyService", "Device", "SystemSoftware"}

# Polymorphic link rows name a layer table instead of archimate_elements.
_TABLE_TYPES = {
    "goals": "Goal",
    "outcomes": "Outcome",
    "gaps": "Gap",
    "requirements": "Requirement",
    "application_components": "ApplicationComponent",
    "data_objects": "DataObject",
    "application_data_objects": "DataObject",
    "technology_nodes": "Node",
    "technology_services": "TechnologyService",
    "technology_devices": "Device",
    "technology_system_software": "SystemSoftware",
}


@dataclass
class LinkedElement:
    element_id: int | None
    name: str
    type: str
    layer: str
    archimate_id: int | None = None


@dataclass
class DesignScore:
    solution_id: int
    checks: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c["passed"])

    @property
    def total(self) -> int:
        return TOTAL_CHECKS

    @property
    def label(self) -> str:
        return "%d of %d" % (self.passed, self.total)

    @property
    def missing(self) -> list:
        return [c for c in self.checks if not c["passed"]]

    def to_dict(self) -> dict:
        return {
            "solution_id": self.solution_id,
            "passed": self.passed,
            "total": self.total,
            "label": self.label,
            "checks": list(self.checks),
            "counts": dict(self.counts),
        }


# ── element reads ───────────────────────────────────────────────────────────

def linked_elements(solution_id: int) -> list[LinkedElement]:
    """Every design element linked to the solution, with its ArchiMate type."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_models import SolutionArchiMateElement

    rows = (
        SolutionArchiMateElement.query
        .filter(SolutionArchiMateElement.solution_id == solution_id)
        .all()
    )
    backbone_ids = [
        r.element_id for r in rows
        if (r.element_table or "archimate_elements").strip().lower() == "archimate_elements"
    ]
    backbone = {}
    if backbone_ids:
        # Tenant-filtered query: an element of another organisation never resolves.
        backbone = {
            e.id: e for e in ArchiMateElement.query.filter(ArchiMateElement.id.in_(backbone_ids))
        }
    out: list[LinkedElement] = []
    for r in rows:
        table = (r.element_table or "archimate_elements").strip().lower()
        if table == "archimate_elements":
            el = backbone.get(r.element_id)
            if el is None:
                continue
            out.append(LinkedElement(r.element_id, el.name or "", el.type or "",
                                     (el.layer or "").lower(), el.id))
        else:
            etype = _TABLE_TYPES.get(table, "")
            out.append(LinkedElement(r.element_id, r.element_name or "", etype,
                                     (r.layer_type or "").lower(), None))
    return out


def linked_by_layer(solution_id: int, layer: str) -> list[dict]:
    """The solution's linked elements in one ArchiMate layer, for documents."""
    wanted = layer.lower()
    return sorted(
        (
            {"id": e.element_id, "name": e.name, "type": e.type, "layer": e.layer}
            for e in linked_elements(solution_id) if e.layer == wanted
        ),
        key=lambda d: d["name"].lower(),
    )


def applications(solution_id: int) -> list[LinkedElement]:
    """The applications the design adds, changes or retires."""
    return [e for e in linked_elements(solution_id) if e.type in APPLICATION_TYPES]


def solution_risks(solution_id: int) -> list[dict]:
    """The risks recorded against the solution, from the risk register first."""
    from app.services import risk_service

    canonical, legacy = risk_service.solution_risk_sources(solution_id)
    risks = [{"id": r.id, "title": r.title, "owner": (r.owner or "").strip(),
              "likelihood": r.likelihood, "impact": r.impact,
              "status": getattr(r.status, "value", r.status) or ""}
             for r in canonical]
    risks += [{"id": None, "title": r.risk_name or r.risk_description or "",
               "owner": (r.owner or "").strip(), "likelihood": r.probability,
               "impact": r.impact, "status": r.status or ""}
              for r in legacy]
    return risks


# ── the ten checks ──────────────────────────────────────────────────────────

def _words(text) -> int:
    return len((text or "").split())


def _realised(requirement_ids: set[int]) -> set[int]:
    """Requirement elements that some element realises."""
    from app.models.archimate_core import ArchiMateRelationship

    if not requirement_ids:
        return set()
    rows = (
        db.session.query(ArchiMateRelationship.target_id, ArchiMateRelationship.type)
        .filter(ArchiMateRelationship.target_id.in_(requirement_ids))
        .all()
    )
    return {t for t, rtype in rows if (rtype or "").strip().lower() in ("realization", "realisation")}


def _requirements(solution_id: int, elements: list[LinkedElement]):
    """(recorded, realised) requirement counts."""
    from app.models.solution_architect_models import SolutionRequirement

    element_reqs = {e.archimate_id for e in elements
                    if e.type in REQUIREMENT_TYPES and e.archimate_id}
    unlinked = 0
    recorded = (
        SolutionRequirement.query
        .filter(SolutionRequirement.solution_id == solution_id,
                SolutionRequirement.deleted_at.is_(None))
        .all()
    )
    for req in recorded:
        if req.archimate_element_id:
            element_reqs.add(req.archimate_element_id)
        else:
            unlinked += 1
    total = len(element_reqs) + unlinked
    return total, len(_realised(element_reqs))


def _options_and_decision(solution_id: int):
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.solution_lifecycle_models import SolutionOption

    options = SolutionOption.query.filter(SolutionOption.solution_id == solution_id).all()
    decisions = ArchitectureDecision.query.filter(ArchitectureDecision.solution_id == solution_id).all()
    compared = len(options) >= 2 or any((d.alternatives or "").strip() for d in decisions)
    decided = bool(decisions) or any(o.is_selected for o in options)
    return len(options), len(decisions), compared, decided


def _costs(solution_id: int):
    from app.models.solution_cost_model import SolutionCostLineItem, SolutionCostModel

    items = (
        SolutionCostLineItem.query
        .join(SolutionCostModel, SolutionCostModel.id == SolutionCostLineItem.cost_model_id)
        .filter(SolutionCostModel.solution_id == solution_id)
        .all()
    )
    one_off = any((i.cost_type or "").lower() == "capex" or (i.frequency or "") == "one_time" for i in items)
    running = any((i.cost_type or "").lower() == "opex" or (i.frequency or "") in ("monthly", "annual")
                  for i in items)
    return one_off, running


def _check(key, label, passed, detail, fix_href):
    return {"key": key, "label": label, "passed": bool(passed), "detail": detail, "fix_href": fix_href}


def compute(solution) -> DesignScore:
    """The ten checks for a loaded solution."""
    sid = solution.id
    base = "/solutions/%d" % sid
    elements = linked_elements(sid)
    types = [e.type for e in elements]

    def count(kinds):
        return sum(1 for t in types if t in kinds)

    problem = max((solution.description, solution.scope_description, solution.business_value), key=_words)
    owner, lead = (solution.solution_owner or "").strip(), (solution.technical_lead or "").strip()
    goals = count(GOAL_TYPES)
    req_total, req_realised = _requirements(sid, elements)
    apps = count(APPLICATION_TYPES)
    data = count(DATA_TYPES)
    runtime = count(RUNTIME_TYPES)
    n_options, n_decisions, compared, decided = _options_and_decision(sid)
    risks = solution_risks(sid)
    unowned = [r for r in risks if not r["owner"]]
    one_off, running = _costs(sid)

    def plural(n, word, many=None):
        return "%d %s" % (n, word if n == 1 else (many or word + "s"))

    checks = [
        _check("problem", "Problem stated", _words(problem) >= 3,
               "The problem is described." if _words(problem) >= 3 else "No problem statement yet.",
               base + "#executive_summary"),
        _check("owner", "Owner and technical lead named", owner and lead,
               "Owner %s, technical lead %s." % (owner, lead) if owner and lead
               else "Missing: %s." % " and ".join(
                   x for x, v in (("owner", owner), ("technical lead", lead)) if not v),
               base + "#executive_summary"),
        _check("goal", "Gap or goal it serves linked", goals > 0,
               plural(goals, "gap or goal", "gaps or goals") + " linked." if goals else "No gap or goal linked.",
               base + "#gap_analysis"),
        _check("requirements", "Requirements realised by the design",
               req_total > 0 and req_realised == req_total,
               ("%d of %s realised by a design element." % (req_realised, plural(req_total, "requirement")))
               if req_total else "No requirements recorded.",
               base + "#requirements_traceability"),
        _check("applications", "Applications identified", apps > 0,
               plural(apps, "application") + " in the design." if apps else "No application in the design.",
               base + "#application_cooperation"),
        _check("data", "Data named", data > 0,
               plural(data, "data object") + " named." if data else "No data object named.",
               base + "#data_information"),
        _check("runtime", "Where it runs named", runtime > 0,
               plural(runtime, "node or technology service", "nodes or technology services") + " named." if runtime
               else "No node or technology service named.",
               base + "#deployment_view"),
        _check("decision", "Options compared and a decision recorded", compared and decided,
               "%s compared, %s recorded." % (plural(n_options, "option"), plural(n_decisions, "decision"))
               if compared and decided else
               ("Options compared, no decision recorded." if compared else "Options not yet compared."),
               base + "/options-advisor"),
        _check("risks", "Risks recorded, each with an owner", risks and not unowned,
               ("%s, every one with an owner." % plural(len(risks), "risk")) if risks and not unowned
               else ("%s without an owner." % plural(len(unowned), "risk") if risks else "No risks recorded."),
               base + "/risks/heatmap"),
        _check("cost", "One-off and running cost recorded", one_off and running,
               "One-off and running cost recorded." if one_off and running
               else "Missing: %s." % " and ".join(
                   x for x, v in (("one-off cost", one_off), ("running cost", running)) if not v),
               base + "#work_packages"),
    ]
    counts = {"elements": len(elements), "applications": apps, "data_objects": data,
              "risks": len(risks), "requirements": req_total, "options": n_options,
              "decisions": n_decisions}
    return DesignScore(solution_id=sid, checks=checks, counts=counts)


def design_score(solution_id: int) -> DesignScore | None:
    """The design score of a solution in the signed-in organisation, or None."""
    from app.models.solution_models import Solution

    solution = Solution.query.filter(Solution.id == solution_id).first()
    if solution is None:
        return None
    return compute(solution)


def design_scores(solutions) -> dict:
    """{solution id: DesignScore} for already-loaded solutions (the list)."""
    return {s.id: compute(s) for s in solutions}
