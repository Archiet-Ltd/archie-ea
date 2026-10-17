"""
Procurement Services (NS-010, NS-011)

Business logic for procurement features including contract management
and renewal tracking.

ADR Reference: docs/adr/0010-procurement-persona.md
"""

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, func, or_

from app import db
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
    """One stated amount, in the currency it was recorded in, read-only.

    The shape ``currency_service.convert_total`` reads from a fact: an amount,
    its currency, and the date whose rate applies. Nothing here converts.
    """

    __slots__ = ("amount", "currency", "period_start", "period_end")

    def __init__(self, amount, currency, as_of):
        self.amount = amount
        self.currency = currency
        self.period_start = None
        self.period_end = as_of


def _currency_of(contract: VendorContract) -> str:
    """The currency as stored on the contract (the column's own default when unset)."""
    return (contract.currency or "USD").strip().upper()


def _currency_shape(stated, reporting_currency: str, as_of: date,
                    rate_cache: Optional[dict] = None) -> Dict[str, any]:
    """One figure in each currency it was stated in, plus one converted figure.

    ``stated`` is a list of (amount, currency); an amount that is None was not
    stated and is left out and counted. The stated sums are kept per currency
    and are never added to each other; the only conversion is
    ``convert_total``, which answers None (and names each currency and date
    with no rate) rather than a partial sum. ``rate_cache`` is shared across
    the figures of one request so a rate is looked up once.
    """
    from app.services.currency_service import convert_total

    present = [(amount, currency) for amount, currency in stated if amount is not None]
    by_currency: Dict[str, float] = {}
    for amount, currency in present:
        by_currency[currency] = by_currency.get(currency, 0) + amount
    converted = convert_total(
        [_StatedAmount(amount, currency, as_of) for amount, currency in present],
        reporting_currency,
        rate_cache,
    )
    return {
        "by_currency": dict(sorted(by_currency.items())),
        "converted": converted.amount,
        "missing_rates": list(converted.missing),
        "left_out": len(stated) - len(present),
    }


def _empty_spend_summary(reporting_currency: Optional[str] = None) -> Dict[str, any]:
    empty = {"by_currency": {}, "converted": None, "missing_rates": []}
    return {
        "reporting_currency": reporting_currency,
        "total_contracts": 0,
        "total": dict(empty, contracts_without_annual_cost=0),
        "total_contract_value": dict(empty, contracts_without_contract_value=0),
        "by_type": {},
        "by_vendor": [],
        "by_contract": [],
    }


