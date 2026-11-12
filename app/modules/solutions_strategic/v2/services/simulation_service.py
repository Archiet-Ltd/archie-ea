"""
Simulation of a change on a copy of the model.

The impact analysis engine (ImpactAnalysisService) says what a change reaches. This module adds the
"on a copy" step: the model's counts, running cost and risks as they are now, and as they would be if
the change happened. The after figures are worked out in memory from the real figures; nothing is
written to the model and the run is not stored in the analysis history.
"""
from typing import Any, Dict, List

from flask import url_for

from app import db
from app.middleware.tenant_context import current_org_id
from app.services.application_cost_accessor import get_run_cost_with_source

CHANGE_LABELS = {
    "REPLACE": "Full Replacement",
    "UPGRADE": "Upgrade / Version Change",
    "RETIRE": "Decommission / Retirement",
    "MODIFY": "Minor Modification",
}


def change_label(change_type: str) -> str:
    """The words a person sees for a change type, the same as the choice on the page."""
    return CHANGE_LABELS.get((change_type or "").upper(), (change_type or "").replace("_", " ").capitalize())


COSTS_WITHHELD = "Cost figures are withheld for your role."

# every key that holds a money amount, in a run's result and in a saved scenario's figures
_COST_KEYS = ("estimated_financial_risk", "estimated_risk_cost", "application_cost", "annual_cost", "cost_source",
              "running_cost", "costed_applications")


def may_see_costs(user) -> bool:
    """The one rule the Overview applies to cost: whoever may read the spend pages."""
    from app.decorators.requires_role import may_see_spend_pages

    return may_see_spend_pages(user)


def withhold_costs(result: Any) -> Any:
    """A copy of a run or a saved scenario's figures with every cost left out and the reason stated.

    The stored data is not changed; this is applied on the way out for a person the cost rule withholds from.
    """
    if isinstance(result, list):
        return [withhold_costs(item) for item in result]
    if not isinstance(result, dict):
        return result
    out = {}
    for key, value in result.items():
        out[key] = None if key in _COST_KEYS else withhold_costs(value)
    if "financial_risk_reason" in result:
        out["financial_risk_reason"] = COSTS_WITHHELD
    if result.get("money") is True and "label" in result:
        out.update(before=None, after=None, change=None, note=COSTS_WITHHELD, withheld=True)
    if any(k in result for k in ("estimated_financial_risk", "estimated_risk_cost", "figures", "simulation")):
        out["costs_withheld"] = True
    return out


class ElementNotInModel(LookupError):
    """The element is not in the caller's model (it does not exist, or belongs to another organisation)."""


def _model_figures(org_id: int) -> Dict[str, Any]:
    """The real model's counts and running cost, read now."""
    from app.models import ArchiMateElement
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateRelationship
    from app.models.risk import Risk, RiskStatus

    from app.modules.dashboard.services.overview_figures import _active_applications, left_out_applications

    # the applications the application list counts: a duplicate, a decommissioned application and a
    # repository nobody owns yet are left out, and the figures say so
    applications = db.session.execute(
        _active_applications().where(ApplicationComponent.organization_id == org_id)
    ).scalars().all()
    costs = [get_run_cost_with_source(a)[0] for a in applications]
    costed = [c for c in costs if c is not None]
    return {
        "elements": db.session.scalar(
            db.select(db.func.count()).select_from(ArchiMateElement).where(ArchiMateElement.organization_id == org_id)),
        "relationships": db.session.scalar(
            db.select(db.func.count()).select_from(ArchiMateRelationship).where(
                ArchiMateRelationship.organization_id == org_id)),
        "applications": len(applications),
        "application_element_ids": sorted(a.archimate_element_id for a in applications if a.archimate_element_id),
        "left_out": left_out_applications(org_id),
        "running_cost": float(sum(costed)) if costed else None,
        "costed_applications": len(costed),
        "open_risks": db.session.scalar(
            db.select(db.func.count()).select_from(Risk).where(
                Risk.organization_id == org_id, Risk.status == RiskStatus.OPEN)),
    }


