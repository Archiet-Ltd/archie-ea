"""Service helpers for the Business Case module.

CRUD lives here (kept thin — the model carries to_dict()); the more
interesting piece is aggregate_financials(), which pulls cost/value figures
from whichever of the three optional links (capability / strategic
initiative / solution) are set on a BusinessCase and uses them to
pre-populate blank financial fields, or report a cross-check delta for
fields the user already filled in.

Every lookup against a linked entity is wrapped defensively: a missing or
dangling link (deleted capability, bad id, etc.) is treated as "no data
from this source" rather than raising — the business case document itself
is always the source of truth, financial aggregation is best-effort.
"""

import logging
from decimal import Decimal, InvalidOperation

from flask_login import current_user

from app import db
from app.models.business_case import BUSINESS_CASE_STATUSES, BusinessCase
from app.models.cost_intelligence import CapabilityCostAllocation
from app.models.solution_models import Solution
from app.models.strategic import StrategicInitiative
from app.models.unified_capability import UnifiedCapability

logger = logging.getLogger(__name__)

# Business-case document fields exposed to the inline field-save API.
EDITABLE_FIELDS = (
    "title",
    "description",
    "status",
    "capability_id",
    "strategic_initiative_id",
    "solution_id",
    "problem_statement",
    "options_considered",
    "recommended_option",
    "expected_benefits",
    "key_risks",
    "financial_benefit_annual",
    "capex",
    "opex_annual",
    "tco_3yr",
    "roi_percentage",
    "payback_months",
)

_MONEY_FIELDS = {
    "financial_benefit_annual",
    "capex",
    "opex_annual",
    "tco_3yr",
    "roi_percentage",
}
_INT_FIELDS = {"capability_id", "strategic_initiative_id", "solution_id", "payback_months"}


