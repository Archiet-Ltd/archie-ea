"""
Procurement Routes (NS-010, NS-011)

Procurement persona dashboards for contract and license management.
All queries are scoped by current user's organization_id.

ADR Reference: docs/adr/0010-procurement-persona.md
"""

from datetime import date, timedelta

from flask import render_template, request, redirect, url_for
from flask_login import current_user, login_required

from app.decorators import requires_procurement
from app.models.application_portfolio import VendorContract
from app.models.license_entitlement import LicenseEntitlement

from . import procurement_bp

# As in my_applications, services.py held the summary shapes these templates
# read and was never imported, so every dashboard raised jinja2.UndefinedError.
from .services import (
    get_contract_amounts,
    get_days_until_renewal,
    get_renewal_summary,
    get_renewal_urgency,
    get_spend_by_category,
    get_spend_summary,
)


def _compliance_summary(licenses):
    """Shape compliance_dashboard.html reads: compliant, warning, violation,
    total, total_entitled, total_consumed, utilization.

    Unlike renewals and spend there is no service equivalent, so this derives
    from the same LicenseEntitlement rows the page already renders rather than
    re-querying - one source of truth per request.
    """
    total_entitled = sum(lic.quantity_entitled or 0 for lic in licenses)
    # Utilisation is measured only over entitlements whose deployment is
    # recorded; an unrecorded one is neither "0 consumed" nor left in the
    # denominator to drag the ratio down.
    recorded = [lic for lic in licenses if lic.quantity_deployed is not None]
    total_consumed = sum(lic.quantity_deployed for lic in recorded)
    measured_entitled = sum(lic.quantity_entitled or 0 for lic in recorded)
    return {
        "total": len(licenses),
        "compliant": sum(1 for lic in licenses if lic.compliance_status == "compliant"),
        "warning": sum(1 for lic in licenses if lic.compliance_status == "under_utilized"),
        "violation": sum(1 for lic in licenses if lic.compliance_status == "over_deployed"),
        "total_entitled": total_entitled,
        "total_consumed": total_consumed,
        # Guarded: an empty tenant has nothing entitled, and a ZeroDivisionError
        # on day one is the same class of failure this whole exercise is fixing.
        "utilization": round(total_consumed / measured_entitled * 100, 1) if measured_entitled > 0 else None,
    }


@procurement_bp.route("/contracts")
@login_required
@requires_procurement
def contracts_list():
    """List all vendor contracts for current organization."""
    org_id = current_user.organization_id

    contracts = VendorContract.query.filter_by(organization_id=org_id).all()

    # Summary stats
    active_count = sum(1 for c in contracts if c.status == "active")
    expiring_soon = sum(
        1 for c in contracts
        if c.end_date and c.end_date <= date.today() + timedelta(days=90)
    )

    # contracts_list.html iterates item.contract.* plus item.days_until_renewal
    # and item.urgency - it wants each contract wrapped with its renewal state,
    # not the bare row. Passing the rows directly raised UndefinedError on
    # item.contract for every line, so the page 500'd the moment a single
    # contract existed. An empty portfolio never enters the loop, which is why it
    # looked healthy until the first record was created.
    today = date.today()

    def _wrap(contract):
        days = (contract.end_date - today).days if contract.end_date else None
        if days is None:
            urgency = "unknown"
        elif days < 0:
            urgency = "expired"
        elif days <= 30:
            urgency = "critical"
        elif days <= 90:
            urgency = "warning"
        else:
            urgency = "ok"
        # The row's amounts come from the same function the spend totals read,
        # so a contract with no annual cost is a dash here and is left out of
        # the total there, never a zero on either.
        return {
            "contract": contract,
            "days_until_renewal": days,
            "urgency": urgency,
            **get_contract_amounts(contract),
        }

    return render_template(
        "procurement/contracts_list.html",
        contracts=[_wrap(c) for c in contracts],
        # The pagination footer reads both. It is guarded by `total > 20`, so on a
        # small portfolio the block never renders and their absence went unnoticed
        # until a real dataset existed.
        total=len(contracts),
        page=1,
        active_count=active_count,
        expiring_soon=expiring_soon,
    )


