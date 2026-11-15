"""Portfolio views: the initiative list and the initiative detail.

Every figure here is either read from the database or rendered as an em dash.
Nothing is defaulted to 0 — a spend of zero and a spend nobody recorded are
different facts, and conflating them is what made the rest of this platform's
dashboards untrustworthy (see the fabricated-data gate).

Tenant scoping is implicit: EnterpriseInitiative, Benefit, Demand and Assumption
all carry TenantMixin, so do_orm_execute injects the organisation predicate.
"""
import logging

from flask import Blueprint, abort, render_template, request
from flask_login import current_user, login_required

from app import db
from app.services.cost_visibility import ROLLUP_FIGURE_KEYS
from app.middleware.tenant_context import current_org_id
from app.services.cost_visibility import COSTS_WITHHELD, costs_visible, withhold_costs

logger = logging.getLogger(__name__)

portfolio_bp = Blueprint("portfolio", __name__, url_prefix="/portfolio")

# The write paths (demand intake, benefit measurement, assumption resolution)
# attach to this blueprint from portfolio_write_routes. Imported at the bottom of
# this module, after portfolio_bp exists, so the decorators can bind.


def _money(value):
    """Decimal -> float for the template, preserving None as None (renders —)."""
    return float(value) if value is not None else None


def _variance(initiative):
    """Forecast against approved budget, as a percentage.

    Returns None when either side is missing rather than implying 0% variance
    against a budget nobody set. A positive figure means the forecast overruns
    the budget; a negative figure means it comes in under.
    """
    budget, forecast = initiative.approved_budget, initiative.forecast_cost
    if budget is None or forecast is None or float(budget) == 0:
        return None
    return round((float(forecast) - float(budget)) / float(budget) * 100, 1)


# What a financial benefit is measured by: its three amounts, and the percentage
# of the sought improvement they work out to. All four are money, so the one cost
# rule decides who reads them. A non-financial benefit's values count something
# else and are never masked.
BENEFIT_MONEY_KEYS = ("baseline_value", "target_value", "actual_value", "realisation_percentage")


@portfolio_bp.route("/")
@login_required
def index():
    """Initiative list — the portfolio view a steering committee asks for."""
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    from app.modules.portfolio.services.initiative_goal_links import (
        initiatives_serving_no_objective,
    )

    org_id = current_org_id()
    unlinked = initiatives_serving_no_objective() if org_id is not None else []
    unlinked_ids = {i.id for i in unlinked}
    only_unlinked = request.args.get("filter") == "no-objective"
    if only_unlinked or org_id is None:
        initiatives = unlinked
    else:
        initiatives = (
            db.session.query(EnterpriseInitiative)
            .filter(EnterpriseInitiative.organization_id == org_id)
            .order_by(EnterpriseInitiative.name)
            .all()
        )

    rows = []
    total_budget = total_spent = 0.0
    have_budget = have_spent = False

    for i in initiatives:
        budget, spent = _money(i.approved_budget), _money(i.spent_to_date)
        if budget is not None:
            total_budget += budget
            have_budget = True
        if spent is not None:
            total_spent += spent
            have_spent = True
        rows.append({
            "id": i.id,
            "name": i.name,
            "code": getattr(i, "code", None),
            "status": i.status,
            "health_status": i.health_status,
            "current_phase": i.current_phase,
            "completion_percentage": i.completion_percentage,
            "approved_budget": budget,
            "spent_to_date": spent,
            "forecast_cost": _money(i.forecast_cost),
            "variance_pct": _variance(i),
            "executive_sponsor": i.executive_sponsor,
            "planned_end_date": i.planned_end_date,
            "benefit_count": len(i.benefits),
        })

    summary = {
        "count": len(rows),
        # None, not 0.0, when nothing carried a figure at all.
        "total_budget": total_budget if have_budget else None,
        "total_spent": total_spent if have_spent else None,
        "red": sum(1 for r in rows if (r["health_status"] or "").lower() == "red"),
        "amber": sum(1 for r in rows if (r["health_status"] or "").lower() in ("amber", "yellow")),
        "green": sum(1 for r in rows if (r["health_status"] or "").lower() == "green"),
        "unrated": sum(1 for r in rows if not r["health_status"]),
    }

    summary["no_objective"] = len(unlinked_ids)

    withheld = not costs_visible(current_user)
    if withheld:
        rows = withhold_costs(rows, keys=ROLLUP_FIGURE_KEYS)
        summary = withhold_costs(summary)

    return render_template(
        "portfolio/index.html",
        costs_withheld=withheld,
        costs_withheld_sentence=COSTS_WITHHELD,
        initiatives=rows,
        summary=summary,
        only_unlinked=only_unlinked,
        footer=_list_footer(initiatives, summary, only_unlinked),
    )