def _to_decimal(value):
    """Best-effort coerce a raw number into Decimal; None passes through."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def list_business_cases():
    """Return all business cases for the current tenant, most recently updated first."""
    return BusinessCase.query.order_by(BusinessCase.updated_at.desc()).all()


def get_business_case_or_none(business_case_id):
    # A primary-key .query.get(id) does not reliably carry the ORM tenant
    # listener's WHERE clause the way a filtered query does, so a guessed id
    # belonging to another organisation would still be found. Filter
    # explicitly, the same two-layer rule applied elsewhere in this codebase.
    return BusinessCase.query.filter_by(
        id=business_case_id, organization_id=current_user.organization_id
    ).first()


def create_business_case(title, description=None, status=None, created_by_id=None, **fields):
    if status and status not in BUSINESS_CASE_STATUSES:
        raise ValueError(f"Invalid status: {status}")

    business_case = BusinessCase(
        title=title or "Untitled Business Case",
        description=description,
        status=status or "draft",
        created_by_id=created_by_id,
    )
    _apply_fields(business_case, fields)
    db.session.add(business_case)
    db.session.commit()
    return business_case


def update_business_case(business_case, **fields):
    status = fields.get("status")
    if status is not None and status not in BUSINESS_CASE_STATUSES and status != "":
        raise ValueError(f"Invalid status: {status}")
    _apply_fields(business_case, fields)
    db.session.commit()
    return business_case


def delete_business_case(business_case):
    db.session.delete(business_case)
    db.session.commit()


def save_field(business_case, field_name, value):
    """Save a single editable field (used by the inline section-save API)."""
    if field_name not in EDITABLE_FIELDS:
        raise ValueError(f"Unknown or non-editable field: {field_name}")
    if field_name == "status" and value and value not in BUSINESS_CASE_STATUSES:
        raise ValueError(f"Invalid status: {value}")
    _apply_fields(business_case, {field_name: value})
    db.session.commit()
    return business_case


def _apply_fields(business_case, fields):
    """Assign a dict of {field: raw_value} onto a BusinessCase, coercing types."""
    for field_name, value in fields.items():
        if field_name not in EDITABLE_FIELDS:
            continue
        if value is None:
            setattr(business_case, field_name, None)
            continue
        if field_name in _MONEY_FIELDS:
            setattr(business_case, field_name, _to_decimal(value))
        elif field_name in _INT_FIELDS:
            try:
                setattr(business_case, field_name, int(value) if value != "" else None)
            except (ValueError, TypeError):
                setattr(business_case, field_name, None)
        else:
            setattr(business_case, field_name, value if value != "" else None)


# ---------------------------------------------------------------------------
# Financial aggregation
# ---------------------------------------------------------------------------


# NPV engine (2 Sep 2026): a real discounted-cash-flow NPV service already
# existed (SolutionCostService.calculate_npv, solutions_strategic/v2) but was
# wired to zero routes — an orphaned capability, per the transformation-gap
# audit. That service is scoped to a Solution's multi-year cost MODEL
# (SolutionCostModel/SolutionCostYearlyProjection), a different system of
# record from BusinessCase's own capex/opex/benefit fields; repointing it here
# would answer this question from the wrong store (ADR-0008). This computes a
# genuine NPV from BusinessCase's own live fields instead.
#
# Computed on read, never stored: capex/opex/benefit can each be edited
# independently via save_field(), so a stored NPV would silently go stale the
# moment any one of them changed. Recomputing at serialization time (to_dict)
# means it is never wrong, at the cost of a few float ops per read — cheap.
NPV_DISCOUNT_RATE = Decimal("0.10")  # 10% — a standard corporate hurdle rate
NPV_HORIZON_YEARS = 3  # matches tco_3yr's horizon, so the two numbers agree on a timeframe


def calculate_npv(capex, opex_annual, financial_benefit_annual,
                   years=NPV_HORIZON_YEARS, rate=NPV_DISCOUNT_RATE):
    """Net present value of a business case's own capex/opex/benefit.

    NPV = -capex + sum_{t=1..years} (benefit - opex) / (1+rate)^t

    Returns None (never a fabricated number) unless at least capex or benefit
    is set — a business case with nothing costed yet has no NPV to report, not
    a zero one.
    """
    if capex is None and financial_benefit_annual is None:
        return None
    capex = _to_decimal(capex) or Decimal("0")
    opex_annual = _to_decimal(opex_annual) or Decimal("0")
    benefit = _to_decimal(financial_benefit_annual) or Decimal("0")
    net_annual = benefit - opex_annual
    npv = -capex
    for year in range(1, years + 1):
        npv += net_annual / ((Decimal("1") + rate) ** year)
    return npv


def aggregate_financials(business_case, apply_missing=True):
    """Pull cost/value data from the linked StrategicInitiative,
    CapabilityCostAllocation (+ UnifiedCapability), and Solution — and use it
    to pre-populate blank capex/opex/tco/roi/benefit fields on *business_case*.

    Fields the user has already filled in are left untouched; instead a
    cross-check delta (current vs. suggested) is reported so a reviewer can
    see where the manually-entered numbers diverge from linked data.

    Returns a report dict:
        {
          "strategic_initiative": {...} | None,
          "capability_cost_allocation": {...} | None,
          "unified_capability": {...} | None,
          "solution": {...} | None,
          "suggested": {capex, opex_annual, tco_3yr, roi_percentage,
                        financial_benefit_annual},
          "applied_fields": [field names actually written],
          "cross_checks": {field: {"current": x, "suggested": y}},
        }

    Does NOT commit — caller decides whether/when to persist.
    """
    report = {
        "strategic_initiative": None,
        "capability_cost_allocation": None,
        "unified_capability": None,
        "solution": None,
        "suggested": {},
        "applied_fields": [],
        "cross_checks": {},
    }

    capex_candidates = []
    opex_candidates = []
    benefit_candidates = []
    roi_candidates = []

    # ---- StrategicInitiative -------------------------------------------------
    if business_case.strategic_initiative_id:
        initiative = _safe_get(StrategicInitiative, business_case.strategic_initiative_id)
        if initiative is not None:
            report["strategic_initiative"] = {
                "id": initiative.id,
                "name": initiative.name,
                "budget_allocated": initiative.budget_allocated,
                "budget_spent": initiative.budget_spent,
                "business_value_score": initiative.business_value_score,
            }
            if initiative.budget_allocated:
                capex_candidates.append(_to_decimal(initiative.budget_allocated))
            # NOTE: budget_spent is cumulative spend-to-date, NOT an annual
            # operating cost, so it must not feed opex_annual. It stays in the
            # report above for informational cross-checking only.

    # ---- CapabilityCostAllocation (+ UnifiedCapability best-effort) ---------
    if business_case.capability_id:
        allocation = _safe_latest_allocation(business_case.capability_id)
        if allocation is not None:
            try:
                total_cost = allocation.calculate_total_cost()
            except Exception:
                logger.exception(
                    "aggregate_financials: calculate_total_cost failed for allocation %s",
                    allocation.id,
                )
                total_cost = None
            try:
                roi = allocation.calculate_roi_percentage()
            except Exception:
                logger.exception(
                    "aggregate_financials: calculate_roi_percentage failed for allocation %s",
                    allocation.id,
                )
                roi = None
            report["capability_cost_allocation"] = {
                "allocation_id": allocation.id,
                "fiscal_year": allocation.fiscal_year,
                "total_cost": float(total_cost) if total_cost is not None else None,
                "business_value_generated": (
                    float(allocation.business_value_generated)
                    if allocation.business_value_generated is not None
                    else None
                ),
                "roi_percentage": roi,
            }
            if total_cost:
                opex_candidates.append(_to_decimal(total_cost))
            if allocation.business_value_generated:
                benefit_candidates.append(_to_decimal(allocation.business_value_generated))
            if roi is not None:
                roi_candidates.append(_to_decimal(roi))

        # UnifiedCapability has its own id space, so business_case.capability_id
        # (a business_capability id) names the projected row only through its
        # provenance (ADR 0008), never through a coincidentally equal primary key.
        unified = _safe_projected_capability(business_case.capability_id)
        if unified is not None:
            report["unified_capability"] = {
                "id": unified.id,
                "name": unified.name,
                "annual_cost": unified.annual_cost,
                "roi_percentage": unified.roi_percentage,
            }
            if unified.annual_cost:
                opex_candidates.append(_to_decimal(unified.annual_cost))
            if unified.roi_percentage is not None:
                roi_candidates.append(_to_decimal(unified.roi_percentage))

    # ---- Solution --------------------------------------------------------
    if business_case.solution_id:
        solution = _safe_get(Solution, business_case.solution_id)
        if solution is not None:
            report["solution"] = {
                "id": solution.id,
                "name": solution.name,
                "estimated_cost": (
                    float(solution.estimated_cost) if solution.estimated_cost is not None else None
                ),
                "actual_cost": (
                    float(solution.actual_cost) if solution.actual_cost is not None else None
                ),
                "roi_percentage": solution.roi_percentage,
            }
            if solution.actual_cost:
                capex_candidates.append(_to_decimal(solution.actual_cost))
            elif solution.estimated_cost:
                capex_candidates.append(_to_decimal(solution.estimated_cost))
            if solution.roi_percentage is not None:
                roi_candidates.append(_to_decimal(solution.roi_percentage))

    # ---- Combine candidates into suggested values ------------------------
    capex_candidates = [c for c in capex_candidates if c is not None]
    opex_candidates = [c for c in opex_candidates if c is not None]
    benefit_candidates = [c for c in benefit_candidates if c is not None]
    roi_candidates = [c for c in roi_candidates if c is not None]

    suggested_capex = max(capex_candidates) if capex_candidates else None
    suggested_opex = max(opex_candidates) if opex_candidates else None
    suggested_benefit = sum(benefit_candidates) if benefit_candidates else None
    suggested_roi = (
        sum(roi_candidates) / len(roi_candidates) if roi_candidates else None
    )
    suggested_tco = None
    if suggested_capex is not None or suggested_opex is not None:
        suggested_tco = (suggested_capex or Decimal("0")) + Decimal("3") * (
            suggested_opex or Decimal("0")
        )

    field_map = {
        "capex": suggested_capex,
        "opex_annual": suggested_opex,
        "tco_3yr": suggested_tco,
        "roi_percentage": suggested_roi,
        "financial_benefit_annual": suggested_benefit,
    }
    report["suggested"] = {
        k: (float(v) if v is not None else None) for k, v in field_map.items()
    }

    for field_name, value in field_map.items():
        if value is None:
            continue
        current = getattr(business_case, field_name)
        if current is None:
            if apply_missing:
                setattr(business_case, field_name, value)
                report["applied_fields"].append(field_name)
        else:
            report["cross_checks"][field_name] = {
                "current": float(current),
                "suggested": float(value),
            }

    return report


def refresh_financials_from_links(business_case):
    """aggregate_financials() + commit — used by the 'pull financials' action."""
    report = aggregate_financials(business_case, apply_missing=True)
    db.session.commit()
    return report


def _safe_get(model, pk):
    try:
        return model.query.get(pk)
    except Exception:
        logger.exception("aggregate_financials: lookup failed for %s id=%s", model.__name__, pk)
        return None


def _safe_projected_capability(capability_id):
    from app.commands.project_capabilities import SOURCE_TABLE

    try:
        return UnifiedCapability.query.filter_by(
            source_table=SOURCE_TABLE, source_id=str(capability_id)
        ).first()
    except Exception:
        logger.exception(
            "aggregate_financials: UnifiedCapability lookup failed for business_capability id=%s",
            capability_id,
        )
        return None


def _safe_latest_allocation(capability_id):
    try:
        return (
            CapabilityCostAllocation.query.filter_by(capability_id=capability_id)
            .order_by(
                CapabilityCostAllocation.fiscal_year.desc(),
                CapabilityCostAllocation.fiscal_quarter.desc(),
            )
            .first()
        )
    except Exception:
        logger.exception(
            "aggregate_financials: CapabilityCostAllocation lookup failed for capability_id=%s",
            capability_id,
        )
        return None
