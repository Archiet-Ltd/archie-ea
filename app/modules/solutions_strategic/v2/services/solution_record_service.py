"""The solution record: everything the solution page shows, assembled once.

The header, the seven tabs (Overview, Design, Requirements, Options and
decision, Risks, Review, Delivery), the properties column, the typed notes and
the actions a person may take are read here from the solution's own record.
Nothing is worked out twice: the design score comes from
``solution_design_score_service`` (the one figure every surface shows) and
the board view from ``ChiefArchitectService.solution_packet``.

The page is ``templates/solutions/record.html``. Old addresses of the
solution's pages (the completeness report, the conformance review, the review
packet) land on the matching tab through ``tab_for``.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from flask import url_for
from werkzeug.routing import BuildError

from app import db

logger = logging.getLogger(__name__)

TABS = (
    ("overview", "Overview"),
    ("design", "Design"),
    ("requirements", "Requirements"),
    ("options", "Options and decision"),
    ("risks", "Risks"),
    ("review", "Review"),
    ("delivery", "Delivery"),
)
TAB_KEYS = tuple(k for k, _ in TABS)

# Old addresses and older tab names, each landing on the tab that holds them now.
_TAB_ALIASES = {
    "governance": "review",
    "conformance": "review",
    "review-packet": "review",
    "packet": "review",
    "completeness": "overview",
    "decision": "options",
    "options-and-decision": "options",
    "work-packages": "delivery",
    "roadmap": "delivery",
    "migration-roadmap": "delivery",
    "traceability": "requirements",
    "options-advisor": "options",
    "heatmap": "risks",
    "communications": "overview",
    "notes": "overview",
}

# Which tab fixes each of the ten checks ("edit" opens the edit form).
FIX_TABS = {
    "problem": "edit",
    "owner": "edit",
    "goal": "design",
    "requirements": "requirements",
    "applications": "design",
    "data": "design",
    "runtime": "design",
    "decision": "options",
    "risks": "risks",
    "cost": "overview",
}

LAYERS = (
    ("strategy", "Strategy"),
    ("motivation", "Motivation"),
    ("business", "Business"),
    ("application", "Application"),
    ("data", "Data"),
    ("technology", "Technology"),
    ("physical", "Physical"),
    ("implementation", "Implementation and migration"),
)
LAYER_LABELS = dict(LAYERS)

# The blueprint narrative that described each layer, kept as the layer's note.
_LAYER_NOTES = {
    "motivation": "vision_motivation",
    "business": "business_process",
    "application": "application_cooperation",
    "data": "data_information",
    "technology": "deployment_view",
    "implementation": "transition_roadmap",
}

_STAGES = {
    "draft": "Draft",
    "proposed": "Proposed",
    "in_review": "In review",
    "arb_review": "In review",
    "approved": "Approved",
    "approved_with_conditions": "Approved with conditions",
    "rejected": "Rejected",
    "deferred": "Deferred",
}
_OPEN_REVIEW = {"submitted", "under_review", "in_review", "pending", "scheduled", "open"}


def tab_for(raw) -> str:
    """The tab a request names, with old names mapped; Overview otherwise."""
    key = (raw or "").strip().lower()
    key = _TAB_ALIASES.get(key, key)
    return key if key in TAB_KEYS else "overview"


def record_url(solution_id: int, tab: str = "overview") -> str:
    base = "/solutions/%d" % solution_id
    return base if tab == "overview" else "%s?tab=%s" % (base, tab)


# ── elements ────────────────────────────────────────────────────────────────

def element_href(layer: str, element_type: str, archimate_id) -> str | None:
    """The element's own record page, when the model has one."""
    if not archimate_id:
        return None
    try:
        return url_for("archimate_crud.detail_element", layer=layer,
                       element_type=element_type, element_id=archimate_id)
    except (BuildError, RuntimeError):
        return None


def _change_kind(link, element) -> str:
    """Added, Changed or Retired: what the design does to the element."""
    rel = (getattr(link, "relationship_type", "") or "").strip().lower()
    status = (getattr(element, "status", "") or "").strip().lower()
    if rel in ("retires", "retired", "decommissions") or status in ("retired", "deprecated", "decommissioned"):
        return "Retired"
    if getattr(link, "is_new_element", False):
        return "Added"
    return "Changed"


