"""
Procurement Services (NS-010, NS-011)

Business logic for procurement features including contract management
and renewal tracking.

ADR Reference: docs/adr/0010-procurement-persona.md
"""

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import joinedload

from app.models.application_portfolio import VendorContract


def get_days_until_renewal(contract: VendorContract) -> Optional[int]:
    """Calculate days until contract renewal/expiration."""
    target_date = contract.renewal_date or contract.end_date
    if not target_date:
        return None
    return (target_date - date.today()).days


def get_renewal_urgency(contract: VendorContract) -> str:
    """
    Determine renewal urgency level based on days remaining.

    Returns: 'critical' (<30 days), 'warning' (<90 days),
             'upcoming' (<180 days), 'ok' (>180 days), 'expired', 'unknown'
    """
    days = get_days_until_renewal(contract)
    if days is None:
        return "unknown"
    if days < 0:
        return "expired"
    if days < 30:
        return "critical"
    if days < 90:
        return "warning"
    if days < 180:
        return "upcoming"
    return "ok"


def get_contracts_list(
    status: Optional[str] = None,
    vendor_id: Optional[int] = None,
    urgency: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
) -> Tuple[List[VendorContract], int]:
    """
    Get paginated list of contracts with optional filters.

    Returns: (contracts, total_count)
    """
    query = VendorContract.query

    # Apply filters
    if status:
        query = query.filter(VendorContract.status == status)
    if vendor_id:
        query = query.filter(VendorContract.vendor_id == vendor_id)
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                VendorContract.contract_name.ilike(search_term),
                VendorContract.contract_number.ilike(search_term),
            )
        )

    # Order by renewal date (soonest first), then by name
    query = query.order_by(
        VendorContract.renewal_date.asc().nullslast(),
        VendorContract.contract_name.asc(),
    )

    total = query.count()
    contracts = query.offset((page - 1) * per_page).limit(per_page).all()

    # Filter by urgency if specified (post-query since it's calculated)
    if urgency:
        contracts = [c for c in contracts if get_renewal_urgency(c) == urgency]

    return contracts, total


def get_expiring_contracts(days: int = 90) -> List[VendorContract]:
    """Get contracts expiring within the specified number of days."""
    cutoff = date.today() + timedelta(days=days)

    return (
        VendorContract.query
        .filter(VendorContract.status == "active")
        .filter(
            or_(
                and_(
                    VendorContract.renewal_date.isnot(None),
                    VendorContract.renewal_date <= cutoff,
                ),
                and_(
                    VendorContract.renewal_date.is_(None),
                    VendorContract.end_date.isnot(None),
                    VendorContract.end_date <= cutoff,
                ),
            )
        )
        .order_by(
            func.coalesce(VendorContract.renewal_date, VendorContract.end_date).asc()
        )
        .all()
    )


def get_renewal_summary() -> Dict[str, int]:
    """Get summary counts by renewal urgency."""
    contracts = VendorContract.query.filter(VendorContract.status == "active").all()

    summary = {
        "critical": 0,  # <30 days
        "warning": 0,   # <90 days
        "upcoming": 0,  # <180 days
        "ok": 0,        # >180 days
        "expired": 0,
        "unknown": 0,
        "total": len(contracts),
    }

    for contract in contracts:
        urgency = get_renewal_urgency(contract)
        summary[urgency] = summary.get(urgency, 0) + 1

    return summary


def get_contract_amounts(contract: VendorContract) -> Dict[str, Optional[float]]:
    """The amounts stated on one contract, exactly as stored.

    An amount nobody entered is None, and stays None. The contracts register
    and the spend totals both read a contract's amounts through this function,
    so one contract cannot be a stated figure on one screen and a missing one
    on the other. A stated zero is an amount; only None is missing.
    """
    return {
        "contract_value": contract.contract_value,
        "annual_cost": contract.annual_cost,
    }


def total_of_stated(amounts) -> Tuple[Optional[float], int]:
    """Add up the amounts that were stated and count the ones that were not.

    Returns (total, left_out). The total is None when no amount was stated, so
    a screen can show an em dash instead of a zero nobody entered; left_out is
    how many amounts were not added, for the line under the total that says so.
    """
    amounts = list(amounts)
    stated = [a for a in amounts if a is not None]
    return (sum(stated) if stated else None), len(amounts) - len(stated)


class _StatedAmount:
    """A stated amount in the currency it was recorded in, in the shape
    ``convert_total`` reads. The rate in effect on ``period_end`` is used."""

    __slots__ = ("amount", "currency", "period_start", "period_end")

    def __init__(self, amount, currency, as_of):
        self.amount = amount
        self.currency = currency
        self.period_start = None
        self.period_end = as_of


def _currency_of(contract: VendorContract) -> str:
    return (contract.currency or "").strip().upper()


