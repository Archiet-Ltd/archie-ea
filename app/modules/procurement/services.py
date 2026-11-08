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
    from app.services.vendor_register import renewal_date_of

    target_date = renewal_date_of(contract)
    if not target_date:
        return None
    return (target_date - date.today()).days


def get_renewal_urgency(contract: VendorContract) -> str:
    """
    Determine renewal urgency level based on days remaining, by the one banding the vendor pages share.

    Returns: 'critical' (up to 30 days), 'warning' (up to 90), 'upcoming' (up to 180), 'ok' (later),
             'expired', 'unknown'
    """
    from app.services.vendor_register import renewal_band

    return renewal_band(get_days_until_renewal(contract))


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
    from app.services.vendor_register import active_condition

    cutoff = date.today() + timedelta(days=days)

    return (
        VendorContract.query
        .filter(active_condition())
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


def get_renewal_summary(organization_id: int) -> Dict[str, int]:
    """Summary counts by renewal urgency, for one organisation's active contracts (the contract ledger's one
    definition). A contract still marked active whose end date has passed is not active: it is counted under
    "expired" only, never in the total."""
    from app.services.vendor_register import is_active

    contracts = VendorContract.query.filter(
        VendorContract.organization_id == organization_id, VendorContract.status == "active").all()
    active = [c for c in contracts if is_active(c)]

    summary = {
        "critical": 0,  # <30 days
        "warning": 0,   # <90 days
        "upcoming": 0,  # <180 days
        "ok": 0,        # >180 days
        "expired": len(contracts) - len(active),
        "unknown": 0,
        "total": len(active),
    }

    for contract in active:
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


def get_spend_summary(organization_id: int) -> Dict[str, any]:
    """Spend analytics summary for one organisation.

    Every figure (the totals, the counts, the vendors and the types) is computed
    from that organisation's active contracts and no other's. Totals add only
    the amounts that were stated. total_value and total_annual_cost are None
    when no active contract states one, and contracts_without_value /
    contracts_without_annual_cost count the active contracts each total leaves out.
    """
    from app.services import vendor_register

    from app.services.application_cost_accessor import get_reporting_currency

    ledger = vendor_register.contract_ledger(organization_id)
    contracts = ledger["counted"]
    amounts = [(c, get_contract_amounts(c)) for c in contracts]

    total_value, without_value = total_of_stated(a["contract_value"] for _, a in amounts)
    without_annual = sum(1 for _, a in amounts if a["annual_cost"] is None)

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

    # Group by vendor: the Vendors row's own figure, and every share is that figure over the one total
    rows = vendor_register.register(organization_id)
    counted = ledger["by_vendor"]
    total_annual = vendor_register.total_annual_cost(rows, ledger)
    by_vendor = {
        row["name"]: {"count": len(counted.get(row["id"], [])), "annual_cost": row["annual_cost"]}
        for row in rows if row["annual_cost"] is not None or row["id"] in counted
    }
    # A vendor with no stated annual cost has nothing to rank by, so it sorts after every vendor that has one.
    top_vendors = sorted(by_vendor.items(), key=lambda x: (x[1]["annual_cost"] is None, -(x[1]["annual_cost"] or 0),
                                                           x[0].lower()))[:10]

    return {
        "total_contracts": len(contracts),
        "total_value": total_value,
        "total_annual_cost": total_annual,
        "contracts_without_value": without_value,
        "contracts_without_annual_cost": without_annual,
        "by_type": by_type,
        "top_vendors": top_vendors,
        "recorded_on_systems": any("recorded" in (r["annual_cost_basis"] or "") or "systems" in (r["annual_cost_basis"] or "")
                                   for r in rows),
        "unassigned_contracts": len(ledger["unassigned"]),
        "unassigned_annual_cost": vendor_register.unassigned_cost(ledger),
        "other_currency": vendor_register.other_currency_totals(ledger),
        "other_currency_contracts": len(ledger["other_currency"]),
        "other_currency_causes": [
            vendor_register.not_added_cause(code, get_reporting_currency(organization_id))
            for code in sorted(vendor_register.other_currency_totals(ledger))
        ],
    }


def get_spend_by_category(organization_id: int) -> "Dict[str, float]":
    """Annual cost of the counted contracts grouped by contract_category, highest first.

    Reads the one contract ledger (vendor_register), so a lapsed, inactive or decommissioned-system contract
    is left out here exactly as it is on the Vendors, vendor and Spend pages. Contracts that state no annual
    cost add nothing; a contract with no category is grouped as "Uncategorized".
    """
    from app.services import vendor_register

    by_category = vendor_register.counted_cost_by_category(vendor_register.contract_ledger(organization_id))
    return dict(sorted(((("Uncategorized" if k == "uncategorised" else k), v) for k, v in by_category.items()),
                       key=lambda x: -x[1]))