def design_groups(solution) -> list[dict]:
    """The elements the solution adds, changes and retires, grouped by layer."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_models import SolutionArchiMateElement
    from app.services.solution_design_score_service import _TABLE_TYPES

    rows = SolutionArchiMateElement.query.filter(
        SolutionArchiMateElement.solution_id == solution.id
    ).all()
    ids = [r.element_id for r in rows
           if (r.element_table or "archimate_elements").strip().lower() == "archimate_elements"]
    backbone = {}
    if ids:
        backbone = {e.id: e for e in ArchiMateElement.query.filter(ArchiMateElement.id.in_(ids))}

    by_layer: dict[str, list] = {}
    for r in rows:
        table = (r.element_table or "archimate_elements").strip().lower()
        if table == "archimate_elements":
            el = backbone.get(r.element_id)
            if el is None:
                continue  # another organisation's element never shows
            layer = (el.layer or "").lower()
            item = {"id": el.id, "name": el.name or "Unnamed", "type": el.type or "", "layer": layer,
                    "href": element_href(layer, el.type or "", el.id),
                    "change": _change_kind(r, el), "notes": (r.notes or "").strip()}
        else:
            layer = (r.layer_type or "").lower()
            item = {"id": None, "name": r.element_name or "Unnamed", "type": _TABLE_TYPES.get(table, ""),
                    "layer": layer, "href": None, "change": _change_kind(r, None),
                    "notes": (r.notes or "").strip()}
        by_layer.setdefault(layer, []).append(item)

    narratives = solution.section_narratives or {}
    groups = []
    known = {k for k, _ in LAYERS}
    order = [k for k, _ in LAYERS] + sorted(k for k in by_layer if k not in known)
    for layer in order:
        items = sorted(by_layer.get(layer, []), key=lambda d: (d["change"] != "Added", d["name"].lower()))
        if not items:
            continue
        note = narratives.get(_LAYER_NOTES.get(layer, ""), "")
        if isinstance(note, dict):
            note = note.get("text") or note.get("content") or ""
        groups.append({
            "layer": layer,
            "label": LAYER_LABELS.get(layer, layer.replace("_", " ").title() or "Other"),
            "elements": items,
            "note": (note or "").strip(),
            "added": sum(1 for i in items if i["change"] == "Added"),
            "changed": sum(1 for i in items if i["change"] == "Changed"),
            "retired": sum(1 for i in items if i["change"] == "Retired"),
        })
    return groups


def _first(groups, element_type):
    for g in groups:
        for e in g["elements"]:
            if e["type"] == element_type:
                return e
    return None


# ── figures ─────────────────────────────────────────────────────────────────

def _money(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def cost_summary(solution) -> dict:
    """One-off and running cost from the cost model, else the estimate on the record."""
    from app.models.solution_cost_model import SolutionCostModel

    model = (
        SolutionCostModel.query.filter(SolutionCostModel.solution_id == solution.id)
        .order_by(SolutionCostModel.status.desc(), SolutionCostModel.id.desc())
        .first()
    )
    out = {"currency": "USD", "one_off": None, "running": None, "years": None,
           "estimated": _money(solution.estimated_cost), "actual": _money(solution.actual_cost),
           "recorded": False}
    if model is not None:
        out.update({
            "currency": model.currency or "USD",
            "one_off": _money(model.total_capex),
            "running": _money(model.total_opex),
            "years": model.projection_years,
            "recorded": bool(_money(model.total_capex) or _money(model.total_opex)),
        })
    for key in ("one_off", "running", "estimated", "actual"):
        out[key + "_text"] = money_text(out[key], out["currency"])
    return out


def money_text(value, currency="USD") -> str:
    """A money figure as a person reads it: the currency code and a rounded amount."""
    if value is None:
        return ""
    symbols = {"USD": "$", "EUR": "€", "GBP": "£"}
    prefix = symbols.get((currency or "").upper(), (currency or "").upper() + " ")
    amount = Decimal(value)
    if abs(amount) >= 1_000_000:
        return "%s%sm" % (prefix, ("%.1f" % (amount / 1_000_000)).rstrip("0").rstrip("."))
    return "%s%s" % (prefix, "{:,.0f}".format(amount))


def what_it_changes(solution, groups) -> dict:
    from app.models.solution_models import SolutionCapabilityMapping

    apps = [e for g in groups for e in g["elements"] if e["type"] == "ApplicationComponent"]
    try:
        capabilities = SolutionCapabilityMapping.query.filter_by(solution_id=solution.id).count()
    except Exception:
        db.session.rollback()
        capabilities = 0
    try:
        vendors = solution.vendor_products.count() if hasattr(solution.vendor_products, "count") \
            else len(solution.vendor_products or [])
    except Exception:
        db.session.rollback()
        vendors = 0
    impact_href = None
    first_app = apps[0] if apps else None
    if first_app and first_app.get("href"):
        impact_href = first_app["href"]
    return {
        "added": sum(1 for a in apps if a["change"] == "Added"),
        "changed": sum(1 for a in apps if a["change"] == "Changed"),
        "retired": sum(1 for a in apps if a["change"] == "Retired"),
        "applications": len(apps),
        "capabilities": capabilities,
        "vendors": vendors,
        "elements": sum(len(g["elements"]) for g in groups),
        "impact_href": impact_href,
    }


def score_with_tab_links(score, solution_id: int) -> dict:
    """The design score, each open check linking to the tab that fixes it."""
    data = score.to_dict()
    edit = "/solutions/%d/edit" % solution_id
    for check in data["checks"]:
        tab = FIX_TABS.get(check["key"], "overview")
        check["fix_href"] = edit if tab == "edit" else record_url(solution_id, tab)
    return data


def next_step(score_data: dict, solution, latest_review) -> dict:
    """The one thing to do next, from the first open check or the stage."""
    for check in score_data["checks"]:
        if not check["passed"]:
            return {"label": check["label"], "detail": check["detail"], "href": check["fix_href"],
                    "action": "Fix this"}
    status = (solution.governance_status or "draft").lower()
    if latest_review is not None and status in ("arb_review", "in_review", "proposed"):
        return {"label": "Awaiting the board's decision", "detail": "Every check passes. The review is open.",
                "href": record_url(solution.id, "review"), "action": "Open the review"}
    if status in ("approved", "approved_with_conditions"):
        return {"label": "Deliver it", "detail": "The board approved this design.",
                "href": record_url(solution.id, "delivery"), "action": "Open delivery"}
    return {"label": "Submit for review", "detail": "Every check passes.",
            "href": record_url(solution.id, "review"), "action": "Submit for review"}


# ── header, properties, review ──────────────────────────────────────────────

def stage_label(solution) -> str:
    status = (solution.governance_status or "draft").lower()
    return _STAGES.get(status, status.replace("_", " ").capitalize())


def programme_of(solution) -> dict | None:
    try:
        initiative = solution.initiative
    except Exception:
        initiative = None
    if initiative is not None:
        return {"name": initiative.name, "href": None}
    try:
        workstream = solution.workstream
        programme = workstream.programme if workstream is not None else None
    except Exception:
        programme = None
    if programme is not None:
        href = None
        try:
            href = url_for("transformation_room.programme_room", programme_id=programme.id)
        except (BuildError, RuntimeError):
            pass
        return {"name": programme.name, "href": href}
    return None


def latest_review_of(solution):
    try:
        from app.models.architecture_review_board import ARBReviewItem
        return (ARBReviewItem.query.filter_by(solution_id=solution.id)
                .order_by(ARBReviewItem.id.desc()).first())
    except Exception:
        db.session.rollback()
        return None


def board_history(solution) -> list[dict]:
    """Submissions, decisions and conditions, newest first."""
    try:
        from sqlalchemy.orm import joinedload
        from app.models.architecture_review_board import ARBReviewComment, ARBReviewItem
        from app.modules.solutions_strategic.v2.routes.solution_design_routes import (
            _serialize_arb_review_history_item,
        )
        reviews = (
            ARBReviewItem.query.options(
                joinedload(ARBReviewItem.submitter), joinedload(ARBReviewItem.reviewer),
                joinedload(ARBReviewItem.decided_by),
                joinedload(ARBReviewItem.comments).joinedload(ARBReviewComment.user),
            )
            .filter_by(solution_id=solution.id).order_by(ARBReviewItem.id.desc()).all()
        )
        return [_serialize_arb_review_history_item(r) for r in reviews]
    except Exception as exc:  # the record still opens without its history
        db.session.rollback()
        logger.warning("Board history unavailable for solution %s: %s", solution.id, exc)
        return []


def review_packet(solution) -> dict:
    try:
        from app.modules.solutions_strategic.v2.services.chief_architect_service import (
            ChiefArchitectService,
        )
        return ChiefArchitectService.solution_packet(solution.id) or {"success": False}
    except Exception as exc:
        db.session.rollback()
        logger.warning("Review packet unavailable for solution %s: %s", solution.id, exc)
        return {"success": False, "error": "The review packet could not be assembled."}


def properties(solution, groups) -> list[dict]:
    from app.models.solution_lifecycle_models import SolutionPlateau

    plateaus = [e["name"] for g in groups for e in g["elements"] if e["type"] == "Plateau"]
    if not plateaus:
        try:
            plateaus = [p.name for p in SolutionPlateau.query.filter_by(solution_id=solution.id)
                        .order_by(SolutionPlateau.order).all()]
        except Exception:
            db.session.rollback()
    return [
        {"key": "sponsor", "label": "Sponsor", "value": (solution.business_sponsor or "").strip()},
        {"key": "technical_lead", "label": "Technical lead", "value": (solution.technical_lead or "").strip()},
        {"key": "domain", "label": "Domain", "value": (solution.business_domain or "").strip()},
        {"key": "kind", "label": "Kind", "value": (solution.solution_type or "").strip()},
        {"key": "plateau", "label": "Plateau", "value": ", ".join(plateaus)},
        {"key": "target_date", "label": "Target date", "value": solution.planned_end_date},
        {"key": "phase", "label": "Architecture phase",
         "value": getattr(solution, "adm_phase_label", None) or ("Phase %s" % solution.adm_phase if solution.adm_phase else "")},
    ]


def comments(solution_id: int) -> list[dict] | None:
    """The record's comments, newest first; None when they could not be read, so the page says so
    rather than showing "No comments yet"."""
    try:
        from app.models.solution_models import SolutionComment
        rows = (SolutionComment.query.filter_by(solution_id=solution_id)
                .order_by(SolutionComment.created_at.desc()).limit(50).all())
        return [{"id": c.id, "author": c.author_name, "content": c.content,
                 "created_at": c.created_at, "section": c.section_name} for c in rows]
    except Exception as exc:
        logger.warning("solution record reader (comments): %s", exc)
        db.session.rollback()
        return None


def actions_for(solution, user, latest_review) -> dict:
    """Who may do what on this record. Decide is never the author's."""
    from app.core.auth.permissions import can

    is_author = bool(user is not None and solution.created_by_id == getattr(user, "id", None))
    status = (solution.governance_status or "draft").lower()
    review_open = latest_review is not None and (
        (getattr(latest_review, "status", "") or "").lower() in _OPEN_REVIEW
        or not getattr(latest_review, "decision", None)
    )
    try:
        may_edit = is_author or can(user, "edit", "solution", solution.organization_id)
    except Exception:
        may_edit = is_author
    try:
        may_decide = can(user, "decide", "arb_decision", solution.organization_id)
    except Exception:
        may_decide = False
    decide_href = None
    if latest_review is not None:
        try:
            decide_href = url_for("arb.review_detail", id=latest_review.id)
        except (BuildError, RuntimeError):
            decide_href = "/arb/reviews/%d" % latest_review.id
    return {
        "is_author": is_author,
        "can_edit": bool(may_edit),
        "can_submit": bool(may_edit) and status in ("draft", "proposed", "rejected", "deferred") and not review_open,
        "can_decide": bool(may_decide) and not is_author and review_open and decide_href is not None,
        "decide_href": decide_href,
        "review_open": review_open,
    }