def _spend_shape(items, reporting_currency: str, as_of: date) -> Dict[str, any]:
    """One figure for a group of contracts: the stated sums per currency and one
    converted amount in the reporting currency.

    ``items`` are ``(amount, currency)`` pairs, stated amounts only. Amounts are
    added only within one currency; the only place two currencies meet is
    ``convert_total``, which returns no amount when any rate is missing. An
    amount with no currency recorded cannot be converted: it is left out of
    both sums and counted in ``without_currency``.
    """
    from decimal import Decimal

    from app.services.currency_service import convert_total

    by_currency: Dict[str, Decimal] = {}
    convertible = []
    without_currency = 0
    for amount, currency in items:
        if not currency:
            without_currency += 1
            continue
        by_currency[currency] = by_currency.get(currency, Decimal(0)) + Decimal(str(amount))
        convertible.append(_StatedAmount(Decimal(str(amount)), currency, as_of))
    if convertible:
        converted = convert_total(convertible, reporting_currency)
        amount, missing = converted.amount, list(converted.missing)
    else:
        amount, missing = None, []
    return {
        "by_currency": by_currency,
        "converted": amount,
        "missing_rates": missing,
        "without_currency": without_currency,
    }


def _covered_applications(organization_id: int, contracts) -> Dict[int, List[str]]:
    """Names of the applications each contract covers: the contract-application
    links plus the contract's own application link, each application once.
    One batched read for the links and one for the names."""
    from app.models.application_portfolio import ApplicationComponent
    from app.models.contract_application import ContractApplication
    from app import db

    contract_ids = [c.id for c in contracts]
    links = {cid: [] for cid in contract_ids}
    if not contract_ids:
        return {}
    for contract_id, application_id in db.session.execute(
        db.select(ContractApplication.contract_id, ContractApplication.application_id)
        .where(ContractApplication.organization_id == organization_id)
        .where(ContractApplication.contract_id.in_(contract_ids))
        .order_by(ContractApplication.id)
    ).all():
        links[contract_id].append(application_id)
    for contract in contracts:
        if contract.application_id is not None and contract.application_id not in links[contract.id]:
            links[contract.id].append(contract.application_id)
    wanted = sorted({a for ids in links.values() for a in ids})
    names: Dict[int, str] = {}
    if wanted:
        names = dict(
            db.session.execute(
                db.select(ApplicationComponent.id, ApplicationComponent.name)
                .where(ApplicationComponent.organization_id == organization_id)
                .where(ApplicationComponent.id.in_(wanted))
            ).all()
        )
    return {cid: sorted(names[a] for a in ids if a in names) for cid, ids in links.items()}


def _empty_spend_summary(reporting_currency: Optional[str]) -> Dict[str, any]:
    empty = {"by_currency": {}, "converted": None, "missing_rates": [], "without_currency": 0}
    return {
        "reporting_currency": reporting_currency,
        "total_contracts": 0,
        "total": {**empty, "contracts_without_annual_cost": 0},
        "total_value": None,
        "total_value_detail": dict(empty),
        "total_annual_cost": None,
        "contracts_without_value": 0,
        "contracts_without_annual_cost": 0,
        "by_type": {},
        "by_vendor": [],
        "by_contract": [],
        "top_vendors": [],
        "default_currency_contracts": 0,
    }