def _list_footer(initiatives, summary, only_unlinked):
    """Where the list's figures come from: the initiatives counted, and the ones serving no objective."""
    from app.services import result_footer_service as footers

    stamps = [i.updated_at or i.created_at for i in initiatives if i.updated_at or i.created_at]
    recorded = max(stamps) if stamps else None
    count = summary["count"]
    unlinked = summary["no_objective"]
    return footers.summary_footer([
        footers.fact(
            "Initiatives shown: %s" % (count if count else "none recorded"),
            href=footers.page_link("portfolio.index") if only_unlinked else None,
            source_kind="manual", subject="Initiatives", as_of=recorded, missing=not count),
        footers.fact(
            "Serving no objective: %s" % unlinked,
            href=footers.page_link("portfolio.index", filter="no-objective") if unlinked and not only_unlinked else None,
            source_kind="derived", subject="Objectives", as_of=recorded),
    ])


@portfolio_bp.route("/<int:initiative_id>")
@login_required
def detail(initiative_id):
    """One initiative: money, benefits, RAID, and what it delivers."""
    from app.models.strategic import StrategicInitiative
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    org_id = current_org_id()
    if org_id is None:
        abort(404)
    initiative = db.session.execute(
        db.select(EnterpriseInitiative).where(
            EnterpriseInitiative.id == initiative_id,
            EnterpriseInitiative.organization_id == org_id,
        )
    ).scalar_one_or_none()
    if initiative is None:
        abort(404)

    benefits = sorted(initiative.benefits, key=lambda b: (b.target_date is None, b.target_date))
    assumptions = sorted(
        initiative.assumptions, key=lambda a: (a.exposure is None, -(a.exposure or 0))
    )
    demands = list(initiative.demands)
    from app.models.motivation import Goal
    from app.modules.portfolio.services.initiative_goal_links import (
        CONTRIBUTION_LEVELS,
        InitiativeGoalLinkError,
        list_links,
        list_programme_goals,
    )

    try:
        goal_links = list_links(initiative.id)
        programme_goals = list_programme_goals(initiative.id)
    except InitiativeGoalLinkError:
        abort(404)
    linked_goal_ids = {g["goal_id"] for g in goal_links}
    available_goals = [
        g
        for g in db.session.execute(
            db.select(Goal)
            .where(Goal.organization_id == initiative.organization_id)
            .order_by(Goal.name)
            .limit(500)
        ).scalars()
        if g.id not in linked_goal_ids
    ]
    transformation_programmes = (
        db.session.query(StrategicInitiative)
        .filter(
            StrategicInitiative.organization_id == initiative.organization_id,
            StrategicInitiative.record_kind == "transformation_programme",
        )
        .order_by(StrategicInitiative.name)
        .all()
    )

    # Financial benefits only — summing a monetary saving with an NPS point is
    # meaningless, so the two are reported separately.
    financial = [b for b in benefits if b.is_financial]
    benefit_target = sum(
        float(b.target_delta) for b in financial if b.target_delta is not None
    ) or None
    benefit_actual = sum(
        float(b.actual_delta) for b in financial if b.actual_delta is not None
    ) or None

    money = {
        "variance_pct": _variance(initiative),
        "approved_budget": _money(initiative.approved_budget),
        "spent_to_date": _money(initiative.spent_to_date),
        "forecast_cost": _money(initiative.forecast_cost),
    }
    withheld = not costs_visible(current_user)
    rows = [b.to_dict() for b in benefits]
    if withheld:
        money = withhold_costs(money, keys=tuple(money))
        # A financial benefit's amounts are money, so the one masker takes them
        # out here rather than the template choosing not to print them: a
        # withheld person is never sent a figure the cost rule keeps from them.
        # A non-financial benefit's values count something else and are left
        # exactly as they were.
        benefits = [
            withhold_costs(row, keys=BENEFIT_MONEY_KEYS) if row["is_financial"] else row
            for row in rows
        ]
    else:
        benefits = rows

    return render_template(
        "portfolio/detail.html",
        costs_withheld=withheld,
        costs_withheld_sentence=COSTS_WITHHELD,
        initiative=initiative,
        variance_pct=money["variance_pct"],
        approved_budget=money["approved_budget"],
        spent_to_date=money["spent_to_date"],
        forecast_cost=money["forecast_cost"],
        benefits=benefits,
        benefit_target=None if withheld else benefit_target,
        benefit_actual=None if withheld else benefit_actual,
        non_financial_count=len(benefits) - len(financial),
        assumptions=assumptions,
        demands=demands,
        goal_links=goal_links,
        programme_goals=programme_goals,
        available_goals=available_goals,
        contribution_levels=CONTRIBUTION_LEVELS,
        work_packages=list(initiative.migration_work_packages),
        transformation_programmes=transformation_programmes,
        linked_programme=(
            db.session.get(StrategicInitiative, initiative.linked_strategic_initiative_id)
            if initiative.linked_strategic_initiative_id else None
        ),
    )


# Bind the write paths to this blueprint (see note beside portfolio_bp above).
from app.modules.portfolio.routes import portfolio_write_routes  # noqa: E402,F401