def _relationships_touching(org_id: int, element_id: int) -> int:
    from app.models.archimate_core import ArchiMateRelationship

    return db.session.scalar(
        db.select(db.func.count()).select_from(ArchiMateRelationship).where(
            ArchiMateRelationship.organization_id == org_id,
            db.or_(ArchiMateRelationship.source_id == element_id, ArchiMateRelationship.target_id == element_id)))


def _risks_reached(org_id: int, element_id: int, reached_ids: List[int]) -> List[Dict[str, Any]]:
    """Open risks about the changed element or anything it reaches, from the one risk register."""
    from app.models import ArchiMateElement
    from app.models.application_portfolio import ApplicationComponent
    from app.models.risk import Risk, RiskStatus
    from app.models.risk_entity_link import RiskEntityLink

    element_ids = sorted(set(reached_ids) | {element_id})
    components = dict(db.session.execute(
        db.select(ApplicationComponent.id, ApplicationComponent.archimate_element_id).where(
            ApplicationComponent.organization_id == org_id,
            ApplicationComponent.archimate_element_id.in_(element_ids))).all())
    linked: Dict[int, int] = {}
    if components:
        for risk_id, component_id in db.session.execute(
                db.select(RiskEntityLink.risk_id, RiskEntityLink.entity_id).where(
                    RiskEntityLink.organization_id == org_id,
                    RiskEntityLink.entity_type == "application",
                    RiskEntityLink.entity_id.in_(list(components)))).all():
            linked.setdefault(risk_id, components[component_id])
    risks = db.session.execute(
        db.select(Risk).where(
            Risk.organization_id == org_id,
            Risk.status == RiskStatus.OPEN,
            db.or_(Risk.archimate_element_id.in_(element_ids), Risk.id.in_(list(linked) or [-1])),
        ).order_by(Risk.id)
    ).scalars().all()
    about_ids = {r.archimate_element_id or linked.get(r.id) for r in risks} - {None}
    names = dict(db.session.execute(
        db.select(ArchiMateElement.id, ArchiMateElement.name).where(
            ArchiMateElement.organization_id == org_id, ArchiMateElement.id.in_(sorted(about_ids)))).all()
    ) if about_ids else {}
    from app.release_waves import is_released_endpoint

    register_open = is_released_endpoint("risk.risk_page")
    rows = []
    for risk in risks:
        about = risk.archimate_element_id if risk.archimate_element_id in element_ids else linked.get(risk.id)
        rows.append({
            "id": risk.id,
            "title": risk.title,
            "level": risk.risk_level,
            "about_id": about,
            "about": names.get(about),
            "about_changed_element": about == element_id,
            "href": url_for("risk.risk_page", risk_id=risk.id) if register_open else None,
        })
    return rows


def _applications_note(changed_app, counted, left_out) -> str:
    """Which applications the count covers, in words, and whether the changed element is one of them."""
    if not changed_app:
        lead = "The element is not an application."
    elif counted:
        lead = "The element is an application."
    else:
        lead = "The element is an application the application list does not count, so the count does not move."
    parts = []
    if left_out["decommissioned"]:
        parts.append("%d decommissioned" % left_out["decommissioned"])
    if left_out["duplicate"]:
        parts.append("%d duplicate" % left_out["duplicate"])
    if left_out["repository"]:
        parts.append("%d repository not yet owned" % left_out["repository"])
    if not parts:
        return lead
    return lead + " Counted as the application list counts them: " + ", ".join(parts) + " left out."


def _figure(label: str, before: Any, after: Any, note: str, money: bool = False) -> Dict[str, Any]:
    """One line of the before and after table. A figure that cannot be worked out is None with the reason in note."""
    change = None
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        change = after - before
    return {"label": label, "before": before, "after": after, "change": change, "note": note, "money": money}


