"""The recommendations list: the prioritised actions, stated as advice, plus ownership and cost advice.

Nothing here asks a model. Every recommendation is worked out from the organisation's own records, so
the list is the same when AI answers are paused.

Sources, each read once and scoped to the organisation:
  * the prioritised actions (priority_action_service): open gaps, late or blocked work packages and
    open risks, in the order that service ranks them; a gap that came from a compliance finding is
    labelled Compliance
  * applications nobody owns (the same "owned" rule the overview uses)
  * applications in their sunset stage that still carry a run cost

Order: the prioritised actions keep their rank. Ownership advice follows, then cost advice (largest
cost first). The list is cut at LIMIT; ``total`` says how many there were before the cut.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app import db
from app.services import priority_action_service, result_footer_service

LIMIT = 50

RANKING_RULE = (
    "Gaps, work packages and risks keep the order of the prioritised actions: by severity, critical first. "
    "Advice to name an owner follows, then advice about the run cost of applications that are being retired."
)

_RECORD_LABELS = {
    "Gap": "Open the gap",
    "Work package": "Open the work package",
    "Risk": "Open the risk",
    "Compliance": "Open the finding",
    "Ownership": "Open the application",
    "Cost": "Open the application",
}


def _record_link(row: Dict[str, Any]) -> Optional[str]:
    """The page of the record a prioritised action acts on."""
    link = result_footer_service.page_link
    kind, record_id = row["kind"], row["record_id"]
    if kind == "gap":
        return link("archimate_crud.detail_element", layer="implementation", element_type="Gap", element_id=record_id)
    if kind == "work_package":
        return link("implementation_planning.work_package_detail", work_package_id=record_id)
    return link("risk.risk_page", risk_id=record_id)


def _compliance_gap_ids(organization_id: int, gap_ids: List[int]) -> set:
    if not gap_ids:
        return set()
    from app.models.implementation_migration import Gap

    found = db.session.execute(
        db.select(Gap.id).where(
            Gap.organization_id == organization_id, Gap.id.in_(gap_ids), Gap.source_table == "compliance_gaps"
        )
    ).scalars()
    return set(found)


def _from_actions(organization_id: int) -> List[Dict[str, Any]]:
    actions = priority_action_service.prioritised_actions(organization_id)["rows"]
    compliance = _compliance_gap_ids(organization_id, [r["record_id"] for r in actions if r["kind"] == "gap"])
    rows = []
    for action in actions:
        category = priority_action_service.KIND_LABELS[action["kind"]]
        if action["kind"] == "gap" and action["record_id"] in compliance:
            category = "Compliance"
        rows.append({
            "category": category,
            "title": action["action"],
            "reason": action["why"],
            "severity": action["severity"],
            "record_name": action["name"],
            "record_href": _record_link(action),
            "record_label": _RECORD_LABELS[category],
            "footer": action["footer"],
        })
    return rows


def _advice_rows(organization_id: int) -> List[Dict[str, Any]]:
    from app.models.application_portfolio import ApplicationComponent as A
    from app.models.application_portfolio import lifecycle_label
    from app.modules.dashboard.services.overview_figures import _active_applications
    from app.services import vendor_register
    from app.services.application_cost_accessor import get_run_cost_with_source
    from app.services.element_ownership_service import application_is_owned

    apps = db.session.execute(
        _active_applications().where(A.organization_id == organization_id).order_by(A.name)
    ).scalars().all()
    ledger = vendor_register.contract_ledger(organization_id)
    on_system = vendor_register.counted_by_system(ledger) if ledger else {}
    detail = result_footer_service.page_link

    result_footer_service.prefetch_element_origins(apps)
    ownership: List[Dict[str, Any]] = []
    cost: List[Dict[str, Any]] = []
    for app in apps:
        href = detail("unified_applications.application_detail", id=app.id)
        stage = lifecycle_label(app.lifecycle_status)
        if not application_is_owned(app):
            ownership.append({
                "category": "Ownership",
                "title": "Name an owner for %s" % app.name,
                "reason": "No one is recorded as accountable for this application, so no one answers for its "
                          "cost, its risks or its retirement.",
                "severity": None,
                "record_name": app.name,
                "record_href": href,
                "record_label": _RECORD_LABELS["Ownership"],
                "footer": result_footer_service.build_footer([
                    result_footer_service.application_fact(app, "Record"),
                    result_footer_service.fact("Owner: none recorded", missing=True),
                ]),
            })
        if (stage or "").casefold() == "sunset":
            amount, source = get_run_cost_with_source(app, on_system.get(app.id, []))
            if amount:
                cost.append({
                    "_amount": amount,
                    "category": "Cost",
                    "title": "Plan the retirement of %s" % app.name,
                    "reason": "It is in its sunset stage and still costs %s a year (%s)." % (
                        _money(amount), source),
                    "severity": None,
                    "record_name": app.name,
                    "record_href": href,
                    "record_label": _RECORD_LABELS["Cost"],
                    "footer": result_footer_service.build_footer([
                        result_footer_service.application_fact(app, "Record"),
                        result_footer_service.fact("Run cost: %s a year, %s" % (_money(amount), source),
                                                   href=href, subject="Cost", as_of=app.updated_at or app.created_at),
                    ]),
                })
    cost.sort(key=lambda r: (-r["_amount"], r["record_name"].lower()))
    for row in cost:
        del row["_amount"]
    return ownership + cost


def _money(amount) -> str:
    from app.modules.dashboard.services.overview_figures import _money as money

    return money(amount)


def recommendations(organization_id: int, limit: Optional[int] = LIMIT) -> Dict[str, Any]:
    """The ranked recommendations (at most ``limit``), their count before the cut, and the ranking rule."""
    rows = _from_actions(organization_id) + _advice_rows(organization_id)
    for position, row in enumerate(rows, start=1):
        row["position"] = position
    return {"rows": rows[:limit] if limit else rows, "total": len(rows), "rule": RANKING_RULE}