def _covered_applications(organization_id: int, contracts) -> Dict[int, List[Tuple[int, str]]]:
    """(id, name) of the applications each contract covers, de-duplicated by id.

    Names are not unique, so identity is the id; the name is for display only.

    Reads the contract-to-application links and the contract's legacy single
    application, in two organisation-scoped queries for all contracts at once.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.contract_application import ContractApplication

    contract_ids = [c.id for c in contracts]
    ids_by_contract: Dict[int, List[int]] = {c.id: [] for c in contracts}
    if not contract_ids:
        return {}

    links = ContractApplication.query.filter(
        ContractApplication.organization_id == organization_id,
        ContractApplication.contract_id.in_(contract_ids),
    ).all()
    for link in links:
        ids_by_contract[link.contract_id].append(link.application_id)
    for c in contracts:
        if c.application_id is not None:
            ids_by_contract[c.id].append(c.application_id)

    wanted = {app_id for ids in ids_by_contract.values() for app_id in ids}
    names = {}
    if wanted:
        rows = db.session.execute(
            db.select(ApplicationComponent.id, ApplicationComponent.name).where(
                ApplicationComponent.organization_id == organization_id,
                ApplicationComponent.id.in_(wanted),
            )
        ).all()
        names = {row.id: row.name for row in rows}

    covered: Dict[int, List[Tuple[int, str]]] = {}
    for contract_id, ids in ids_by_contract.items():
        seen, ordered = set(), []
        for app_id in ids:
            if app_id in names and app_id not in seen:
                seen.add(app_id)
                ordered.append(app_id)
        covered[contract_id] = [(i, names[i]) for i in ordered]
    return covered


def _vendor_names(vendor_ids) -> Dict[int, str]:
    from app.models.vendor.vendor_organization import VendorOrganization

    if not vendor_ids:
        return {}
    # tenant-scoping-ok: the shared vendor master is read only through the ids on this organisation's own contracts
    rows = db.session.execute(
        db.select(VendorOrganization.id, VendorOrganization.name).where(
            VendorOrganization.id.in_(vendor_ids)
        )
    ).all()
    return {row.id: row.name for row in rows}


def _rank_key(row) -> Tuple[int, float]:
    """Converted amount first, then the largest single-currency amount, then nothing stated."""
    if row["converted"] is not None:
        return (2, float(row["converted"]))
    if row["by_currency"]:
        return (1, float(max(row["by_currency"].values())))
    return (0, 0.0)


def get_spend_summary(organization_id: Optional[int], as_of: Optional[date] = None) -> Dict[str, any]:
    """Annual spend of one organisation's active contracts, by vendor and by contract.

    Every amount is kept in the currency it was recorded in and is also
    converted to the organisation's reporting currency at the rate in effect on
    ``as_of`` (today when omitted). A figure whose rate is not on file is None
    with the missing (currency, date) pairs named; two currencies are never
    added together. A contract with no annual cost is left out of the sums and
    counted. With no organisation the empty shape comes back.
    """
    if organization_id is None:
        return _empty_spend_summary()

    from app.modules.intelligence.services.query_catalogue import truth_class_for
    from app.services.application_cost_accessor import get_reporting_currency

    as_of = as_of or date.today()
    reporting = get_reporting_currency(organization_id)
    contracts = (
        VendorContract.query.filter(
            VendorContract.organization_id == organization_id, VendorContract.status == "active"
        )
        .order_by(VendorContract.contract_name.asc(), VendorContract.id.asc())
        .all()
    )
    if not contracts:
        return _empty_spend_summary(reporting)

    amounts = {c.id: get_contract_amounts(c) for c in contracts}
    annual = [(amounts[c.id]["annual_cost"], _currency_of(c)) for c in contracts]
    stated_value = [(amounts[c.id]["contract_value"], _currency_of(c)) for c in contracts]
    apps = _covered_applications(organization_id, contracts)
    vendor_names = _vendor_names({c.vendor_id for c in contracts if c.vendor_id})

    rate_cache: dict = {}

    def shape_of(group, key="annual_cost"):
        return _currency_shape(
            [(amounts[c.id][key], _currency_of(c)) for c in group], reporting, as_of, rate_cache
        )

    total_annual = _currency_shape(annual, reporting, as_of, rate_cache)
    total_value = _currency_shape(stated_value, reporting, as_of, rate_cache)

    by_type: Dict[str, any] = {}
    groups: Dict[str, list] = {}
    for c in contracts:
        groups.setdefault(c.contract_type or "unknown", []).append(c)
    for contract_type, group in groups.items():
        shape = shape_of(group)
        by_type[contract_type] = {
            "count": len(group),
            # The converted figure, or None while a rate is missing: never a
            # sum across currencies.
            "annual_cost": shape["converted"],
            "converted": shape["converted"],
            "by_currency": shape["by_currency"],
            "missing_rates": shape["missing_rates"],
            "contracts_without_annual_cost": shape["left_out"],
        }

    vendor_groups: Dict[int, list] = {}
    for c in contracts:
        if c.vendor_id in vendor_names:
            vendor_groups.setdefault(c.vendor_id, []).append(c)
    by_vendor = []
    for vendor_id, group in vendor_groups.items():
        shape = shape_of(group)
        covered = {app_id for c in group for app_id, _name in apps.get(c.id, [])}
        end_dates = [c.end_date for c in group if c.end_date is not None]
        by_vendor.append({
            "vendor_id": vendor_id,
            "vendor_name": vendor_names[vendor_id],
            "contract_count": len(group),
            "by_currency": shape["by_currency"],
            "converted": shape["converted"],
            "missing_rates": shape["missing_rates"],
            "contracts_without_annual_cost": shape["left_out"],
            "covered_application_count": len(covered) or None,
            "nearest_end_date": min(end_dates) if end_dates else None,
        })
    by_vendor.sort(key=lambda row: row["vendor_name"].lower())
    by_vendor.sort(key=_rank_key, reverse=True)  # stable: ties stay alphabetical

    by_contract = []
    for c in contracts:
        shape = shape_of([c])
        by_contract.append({
            "contract_id": c.id,
            "contract_name": c.contract_name,
            "vendor_name": vendor_names.get(c.vendor_id),
            "annual_cost": amounts[c.id]["annual_cost"],
            "currency": _currency_of(c),
            "converted": shape["converted"],
            "missing_rates": shape["missing_rates"],
            "end_date": c.end_date,
            "applications": [name for _id, name in apps.get(c.id, [])],
            "source_table": VendorContract.__tablename__,
            "truth_class": truth_class_for(VendorContract.__tablename__),
        })

    return {
        "reporting_currency": reporting,
        "total_contracts": len(contracts),
        "total": {
            "by_currency": total_annual["by_currency"],
            "converted": total_annual["converted"],
            "missing_rates": total_annual["missing_rates"],
            "contracts_without_annual_cost": total_annual["left_out"],
        },
        "total_contract_value": {
            "by_currency": total_value["by_currency"],
            "converted": total_value["converted"],
            "missing_rates": total_value["missing_rates"],
            "contracts_without_contract_value": total_value["left_out"],
        },
        "by_type": by_type,
        "by_vendor": by_vendor,
        "by_contract": by_contract,
    }


def get_spend_by_category(organization_id: Optional[int]) -> Dict[str, Dict[str, any]]:
    """Contract value of one organisation by contract_category, highest first.

    Each category carries the value in every currency it was stated in and one
    converted figure (None while a rate is missing). A contract with no stated
    value is left out and counted in ``contracts_without_value``; it is never
    added as 0. Shared with the AI spend-recommendations context so both read
    one aggregate. With no organisation the answer is empty.
    """
    if organization_id is None:
        return {}

    from app.services.application_cost_accessor import get_reporting_currency

    as_of = date.today()
    reporting = get_reporting_currency(organization_id)
    contracts = VendorContract.query.filter(
        VendorContract.organization_id == organization_id
    ).all()

    rate_cache: dict = {}
    groups: Dict[str, list] = {}
    for c in contracts:
        groups.setdefault(c.contract_category or "Uncategorized", []).append(c)

    categories = {}
    for category, group in groups.items():
        shape = _currency_shape(
            [(get_contract_amounts(c)["contract_value"], _currency_of(c)) for c in group],
            reporting,
            as_of,
            rate_cache,
        )
        categories[category] = {
            "by_currency": shape["by_currency"],
            "converted": shape["converted"],
            "missing_rates": shape["missing_rates"],
            "contracts_without_value": shape["left_out"],
        }
    return dict(sorted(categories.items(), key=lambda item: _rank_key(item[1]), reverse=True))