# ── requirements, options, risks, delivery and notes (slice 5b) ────────────

_REQ_KINDS = {
    "functional": "Functional", "quality": "Quality", "non_functional": "Quality",
    "constraint": "Constraint", "data": "Data", "integration": "Integration",
    "compliance": "Compliance", "operational": "Operational",
}
_MOSCOW = {"MUST": "Must", "SHOULD": "Should", "COULD": "Could", "WONT": "Won't"}
_LEVELS = {"very_low": 1, "low": 2, "medium": 3, "high": 4, "very_high": 5, "critical": 5}
_BAND_LABELS = {"low": "Low", "medium": "Medium", "high": "High", "critical": "Critical"}
NOTE_KINDS = (
    ("meeting", "Meeting"),
    ("email", "Email"),
    ("decision", "Decision"),
    ("escalation", "Escalation"),
    ("status_update", "Status update"),
)
_NOTE_LABELS = dict(NOTE_KINDS)


def _name_of(user) -> str:
    """A person's name for the page, or their email, or nothing."""
    if user is None:
        return ""
    full = getattr(user, "full_name", None)
    try:
        full = full() if callable(full) else full
    except Exception:
        full = None
    return (full or getattr(user, "email", "") or "").strip()


def _band(score: int) -> str:
    from app.services import risk_service
    return risk_service.risk_band(score)


