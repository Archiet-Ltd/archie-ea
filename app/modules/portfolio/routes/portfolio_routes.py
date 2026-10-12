"""Portfolio views: the initiative list and the initiative detail.

Every figure here is either read from the database or rendered as an em dash.
Nothing is defaulted to 0 — a spend of zero and a spend nobody recorded are
different facts, and conflating them is what made the rest of this platform's
dashboards untrustworthy (see the fabricated-data gate).

Tenant scoping is implicit: EnterpriseInitiative, Benefit, Demand and Assumption
all carry TenantMixin, so do_orm_execute injects the organisation predicate.
"""
import logging

from flask import Blueprint, abort, render_template
from flask_login import current_user, login_required

from app import db

logger = logging.getLogger(__name__)

portfolio_bp = Blueprint("portfolio", __name__, url_prefix="/portfolio")

# The write paths (demand intake, benefit measurement, assumption resolution)
# attach to this blueprint from portfolio_write_routes. Imported at the bottom of
# this module, after portfolio_bp exists, so the decorators can bind.


def _money(value):
    """Decimal -> float for the template, preserving None as None (renders —)."""
    return float(value) if value is not None else None


def _variance(initiative):
    """Spend against approved budget, as a percentage.

    Returns None when either side is missing rather than implying 0% variance
    against a budget nobody set.
    """
    budget, spent = initiative.approved_budget, initiative.spent_to_date
    if budget is None or spent is None or float(budget) == 0:
        return None
    return round((float(spent) - float(budget)) / float(budget) * 100, 1)


@portfolio_bp.route("/")
@login_required
def index():
    """Initiative list — the portfolio view a steering committee asks for."""
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    initiatives = (
        db.session.query(EnterpriseInitiative)
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

    return render_template("portfolio/index.html", initiatives=rows, summary=summary)


@portfolio_bp.route("/<int:initiative_id>")
@login_required
def detail(initiative_id):
    """One initiative: money, benefits, RAID, and what it delivers."""
    from app.models.strategic import StrategicInitiative
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    initiative = db.session.get(EnterpriseInitiative, initiative_id)
    if initiative is None:
        abort(404)

    benefits = sorted(initiative.benefits, key=lambda b: (b.target_date is None, b.target_date))
    assumptions = sorted(
        initiative.assumptions, key=lambda a: (a.exposure is None, -(a.exposure or 0))
    )
    demands = list(initiative.demands)
    transformation_programmes = (
        db.session.query(StrategicInitiative)
        .filter(
            StrategicInitiative.organization_id == current_user.organization_id,
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

    return render_template(
        "portfolio/detail.html",
        initiative=initiative,
        variance_pct=_variance(initiative),
        approved_budget=_money(initiative.approved_budget),
        spent_to_date=_money(initiative.spent_to_date),
        forecast_cost=_money(initiative.forecast_cost),
        benefits=benefits,
        benefit_target=benefit_target,
        benefit_actual=benefit_actual,
        non_financial_count=len(benefits) - len(financial),
        assumptions=assumptions,
        demands=demands,
        work_packages=list(initiative.migration_work_packages),
        transformation_programmes=transformation_programmes,
        linked_programme=(
            db.session.get(StrategicInitiative, initiative.linked_strategic_initiative_id)
            if initiative.linked_strategic_initiative_id else None
        ),
    )


# Bind the write paths to this blueprint (see note beside portfolio_bp above).
from app.modules.portfolio.routes import portfolio_write_routes  # noqa: E402,F401
from app.modules.portfolio.routes import backlog_routes  # noqa: E402,F401