@procurement_bp.route("/contracts/<int:contract_id>")
@login_required
@requires_procurement
def contract_detail(contract_id):
    """View contract details (scoped to org)."""
    org_id = current_user.organization_id

    contract = VendorContract.query.filter_by(
        id=contract_id,
        organization_id=org_id
    ).first_or_404()

    # Get licenses under this contract
    licenses = LicenseEntitlement.query.filter_by(
        contract_id=contract_id,
        organization_id=org_id
    ).all()

    return render_template(
        "procurement/contract_detail.html",
        contract=contract,
        licenses=licenses,
    )


@procurement_bp.route("/renewals")
@login_required
@requires_procurement
def renewals_dashboard():
    """Dashboard showing upcoming contract renewals."""
    org_id = current_user.organization_id
    today = date.today()

    # The template renders `contracts` as [{contract, urgency, days_until_renewal}]
    # filtered to the ?days= window. This route used to pass neither `contracts`
    # nor `days_filter`, so the list below the summary tiles NEVER rendered — the
    # page always claimed "No expiring contracts" while the tiles showed real
    # counts, and the days dropdown submitted a parameter nothing read.
    if request.args.get("days") in ("30", "60", "90", "180", "365"):
        days_filter = int(request.args["days"])
    else:
        days_filter = 30

    all_contracts = VendorContract.query.filter_by(organization_id=org_id).all()
    items = []
    for c in all_contracts:
        days = get_days_until_renewal(c)
        if days is None:
            continue
        if days <= days_filter:  # includes expired (negative days)
            items.append({
                "contract": c,
                "urgency": get_renewal_urgency(c),
                "days_until_renewal": days,
            })
    items.sort(key=lambda i: i["days_until_renewal"])

    return render_template(
        "procurement/renewals_dashboard.html",
        contracts=items,
        days_filter=days_filter,
        today=today,
        # Template reads summary.{critical,warning,ok,unknown,upcoming,total} -
        # precisely get_renewal_summary()'s return shape.
        summary=get_renewal_summary(),
    )


@procurement_bp.route("/licenses")
@login_required
@requires_procurement
def licenses_list():
    """List all license entitlements for current organization."""
    org_id = current_user.organization_id

    licenses = LicenseEntitlement.query.filter_by(organization_id=org_id).all()

    # Summary stats
    total_entitled = sum(item.quantity_entitled or 0 for item in licenses)
    total_deployed = sum(item.quantity_deployed or 0 for item in licenses)
    total_used = sum(item.quantity_used or 0 for item in licenses)

    # Compliance breakdown
    compliant = sum(1 for item in licenses if item.compliance_status == "compliant")
    over_deployed = sum(1 for item in licenses if item.compliance_status == "over_deployed")
    under_utilized = sum(1 for item in licenses if item.compliance_status == "under_utilized")

    return render_template(
        "procurement/licenses_list.html",
        licenses=licenses,
        total_entitled=total_entitled,
        total_deployed=total_deployed,
        total_used=total_used,
        compliant=compliant,
        over_deployed=over_deployed,
        under_utilized=under_utilized,
    )


@procurement_bp.route("/licenses/<int:license_id>")
@login_required
@requires_procurement
def license_detail(license_id):
    """View license details (scoped to org)."""
    org_id = current_user.organization_id

    license = LicenseEntitlement.query.filter_by(
        id=license_id,
        organization_id=org_id
    ).first_or_404()

    return render_template(
        "procurement/license_detail.html",
        license=license,
    )


@procurement_bp.route("/compliance")
@login_required
@requires_procurement
def compliance_dashboard():
    """License compliance dashboard - this page has been folded."""
    return redirect(url_for("procurement.licenses_list"), code=302)


@procurement_bp.route("/spend")
@login_required
@requires_procurement
def spend_analytics():
    """Spend analytics dashboard."""
    org_id = current_user.organization_id

    # Spend by category - shared with the AI recommendations endpoint
    # (procurement_ai_service.py) via get_spend_by_category() rather than a
    # second parallel loop over the same rows.
    spend_by_category = get_spend_by_category(org_id)

    return render_template(
        "procurement/spend_analytics.html",
        spend_by_category=spend_by_category,
        # Every figure on the page comes from this one summary. Total contract
        # value and annual cost are each summed here and nowhere else, so the
        # page cannot state two different totals or add a missing amount as 0.
        summary=get_spend_summary(),
    )