_REQUIREMENT_TYPES = ("Requirement", "Constraint", "Principle")


def requirements_for(solution, groups) -> dict:
    """Requirements and constraints from the model (the solution's Motivation elements of type
    Requirement, Constraint and Principle), each beside the element that realises it, followed
    by the record's own requirement rows; the solution's other Motivation elements (drivers,
    goals, outcomes, stakeholders) come with them."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.models import ArchiMateRelationship
    from app.models.solution_architect_models import SolutionConstraint, SolutionRequirement

    rows, constraints = [], []
    by_id = {e["id"]: e for g in groups for e in g["elements"] if e.get("id")}
    motivation = [e for g in groups if g["layer"] == "motivation" for e in g["elements"]]
    model_reqs = [e for e in motivation if e["type"] in _REQUIREMENT_TYPES]
    others = [e for e in motivation if e["type"] not in _REQUIREMENT_TYPES]

    # 1. the model's requirements, each with the element whose realization relationship points at it
    realised: dict = {}
    if model_reqs:
        try:
            rels = (ArchiMateRelationship.query
                    .filter(ArchiMateRelationship.target_id.in_([e["id"] for e in model_reqs]))
                    .filter(db.func.lower(ArchiMateRelationship.type) == "realization")
                    .order_by(ArchiMateRelationship.id).all())
            missing = {r.source_id for r in rels} - set(by_id)
            extra = {e.id: e for e in ArchiMateElement.query.filter(ArchiMateElement.id.in_(missing))} if missing else {}
            for r in rels:
                if r.target_id in realised:
                    continue
                if r.source_id in by_id:
                    s = by_id[r.source_id]
                    realised[r.target_id] = {"name": s["name"], "href": s["href"]}
                elif r.source_id in extra:
                    s = extra[r.source_id]
                    realised[r.target_id] = {"name": s.name or "Unnamed",
                                             "href": element_href((s.layer or "").lower(), s.type or "", s.id)}
        except Exception as exc:
            logger.warning("solution record reader (realizations): %s", exc)
            db.session.rollback()
    for e in sorted(model_reqs, key=lambda d: (d["type"] != "Requirement", d["name"].lower())):
        rows.append({
            "id": "element-%d" % e["id"], "name": e["name"], "href": e["href"], "description": e["notes"],
            "kind": e["type"], "priority": "", "mandatory": e["type"] == "Constraint",
            "status": e["change"], "realised_by": realised.get(e["id"]), "jira": None, "source": "model",
        })

    # 2. the record's own requirement rows (the older store), each beside its realising element
    try:
        query = SolutionRequirement.query.filter_by(solution_id=solution.id)
        if hasattr(SolutionRequirement, "deleted_at"):
            query = query.filter(SolutionRequirement.deleted_at.is_(None))
        reqs = query.order_by(SolutionRequirement.priority.asc().nulls_last(),
                              SolutionRequirement.id.asc()).all()
    except Exception as exc:
        logger.warning("solution record reader (requirement rows): %s", exc)
        db.session.rollback()
        reqs = []
    ids = {r.archimate_element_id for r in reqs if r.archimate_element_id} - set(by_id)
    elements = {}
    if ids:
        try:
            elements = {e.id: e for e in ArchiMateElement.query.filter(ArchiMateElement.id.in_(ids))}
        except Exception as exc:
            logger.warning("solution record reader (requirement elements): %s", exc)
            db.session.rollback()
    for r in reqs:
        linked = None
        if r.archimate_element_id in by_id:
            s = by_id[r.archimate_element_id]
            linked = {"name": s["name"], "href": s["href"]}
        elif r.archimate_element_id in elements:
            el = elements[r.archimate_element_id]
            linked = {"name": el.name or "Unnamed", "href": element_href((el.layer or "").lower(), el.type or "", el.id)}
        kind = r.requirement_type.value if getattr(r, "requirement_type", None) else (r.req_type or "")
        kind = (kind or "").lower()
        priority = _MOSCOW.get((r.moscow_priority or "").upper().replace("'", ""), "")
        if not priority and r.priority:
            priority = "Priority %d" % r.priority
        rows.append({
            "id": r.id, "name": r.name, "href": None, "description": (r.description or "").strip(),
            "kind": _REQ_KINDS.get(kind, kind.replace("_", " ").capitalize() or "Requirement"),
            "priority": priority, "mandatory": bool(r.is_mandatory),
            "status": (r.status or "open").replace("_", " ").capitalize(),
            "realised_by": linked, "jira": r.jira_issue_key, "source": "record",
        })

    # 3. the record's own constraint rows
    pids = {r.problem_id for r in reqs if r.problem_id}
    if pids:
        try:
            for c in (SolutionConstraint.query.filter(SolutionConstraint.problem_id.in_(pids))
                      .order_by(SolutionConstraint.severity.desc().nulls_last(), SolutionConstraint.id).all()):
                value = " ".join(x for x in ((c.value or "").strip(), (c.unit or "").strip()) if x)
                kind = c.constraint_type.value if getattr(c, "constraint_type", None) else ""
                constraints.append({
                    "id": c.id, "name": c.name, "description": (c.description or "").strip(),
                    "kind": kind.replace("_", " ").capitalize(), "value": value,
                    "hard": (c.severity or 0) >= 5,
                })
        except Exception as exc:
            logger.warning("solution record reader (constraint rows): %s", exc)
            db.session.rollback()

    realised_count = sum(1 for r in rows if r["realised_by"])
    return {
        "rows": rows,
        "constraints": constraints,
        "motivation": others,
        "from_model": sum(1 for r in rows if r["source"] == "model"),
        "realised": realised_count,
        "unrealised": len(rows) - realised_count,
        "traceability_href": "/solutions/%d/traceability" % solution.id,
        "add_href": "/solutions/%d?edit=1" % solution.id,
    }


def _score_text(value) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return ("%d" % number) if number == int(number) else ("%.1f" % number)


def options_for(solution) -> dict:
    """The one options analysis about this solution and the decision record it ends in."""
    options, decision, source, adr = [], None, None, None
    try:
        from app.modules.solutions_strategic.v2.services.solution_options_advisor import SolutionOptionsAdvisor
        adr = SolutionOptionsAdvisor.latest(solution.id)
    except Exception as exc:
        logger.warning("solution record reader %d: %s", 4, exc)
        db.session.rollback()
    if adr is not None:
        source = "decision"
        data = SolutionOptionsAdvisor.to_dict(adr)
        for o in data.get("options") or []:
            if not isinstance(o, dict):
                continue
            options.append({
                "name": o.get("name") or "Option",
                "approach": (o.get("approach") or "").replace("_", " ").capitalize(),
                "summary": o.get("summary") or "",
                "cost": (o.get("cost_band") or "").capitalize(),
                "months": o.get("timeline_months"),
                "risk": (o.get("risk") or "").capitalize(),
                "fit": "", "coverage": "",
                "recommended": bool(o.get("recommended")),
                "pros": [p for p in (o.get("pros") or []) if p],
                "cons": [c for c in (o.get("cons") or []) if c],
            })
        decided_by = (getattr(adr, "decided_by", None) or getattr(adr, "approved_by", None)
                      or getattr(adr, "created_by", None))
        status = (adr.status or "proposed").lower()
        decision = {
            "id": adr.id,
            "title": adr.title,
            "status": status.replace("_", " ").capitalize(),
            "status_key": status,
            "context": (adr.context or "").strip(),
            "decision": (adr.decision or "").strip(),
            "rationale": (adr.rationale or "").strip(),
            "consequences": (adr.consequences or "").strip(),
            "effort": data.get("estimated_effort") or "",
            "value": data.get("business_value") or "",
            "decided_by": _name_of(decided_by),
            "decided_at": adr.decided_at or adr.approved_at or adr.created_at,
        }
    else:
        try:
            rows = sorted(list(solution.options or []), key=lambda o: ((o.rank is None), o.rank or 0, o.id))
        except Exception as exc:
            logger.warning("solution record reader %d: %s", 5, exc)
            db.session.rollback()
            rows = []
        if rows:
            source = "options"
        for o in rows:
            options.append({
                "name": o.name, "approach": "", "summary": (o.description or "").strip(),
                "cost": "", "months": None, "risk": _score_text(o.risk_score),
                "fit": _score_text(o.strategic_fit), "coverage": _score_text(o.coverage),
                "recommended": bool(o.is_selected),
                "pros": [p for p in (o.pros or []) if p], "cons": [c for c in (o.cons or []) if c],
            })
    leader = next((o for o in options if o["recommended"]), None)
    return {
        "options": options,
        "decision": decision,
        "leader": leader,
        "source": source,
        "compare_href": "/solutions/%d/options-advisor" % solution.id,
    }


def risks_for(solution, groups) -> dict:
    """The register's risks for this solution (linked to it or to an element it changes),
    with owners, and the cells of a probability-by-impact grid."""
    from app.models.risk import Risk
    from app.services import risk_service

    register_href = "/risks/?solution_id=%d" % solution.id
    rows, seen = [], set()

    def _add(risk, via=None):
        if risk.id in seen:
            return
        seen.add(risk.id)
        likelihood = max(1, min(5, int(risk.likelihood or 1)))
        impact = max(1, min(5, int(risk.impact or 1)))
        score = likelihood * impact
        status = risk.status.value if hasattr(risk.status, "value") else (risk.status or "open")
        status = str(status).lower()
        rows.append({
            "id": risk.id, "title": risk.title, "owner": (risk.owner or "").strip(),
            "likelihood": likelihood, "impact": impact, "score": score,
            "band": _band(score), "band_label": _BAND_LABELS[_band(score)],
            "status": status.replace("_", " ").capitalize(), "status_key": status,
            "mitigation": (risk.mitigation_plan or "").strip(), "via": via, "href": register_href,
        })

    try:
        canonical, legacy = risk_service.solution_risk_sources(solution.id)
    except Exception as exc:
        logger.warning("solution record reader %d: %s", 6, exc)
        db.session.rollback()
        canonical, legacy = [], []
    for risk in canonical:
        _add(risk)
    try:
        for risk in Risk.query.filter_by(solution_id=solution.id).order_by(Risk.id).all():
            _add(risk)
    except Exception as exc:
        logger.warning("solution record reader %d: %s", 7, exc)
        db.session.rollback()
    element_names = {e["id"]: e["name"] for g in groups for e in g["elements"] if e.get("id")}
    if element_names:
        try:
            for risk in (Risk.query.filter(Risk.archimate_element_id.in_(list(element_names)))
                         .order_by(Risk.id).all()):
                _add(risk, via=element_names.get(risk.archimate_element_id))
        except Exception as exc:
            logger.warning("solution record reader %d: %s", 8, exc)
            db.session.rollback()
    for risk in legacy:  # older solution_risks rows the merge has not retired yet
        likelihood = _LEVELS.get((risk.probability or "medium").lower(), 3)
        impact = _LEVELS.get((risk.impact or "medium").lower(), 3)
        score = likelihood * impact
        status = (risk.status or "open").lower()
        rows.append({
            "id": "legacy-%d" % risk.id,
            "title": risk.risk_name or (risk.risk_description or "Risk")[:120],
            "owner": (risk.owner or "").strip(), "likelihood": likelihood, "impact": impact,
            "score": score, "band": _band(score), "band_label": _BAND_LABELS[_band(score)],
            "status": status.replace("_", " ").capitalize(), "status_key": status,
            "mitigation": (risk.mitigation or "").strip(), "via": None, "href": register_href,
        })
    rows.sort(key=lambda d: (-d["score"], d["title"].lower()))

    cells: dict[tuple, list] = {}
    for d in rows:
        cells.setdefault((d["likelihood"], d["impact"]), []).append(d["title"])
    grid = [[{"likelihood": lk, "impact": im, "count": len(cells.get((lk, im), [])),
              "band": _band(lk * im), "titles": cells.get((lk, im), [])}
             for lk in range(1, 6)] for im in range(5, 0, -1)]
    bands = [{"key": key, "label": label, "count": sum(1 for d in rows if d["band"] == key)}
             for key, label, _floor in risk_service.RISK_BANDS]
    open_states = ("open", "in_progress", "mitigating", "identified", "assessed")
    return {
        "rows": rows,
        "grid": grid,
        "bands": [b for b in bands if b["count"]],
        "open": sum(1 for d in rows if d["status_key"] in open_states),
        "without_owner": sum(1 for d in rows if not d["owner"]),
        "register_href": register_href,
        "add_href": register_href,
    }


def _dependency_id(dep):
    if isinstance(dep, bool):
        return None
    if isinstance(dep, int):
        return dep
    if isinstance(dep, str) and dep.isdigit():
        return int(dep)
    if isinstance(dep, dict):
        for key in ("id", "depends_on_id", "work_package_id", "predecessor_id"):
            if dep.get(key) is not None:
                try:
                    return int(dep[key])
                except (TypeError, ValueError):
                    return None
    return None


def _package_row(row_id, name, status, percent, start, target, owner, plateau, depends_on, needed_by, href, store):
    status = (status or "planned").replace("_", " ").capitalize()
    percent = int(percent or 0)
    if status.lower() in ("completed", "complete", "done"):
        percent = 100
    return {
        "id": row_id, "name": name, "status": status, "percent": max(0, min(100, percent)),
        "start": start, "target": target, "owner": owner, "plateau": plateau,
        "depends_on": depends_on, "needed_by": needed_by, "href": href, "store": store,
    }


def delivery_for(solution, groups) -> dict:
    """The solution's delivery work: its work packages with progress, dates, owner, plateau,
    what each depends on and what needs it; its plateaus; the programme; the roadmap.

    The one store is ``unified_work_packages`` (a legacy ``work_packages`` row is copied
    there the moment it is written). Both are read and a name appears once, so nothing a
    person recorded in either store is hidden.
    """
    from app.models.implementation_migration import Plateau, WorkPackage
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.models.user import User

    unified, legacy = [], []
    try:
        unified = (UnifiedWorkPackage.query.filter(db.or_(
            db.and_(UnifiedWorkPackage.context == "solution", UnifiedWorkPackage.context_id == solution.id),
            db.and_(UnifiedWorkPackage.source_type == "solution", UnifiedWorkPackage.source_id == solution.id)))
            .order_by(UnifiedWorkPackage.start_date.asc().nulls_last(), UnifiedWorkPackage.id).all())
    except Exception as exc:
        logger.warning("solution record reader (unified work packages): %s", exc)
        db.session.rollback()
    try:
        legacy = WorkPackage.query.filter_by(context="solution", context_id=solution.id).all()
        legacy.sort(key=lambda w: ((w.sequence_order is None), w.sequence_order or 0, w.id))
    except Exception as exc:
        logger.warning("solution record reader (work packages): %s", exc)
        db.session.rollback()

    owner_ids = {getattr(r, "owner_id", None) for r in unified + legacy} - {None}
    plateau_ids = {getattr(r, "plateau_id", None) for r in unified + legacy} - {None}
    owners, plateau_names = {}, {}
    if owner_ids:
        try:
            # tenant-scoping-ok: names for owner ids read from this solution's own work packages, by id
            owners = {u.id: _name_of(u) for u in User.query.filter(User.id.in_(owner_ids))}
        except Exception as exc:
            logger.warning("solution record reader (owners): %s", exc)
            db.session.rollback()
    if plateau_ids:
        try:
            plateau_names = {p.id: p.name for p in Plateau.query.filter(Plateau.id.in_(plateau_ids))}
        except Exception as exc:
            logger.warning("solution record reader (plateaus): %s", exc)
            db.session.rollback()

    def _names(rows, attr, dep_attr):
        by_id = {r.id: r for r in rows}
        needed: dict = {}
        for r in rows:
            for dep in (getattr(r, dep_attr, None) or []):
                dep_id = _dependency_id(dep)
                if dep_id is not None:
                    needed.setdefault(dep_id, []).append(r.name)

        def depends(r):
            out = []
            for dep in (getattr(r, dep_attr, None) or []):
                dep_id = _dependency_id(dep)
                name = by_id[dep_id].name if dep_id in by_id else (dep.get("name") if isinstance(dep, dict) else None)
                if name:
                    out.append(name)
            return out
        return depends, needed

    packages, seen = [], set()
    depends, needed = _names(unified, "id", "work_dependencies")
    for u in unified:
        href = None
        try:
            href = url_for("implementation_planning.work_package_detail", work_package_id=u.id)
        except (BuildError, RuntimeError):
            pass
        if getattr(u, "archimate_element_id", None):
            href = element_href("implementation", "WorkPackage", u.archimate_element_id) or href
        packages.append(_package_row(
            u.id, u.name, u.status, u.progress_percentage, u.start_date, u.end_date,
            owners.get(u.owner_id) or (u.assigned_to or "").strip(), plateau_names.get(u.plateau_id, ""),
            depends(u), needed.get(u.id, []), href, "unified"))
        seen.add((u.name or "").strip().lower())
    depends, needed = _names(legacy, "id", "dependencies")
    for w in legacy:
        key = (w.name or "").strip().lower()
        if key in seen:
            continue
        seen.add(key)
        packages.append(_package_row(
            "legacy-%d" % w.id, w.name, w.status, w.percent_complete, w.start_date, w.target_date,
            owners.get(w.owner_id, ""), plateau_names.get(w.plateau_id, "") or getattr(getattr(w, "plateau", None), "name", ""),
            depends(w), needed.get(w.id, []), None, "model"))
    try:
        from app.models.roadmap_models import RoadmapWorkPackage
        roadmap_rows = (RoadmapWorkPackage.query.filter_by(source_type="solution", source_id=solution.id)
                        .order_by(RoadmapWorkPackage.start_date.asc().nulls_last(), RoadmapWorkPackage.id).all())
        for r in roadmap_rows:
            key = (r.name or "").strip().lower()
            if key in seen:
                continue
            seen.add(key)
            packages.append(_package_row(
                "roadmap-%d" % r.id, r.name, r.status, r.progress_percentage, r.start_date, r.end_date,
                (r.assigned_to or "").strip(), "", [], [], None, "roadmap"))
    except Exception as exc:
        logger.warning("solution record reader (roadmap work packages): %s", exc)
        db.session.rollback()

    plateaus = []
    try:
        from app.models.solution_lifecycle_models import SolutionPlateau
        plateaus = [{"name": p.name, "target": p.target_date}
                    for p in SolutionPlateau.query.filter_by(solution_id=solution.id)
                    .order_by(SolutionPlateau.order, SolutionPlateau.id).all()]
    except Exception as exc:
        logger.warning("solution record reader (solution plateaus): %s", exc)
        db.session.rollback()
    known = {p["name"] for p in plateaus}
    for p in packages:
        if p["plateau"] and p["plateau"] not in known:
            plateaus.append({"name": p["plateau"], "target": None})
            known.add(p["plateau"])
    roadmap = None
    try:
        from app.modules.solutions_strategic.v2.services.migration_roadmap_advisor import MigrationRoadmapAdvisor
        latest = MigrationRoadmapAdvisor.latest(solution.id)
        if latest is not None:
            roadmap = {
                "headline": (latest.headline or "").strip(), "summary": (latest.summary or "").strip(),
                "plateaus": latest.plateau_count or len(latest.plateaus or []),
                "horizon": latest.horizon_months, "generated_at": latest.generated_at,
            }
    except Exception as exc:
        logger.warning("solution record reader (migration roadmap): %s", exc)
        db.session.rollback()
    return {
        "packages": packages,
        "plateaus": plateaus,
        "roadmap": roadmap,
        "done": sum(1 for p in packages if p["percent"] >= 100),
        "started": sum(1 for p in packages if 0 < p["percent"] < 100),
        "programme": programme_of(solution),
        "target_date": solution.planned_end_date,
        "roadmap_href": "/solutions/%d/migration-roadmap" % solution.id,
        "add_href": "/solutions/%d/migration-roadmap" % solution.id,
    }


def notes_for(solution_id: int) -> list[dict]:
    """Typed notes (meeting, email, decision, escalation, status update), newest first."""
    try:
        from app.models.stakeholder_communication import StakeholderCommunication
        from app.models.user import User
        rows = (StakeholderCommunication.query.filter_by(solution_id=solution_id)
                .order_by(StakeholderCommunication.logged_at.desc(), StakeholderCommunication.id.desc())
                .limit(50).all())
        ids = {r.logged_by for r in rows if r.logged_by}
        # tenant-scoping-ok: names for the authors of this solution's own notes, read by their ids
        users = {u.id: u for u in User.query.filter(User.id.in_(ids))} if ids else {}
        out = []
        for r in rows:
            kind = r.comm_type.value if hasattr(r.comm_type, "value") else str(r.comm_type or "")
            out.append({
                "id": r.id, "kind": kind,
                "kind_label": _NOTE_LABELS.get(kind, kind.replace("_", " ").capitalize()),
                "subject": r.subject, "summary": (r.summary or "").strip(),
                "outcome": (r.outcome or "").strip(), "at": r.logged_at, "by": _name_of(users.get(r.logged_by)),
            })
        return out
    except Exception as exc:
        logger.warning("solution record reader %d: %s", 13, exc)
        db.session.rollback()
        return []


# ── the record ──────────────────────────────────────────────────────────────

def build(solution, tab: str = "overview", user=None) -> dict:
    """The template context of the solution record for one tab."""
    from app.services.solution_design_score_service import compute

    tab = tab_for(tab)
    score = compute(solution)
    score_data = score_with_tab_links(score, solution.id)
    groups = design_groups(solution)
    latest_review = latest_review_of(solution)
    gap = _first(groups, "Gap") or _first(groups, "Goal")
    problem = max((solution.description, solution.scope_description, solution.business_value),
                  key=lambda t: len((t or "").split()))
    ctx = {
        "solution": solution,
        "tab": tab,
        "tabs": [(label, record_url(solution.id, key), key == tab) for key, label in TABS],
        "score": score_data,
        "stage": stage_label(solution),
        "owner": (solution.solution_owner or "").strip(),
        "programme": programme_of(solution),
        "gap": gap,
        "problem": (problem or "").strip(),
        "next_step": next_step(score_data, solution, latest_review),
        "changes": what_it_changes(solution, groups),
        "cost": cost_summary(solution),
        "groups": groups,
        "properties": properties(solution, groups),
        "comments": comments(solution.id),
        "actions": actions_for(solution, user, latest_review),
        "latest_review": latest_review,
        "record_url": record_url(solution.id),
        "notes": notes_for(solution.id),
        "note_kinds": NOTE_KINDS,
    }
    if tab == "review":
        ctx["packet"] = review_packet(solution)
        ctx["history"] = board_history(solution)
    elif tab == "requirements":
        ctx["requirements"] = requirements_for(solution, groups)
    elif tab == "options":
        ctx["options"] = options_for(solution)
    elif tab == "risks":
        ctx["risks"] = risks_for(solution, groups)
    elif tab == "delivery":
        ctx["delivery"] = delivery_for(solution, groups)
    return ctx
