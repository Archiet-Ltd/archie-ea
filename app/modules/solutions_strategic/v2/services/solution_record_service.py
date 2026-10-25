"""The solution record: everything the solution page shows, assembled once.

The header, the Overview, Design and Review tabs, the properties column and
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
            item = {"name": el.name or "Unnamed", "type": el.type or "", "layer": layer,
                    "href": element_href(layer, el.type or "", el.id),
                    "change": _change_kind(r, el), "notes": (r.notes or "").strip()}
        else:
            layer = (r.layer_type or "").lower()
            item = {"name": r.element_name or "Unnamed", "type": _TABLE_TYPES.get(table, ""),
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


def comments(solution_id: int) -> list[dict]:
    try:
        from app.models.solution_models import SolutionComment
        rows = (SolutionComment.query.filter_by(solution_id=solution_id)
                .order_by(SolutionComment.created_at.desc()).limit(50).all())
        return [{"id": c.id, "author": c.author_name, "content": c.content,
                 "created_at": c.created_at, "section": c.section_name} for c in rows]
    except Exception:
        db.session.rollback()
        return []


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
    }
    if tab == "review":
        ctx["packet"] = review_packet(solution)
        ctx["history"] = board_history(solution)
    return ctx