def simulate_change(element_id: int, change_type: str = "MODIFY") -> Dict[str, Any]:
    """Work out what a change would do to the model, on a copy. Reads only; writes nothing."""
    from app.modules.solutions_strategic.v2.services.impact_analysis_service import ImpactAnalysisService

    change_type = (change_type or "MODIFY").upper()
    org_id = current_org_id()
    if org_id is None:
        raise ElementNotInModel(element_id)
    from app.models import ArchiMateElement

    found = db.session.scalar(
        db.select(db.func.count()).select_from(ArchiMateElement).where(
            ArchiMateElement.id == element_id, ArchiMateElement.organization_id == org_id))
    if not found:
        raise ElementNotInModel(element_id)
    analysis = ImpactAnalysisService.analyze_change_impact(element_id, change_type, record=False)

    reached = list(analysis["direct_dependencies"]) + list(analysis["indirect_dependencies"])
    reached_ids = [d["id"] for d in reached]
    before = _model_figures(org_id)
    touching = _relationships_touching(org_id, element_id)
    changed_app = analysis.get("changed_application")
    counted_app = bool(changed_app) and element_id in before["application_element_ids"]
    risks = _risks_reached(org_id, element_id, reached_ids)
    removes = change_type == "RETIRE"
    swaps = change_type == "REPLACE"

    if removes:
        elements_after, rel_after = before["elements"] - 1, before["relationships"] - touching
        apps_after = before["applications"] - (1 if counted_app else 0)
        element_note = "The element is removed."
        rel_note = "%d link%s to the element go with it." % (touching, "" if touching == 1 else "s")
    elif swaps:
        elements_after, rel_after, apps_after = before["elements"], before["relationships"], before["applications"]
        element_note = "One element is removed and its replacement is added, so the count stays."
        rel_note = "%d link%s to the element move to its replacement." % (touching, "" if touching == 1 else "s")
    else:
        elements_after, rel_after, apps_after = before["elements"], before["relationships"], before["applications"]
        element_note = "No element is added or removed."
        rel_note = "No link is added or removed."

    own_cost = changed_app["annual_cost"] if changed_app else None
    if before["running_cost"] is None:
        cost_after, cost_note = None, "No application has a running cost recorded, so there is no total to change."
    elif removes and counted_app and own_cost is not None:
        cost_after = before["running_cost"] - own_cost
        cost_note = "The element's own yearly running cost leaves the total."
    elif removes and changed_app and not counted_app:
        cost_after = before["running_cost"]
        cost_note = "The application is not one the application list counts, so the total does not move."
    elif removes and changed_app:
        cost_after = before["running_cost"]
        cost_note = "The application has no running cost recorded, so the total does not move."
    elif swaps and changed_app:
        cost_after = None
        cost_note = "The replacement's running cost is not known, so the new total cannot be worked out."
    else:
        cost_after = before["running_cost"]
        cost_note = ("The element is not an application, so the running cost does not move." if not changed_app
                     else "A change of this kind does not move the recorded running cost.")

    if any(before["left_out"].values()):
        cost_note += " Applications the application list leaves out are not in this total."

    on_element = sum(1 for r in risks if r["about_changed_element"])
    if removes or swaps:
        risk_note = ("%d of them %s about the element itself and would need a new subject." % (
            on_element, "is" if on_element == 1 else "are")) if on_element else (
            "None of them is about the element itself; they are about what depends on it.")
    else:
        risk_note = "None would be closed by this change; each should be reviewed against it."

    figures = [
        _figure("Elements in the model", before["elements"], elements_after, element_note),
        _figure("Links between elements", before["relationships"], rel_after, rel_note),
        _figure("Applications", before["applications"], apps_after,
                _applications_note(changed_app, counted_app, before["left_out"])),
        _figure("Yearly running cost of all applications", before["running_cost"], cost_after, cost_note, money=True),
        _figure("Open risks touching the change", len(risks), len(risks), risk_note),
    ]
    unchanged = _model_figures(org_id) == before
    return {
        **analysis,
        "simulation": {
            "change_type": change_type,
            "change_label": change_label(change_type),
            "figures": figures,
            "risks": risks,
            "real_model": before,
            "real_model_unchanged": unchanged,
        },
    }
