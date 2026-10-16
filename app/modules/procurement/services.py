"""
Procurement Services (NS-010, NS-011)

Business logic for procurement features including contract management
and renewal tracking.

ADR Reference: docs/adr/0010-procurement-persona.md
"""

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, func, or_

from app.models.application_portfolio import VendorContract


def get_days_until_renewal(contract: VendorContract) -> Optional[int]:
    """Calculate days until contract renewal/expiration."""
    target_date = contract.renewal_date or contract.end_date
    if not target_date:
        return None
    return (target_date - date.today()).days


def get_last_day_to_cancel(contract: VendorContract) -> Optional[date]:
    """Calculate the last day to cancel this contract.

    Last day to cancel = renewal_date - notice_period_days.
    Returns None if either renewal_date or notice_period_days is missing.
    """
    if not contract.renewal_date or contract.notice_period_days is None:
        return None
    return contract.renewal_date - timedelta(days=contract.notice_period_days)


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


def get_spend_summary() -> Dict[str, any]:
    """Get spend analytics summary.

    Totals add only the amounts that were stated. total_value and
    total_annual_cost are None when no active contract states one, and
    contracts_without_value / contracts_without_annual_cost count the active
    contracts each total leaves out.
    """
    contracts = VendorContract.query.filter(VendorContract.status == "active").all()
    amounts = [(c, get_contract_amounts(c)) for c in contracts]

    total_value, without_value = total_of_stated(a["contract_value"] for _, a in amounts)
    total_annual, without_annual = total_of_stated(a["annual_cost"] for _, a in amounts)

    # Group by contract type
    type_counts = {}
    type_costs = {}
    for c, a in amounts:
        ct = c.contract_type or "unknown"
        type_counts[ct] = type_counts.get(ct, 0) + 1
        type_costs.setdefault(ct, []).append(a["annual_cost"])
    by_type = {
        ct: {"count": type_counts[ct], "annual_cost": total_of_stated(costs)[0]}
        for ct, costs in type_costs.items()
    }

    # Group by vendor
    vendor_counts = {}
    vendor_costs = {}
    for c, a in amounts:
        if c.vendor:
            vname = c.vendor.name
            vendor_counts[vname] = vendor_counts.get(vname, 0) + 1
            vendor_costs.setdefault(vname, []).append(a["annual_cost"])
    by_vendor = {
        vname: {"count": vendor_counts[vname], "annual_cost": total_of_stated(costs)[0]}
        for vname, costs in vendor_costs.items()
    }

    # Sort by spend. A vendor with no stated annual cost has nothing to rank
    # by, so it sorts after every vendor that has one; ordering only, it is
    # never shown as a figure.
    top_vendors = sorted(
        by_vendor.items(),
        key=lambda x: (x[1]["annual_cost"] is not None, x[1]["annual_cost"] or 0),
        reverse=True
    )[:10]

    return {
        "total_contracts": len(contracts),
        "total_value": total_value,
        "total_annual_cost": total_annual,
        "contracts_without_value": without_value,
        "contracts_without_annual_cost": without_annual,
        "by_type": by_type,
        "top_vendors": top_vendors,
    }


def get_spend_by_category(organization_id: int) -> "Dict[str, float]":
    """Contract value grouped by contract_category, sorted highest spend first.

    Extracted from the inline loop that used to live only in
    routes.spend_analytics() so the AI spend-recommendations endpoint
    (procurement_ai_service.py) reuses the exact same aggregate instead of
    running a second, parallel query over the same rows - per the Task 4
    brief's explicit "reuse, not reimplement" instruction.
    """
    contracts = VendorContract.query.filter_by(organization_id=organization_id).all()

    spend_by_category: Dict[str, float] = {}
    for c in contracts:
        cat = c.contract_category or "Uncategorized"
        spend_by_category[cat] = spend_by_category.get(cat, 0) + (c.contract_value or 0)

    return dict(sorted(spend_by_category.items(), key=lambda x: -x[1]))