def get_spend_summary(organization_id: Optional[int], as_of: Optional[date] = None) -> Dict[str, any]:
    """Spend by vendor and by contract for one organisation, never mixing currencies.

    Every amount is kept in the currency it was recorded in (``by_currency``) and
    converted to the organisation's reporting currency at the rate in effect on
    ``as_of`` (today by default) through ``convert_total``. A converted figure is
    None, with the missing (currency, date) pairs named, whenever a rate is not on
    file; nothing is ever summed across currencies, and an amount nobody entered
    stays out of the sum and is counted. With no organisation, the empty shape.
    """
    from app.services.application_cost_accessor import get_reporting_currency
    from app.modules.intelligence.services.query_catalogue import truth_class_for

    if organization_id is None:
        return _empty_spend_summary(None)
    as_of = as_of or date.today()
    reporting = get_reporting_currency(organization_id)
    contracts = (
        VendorContract.query.options(joinedload(VendorContract.vendor))
        .filter(VendorContract.organization_id == organization_id, VendorContract.status == "active")
        .order_by(VendorContract.id)
        .all()
    )
    amounts = [(c, get_contract_amounts(c)) for c in contracts]
    covered = _covered_applications(organization_id, contracts)

    def stated(group, key):
        return [(a[key], _currency_of(c)) for c, a in group if a[key] is not None]

    total_annual = _spend_shape(stated(amounts, "annual_cost"), reporting, as_of)
    total_value = _spend_shape(stated(amounts, "contract_value"), reporting, as_of)
    without_annual = sum(1 for _, a in amounts if a["annual_cost"] is None)
    without_value = sum(1 for _, a in amounts if a["contract_value"] is None)

    by_type_groups: Dict[str, list] = {}
    for c, a in amounts:
        by_type_groups.setdefault(c.contract_type or "unknown", []).append((c, a))
    by_type = {}
    for contract_type, group in by_type_groups.items():
        shape = _spend_shape(stated(group, "annual_cost"), reporting, as_of)
        by_type[contract_type] = {"count": len(group), "annual_cost": shape["converted"], **shape}

    by_vendor_groups: Dict[int, list] = {}
    for c, a in amounts:
        if c.vendor_id is not None and c.vendor is not None:
            by_vendor_groups.setdefault(c.vendor_id, []).append((c, a))
    by_vendor = []
    for group in by_vendor_groups.values():
        shape = _spend_shape(stated(group, "annual_cost"), reporting, as_of)
        applications = sorted({name for c, _ in group for name in covered.get(c.id, [])})
        ends = [c.end_date for c, _ in group if c.end_date is not None]
        by_vendor.append({
            "vendor_name": group[0][0].vendor.name,
            "count": len(group),
            "annual_cost": shape["converted"],
            **shape,
            "contracts_without_annual_cost": sum(1 for _, a in group if a["annual_cost"] is None),
            "covered_applications": applications,
            "nearest_end_date": min(ends) if ends else None,
        })

    def vendor_rank(row):
        largest = max(row["by_currency"].values()) if row["by_currency"] else None
        return (
            row["converted"] is not None,
            row["converted"] if row["converted"] is not None else 0,
            largest is not None,
            largest if largest is not None else 0,
        )

    by_vendor.sort(key=vendor_rank, reverse=True)

    by_contract = []
    for c, a in amounts:
        shape = _spend_shape(stated([(c, a)], "annual_cost"), reporting, as_of)
        by_contract.append({
            "contract_id": c.id,
            "contract_name": c.contract_name,
            "vendor_name": c.vendor.name if c.vendor is not None else None,
            "annual_cost": a["annual_cost"],
            "currency": _currency_of(c) or None,
            "converted": shape["converted"],
            "missing_rates": shape["missing_rates"],
            "end_date": c.end_date,
            "covered_applications": covered.get(c.id, []),
            "source_table": VendorContract.__tablename__,
            "truth_class": truth_class_for(VendorContract.__tablename__),
        })
    by_contract.sort(
        key=lambda r: (r["converted"] is not None, r["converted"] or 0, r["annual_cost"] is not None, r["annual_cost"] or 0),
        reverse=True,
    )

    return {
        "reporting_currency": reporting,
        "total_contracts": len(contracts),
        "total": {**total_annual, "contracts_without_annual_cost": without_annual},
        # The two totals the page and the AI context read: the converted figure,
        # or None when no amount was stated or a rate is missing.
        "total_value": total_value["converted"],
        "total_value_detail": total_value,
        "total_annual_cost": total_annual["converted"],
        "contracts_without_value": without_value,
        "contracts_without_annual_cost": without_annual,
        "by_type": by_type,
        "by_vendor": by_vendor,
        "by_contract": by_contract,
        "top_vendors": [(row["vendor_name"], row) for row in by_vendor[:10]],
        # Contracts still carrying the column default currency with no other sign
        # it was entered, reported for the product owner to rule on.
        "default_currency_contracts": sum(1 for c in contracts if _currency_of(c) == "USD"),
    }


def get_spend_by_category(organization_id: Optional[int]) -> Dict[str, Dict[str, any]]:
    """Contract value grouped by contract category, highest spend first, per currency.

    Each category carries the stated contract value per currency, its converted
    amount in the reporting currency (None when a rate is missing) and the count
    of contracts left out because they state no contract value (never added as 0).
    Shared with the AI spend recommendations through ``_build_spend_context``.
    """
    from app.services.application_cost_accessor import get_reporting_currency

    if organization_id is None:
        return {}
    reporting = get_reporting_currency(organization_id)
    contracts = (
        VendorContract.query.filter_by(organization_id=organization_id).order_by(VendorContract.id).all()
    )
    groups: Dict[str, list] = {}
    for c in contracts:
        groups.setdefault(c.contract_category or "Uncategorized", []).append(c)
    out = {}
    for category, group in groups.items():
        items = [(c.contract_value, _currency_of(c)) for c in group if c.contract_value is not None]
        shape = _spend_shape(items, reporting, date.today())
        out[category] = {
            **shape,
            "count": len(group),
            "contracts_without_value": sum(1 for c in group if c.contract_value is None),
        }
    return dict(
        sorted(
            out.items(),
            key=lambda kv: (kv[1]["converted"] is not None, kv[1]["converted"] or 0),
            reverse=True,
        )
    )
