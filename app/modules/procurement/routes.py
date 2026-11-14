"""
Procurement Routes (NS-010, NS-011)

Procurement persona dashboards for contract and license management.
All queries are scoped by current user's organization_id.

ADR Reference: docs/adr/0010-procurement-persona.md
"""

from datetime import date

from flask import abort, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.decorators import (
    requires_contract_visibility,
    requires_licence_visibility,
    requires_procurement,
    requires_spend_visibility,
)
from app.models.application_portfolio import VendorContract
from app.models.license_entitlement import LicenseEntitlement

from app.services import finance_fact_store
from app.services.application_cost_accessor import get_reporting_currency
from app.services.cost_visibility import COSTS_WITHHELD, costs_visible

from . import procurement_bp

# As in my_applications, services.py held the summary shapes these templates
# read and was never imported, so every dashboard raised jinja2.UndefinedError.
from app.services.vendor_register import (
    get_days_until_renewal,
    get_renewal_urgency,
    renewal_date_of,
    renewal_state,
)
from .services import (
    get_contract_amounts,
    get_renewal_summary,
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
@requires_contract_visibility
def contracts_list():
    """List all vendor contracts for current organization."""
    org_id = current_user.organization_id

    from app.services.vendor_register import contract_renewal_due, is_active, register_contracts

    contracts = register_contracts(org_id)

    # The page's status filter: the stats below still count every contract. "Active" is the ledger's one
    # definition (status active, no end date or one not yet past), so a contract that ended is never listed
    # as active here while every cost figure leaves it out.
    status = (request.args.get("status") or "").strip().lower()
    if status == "active":
        listed = [c for c in contracts if is_active(c)]
    else:
        listed = [c for c in contracts if (c.status or "").lower() == status] if status else contracts

    # Summary stats
    active_count = sum(1 for c in contracts if is_active(c))
    expiring_soon = sum(1 for c in contracts if contract_renewal_due(c))

    # contracts_list.html iterates item.contract.* plus item.days_until_renewal
    # and item.urgency - it wants each contract wrapped with its renewal state,
    # not the bare row. Passing the rows directly raised UndefinedError on
    # item.contract for every line, so the page 500'd the moment a single
    # contract existed. An empty portfolio never enters the loop, which is why it
    # looked healthy until the first record was created.
    def _wrap(contract):
        # The renewal state is the one rule the renewals page and the vendor pages read.
        renewal = renewal_state(contract)
        days = renewal.days
        urgency = get_renewal_urgency(contract)
        # The row's amounts come from the same function the spend totals read,
        # so a contract with no annual cost is a dash here and is left out of
        # the total there, never a zero on either.
        return {
            "contract": contract,
            "days_until_renewal": days,
            "urgency": urgency,
            "renewal": renewal,
            **get_contract_amounts(contract),
        }

    return render_template(
        "procurement/contracts_list.html",
        contracts=[_wrap(c) for c in listed],
        status=status,
        search=None,
        # The renewal column shows only when some contract has a date; otherwise the page says so once.
        any_renewal=any(renewal_date_of(c) for c in contracts),
        # The pagination footer reads both. It is guarded by `total > 20`, so on a
        # small portfolio the block never renders and their absence went unnoticed
        # until a real dataset existed.
        total=len(listed),
        page=1,
        active_count=active_count,
        expiring_soon=expiring_soon,
    )


@procurement_bp.route("/contracts/<int:contract_id>")
@login_required
@requires_contract_visibility
def contract_detail(contract_id):
    """View contract details (scoped to org)."""
    from app.services import vendor_register

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
        renews_on=renewal_date_of(contract),
        days_until_renewal=get_days_until_renewal(contract),
        urgency=get_renewal_urgency(contract),
        renewal=renewal_state(contract),
        system_retired=vendor_register.on_retired_system(contract.id, vendor_register.contract_ledger(org_id)),
        retired_note=vendor_register.RETIRED_SYSTEM_RENEWAL,
        organisation_currency=get_reporting_currency(org_id),
    )


@procurement_bp.route("/renewals")
@login_required
@requires_contract_visibility
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

    # The same contracts the tiles count: the active ones. A contract still marked active whose end date
    # has passed is in neither the tiles nor this list; the page says how many there are.
    all_contracts = VendorContract.query.filter_by(organization_id=org_id, status="active").all()
    from app.services import vendor_register

    retired = {c.id for c in vendor_register.contract_ledger(org_id)["retired_system"]}
    items = []
    for c in all_contracts:
        renewal = renewal_state(c)
        if renewal.kind not in ("overdue", "ahead"):
            continue
        if renewal.days <= days_filter:
            items.append({
                "contract": c,
                "urgency": get_renewal_urgency(c),
                "renewal": renewal,
                "days_until_renewal": renewal.days,
                "renews_on": renewal.on,
                "system_retired": c.id in retired,
            })
    items.sort(key=lambda i: i["days_until_renewal"])

    # A renewal brief is generated on request (?brief=<contract id>) for a
    # contract in this list, and rendered on the page, so the page needs no
    # inline script to show it.
    from app.services.feature_flag_service import FeatureFlagService

    ai_available = FeatureFlagService.is_ai_enabled(FeatureFlagService.FEATURE_SUGGESTIONS)
    brief = brief_contract = brief_error = None
    brief_id = request.args.get("brief", "")
    if ai_available and brief_id.isdigit():
        brief_contract = next((i["contract"] for i in items if i["contract"].id == int(brief_id)), None)
        if brief_contract is not None:
            try:
                from .procurement_ai_service import generate_renewal_brief

                brief = generate_renewal_brief(brief_contract)
            except Exception:  # noqa: BLE001 - the page still renders and says so
                brief_error = "The renewal brief could not be prepared just now. Try again in a minute."

    return render_template(
        "procurement/renewals_dashboard.html",
        contracts=items,
        days_filter=days_filter,
        ai_available=ai_available,
        brief=brief,
        brief_contract=brief_contract,
        brief_error=brief_error,
        today=today,
        retired_note=vendor_register.RETIRED_SYSTEM_RENEWAL,
        # Template reads summary.{critical,warning,ok,unknown,upcoming,total} -
        # precisely get_renewal_summary()'s return shape.
        summary=get_renewal_summary(org_id),
    )


@procurement_bp.route("/licenses")
@login_required
@requires_licence_visibility
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
@requires_licence_visibility
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
    """License compliance dashboard."""
    org_id = current_user.organization_id

    licenses = LicenseEntitlement.query.filter_by(organization_id=org_id).all()

    # Group by compliance status
    by_status = {}
    for item in licenses:
        status = item.compliance_status or "unknown"
        if status not in by_status:
            by_status[status] = []
        by_status[status].append(item)

    # Calculate risk exposure (over-deployed licenses)
    risk_exposure = sum(
        (item.quantity_deployed - item.quantity_entitled) * float(item.unit_cost or 0)
        for item in licenses
        if item.compliance_status == "over_deployed" and item.quantity_deployed and item.quantity_entitled
    )

    # Shelfware (entitled but not used)
    shelfware_value = sum(
        (item.quantity_entitled - item.quantity_used) * float(item.unit_cost or 0)
        for item in licenses
        if item.quantity_entitled and item.quantity_used and item.quantity_entitled > item.quantity_used
    )

    return render_template(
        "procurement/compliance_dashboard.html",
        licenses=licenses,
        by_status=by_status,
        risk_exposure=risk_exposure,
        shelfware_value=shelfware_value,
        summary=_compliance_summary(licenses),
    )


@procurement_bp.route("/spend")
@login_required
@requires_spend_visibility
def spend_analytics():
    """Spend analytics dashboard."""
    org_id = current_user.organization_id

    # Spend by category - shared with the AI recommendations endpoint
    # (procurement_ai_service.py) via get_spend_by_category() rather than a
    # second parallel loop over the same rows.
    spend_by_category = get_spend_by_category(org_id)

    # The spend suggestions are generated on request (?recommendations=1) and
    # rendered on the page, so the page needs no inline script to show them.
    from app.services.feature_flag_service import FeatureFlagService

    ai_available = FeatureFlagService.is_ai_enabled(FeatureFlagService.FEATURE_SUGGESTIONS)
    recommendations = None
    recommendations_error = False
    if ai_available and request.args.get("recommendations"):
        try:
            from .procurement_ai_service import generate_spend_recommendations

            recommendations = generate_spend_recommendations(org_id)
        except Exception:  # noqa: BLE001 - the page still renders; it says the suggestions failed
            recommendations_error = True

    return render_template(
        "procurement/spend_analytics.html",
        spend_by_category=spend_by_category,
        ai_available=ai_available,
        recommendations=recommendations,
        recommendations_error=recommendations_error,
        # Every figure on the page comes from this one summary. Total contract
        # value and annual cost are each summed here and nowhere else, so the
        # page cannot state two different totals or add a missing amount as 0.
        summary=get_spend_summary(org_id),
    )


def _day(value):
    return f"{value.day} {value:%b %Y}"


def _amount_text(amount, currency):
    """An amount the way a person reads it: 2,500 USD, 1,250.50 USD, or 12 for a count."""
    text = f"{amount:,.0f}" if amount == amount.to_integral_value() else f"{amount:,.2f}"
    return f"{text} {currency}" if currency else text


def _money_page(org_id, *, form=None, error=None, status=200):
    """The Money page: the latest figures, the history and the form, all from this organisation's facts."""
    page = request.args.get("page", 1, type=int) or 1
    figures = finance_fact_store.derived_figures(org_id)
    rows, total = finance_fact_store.history(org_id, page=page)
    replacement_dates = {}
    for fact in rows:
        if fact.superseded_by_id is not None:
            replacement_dates[fact.id] = _day(fact.superseded_by.as_of)
    form = form or {}
    replaces = None
    replaces_id = form.get("replaces_id") or request.args.get("replaces")
    if replaces_id and str(replaces_id).isdigit():
        try:
            replaces = finance_fact_store.replaceable_fact(org_id, int(replaces_id))
        except finance_fact_store.FinanceFactError:
            replaces = None
    form_values = {
        "measure": form.get("measure") or (replaces.measure if replaces else request.args.get("measure", "cash_balance")),
        "amount": form.get("amount", ""),
        "currency": form.get("currency") or (replaces.currency if replaces and replaces.currency else get_reporting_currency(org_id)),
        "as_of": form.get("as_of") or finance_fact_store.today().isoformat(),
        "source_ref": form.get("source_ref", ""),
        "subject_element_id": form.get("subject_element_id") or "",
    }
    return render_template(
        "procurement/money.html",
        withheld=False, figures=figures, rows=rows, total=total, page=page,
        pages=max(1, -(-total // 20)), replacement_dates=replacement_dates,
        measure_labels=finance_fact_store.MEASURE_LABELS, form=form_values, error=error, replaces=replaces,
        products=finance_fact_store.products(org_id), day=_day, amount_text=_amount_text,
        saved=bool(request.args.get("saved")), today=finance_fact_store.today(),
    ), status


@procurement_bp.route("/spend/money", methods=["GET", "POST"])
@login_required
def money():
    """Money: the organisation's cash, outgoings, revenue, funding and customers as dated facts."""
    if not costs_visible(current_user):
        # A plain sentence and no figures; nothing may be recorded.
        if request.method == "POST":
            abort(403, description=COSTS_WITHHELD)
        return render_template("procurement/money.html", withheld=True, withheld_text=COSTS_WITHHELD)
    from app.middleware.tenant_context import current_org_id

    org_id = current_org_id()
    if org_id is None:
        abort(404)
    if request.method == "GET":
        return _money_page(org_id)

    form = request.form
    try:
        as_of = date.fromisoformat((form.get("as_of") or "").strip())
    except ValueError:
        return _money_page(org_id, form=form, error="Enter the date the figure was true.", status=422)
    subject = (form.get("subject_element_id") or "").strip()
    replaces = (form.get("replaces_id") or "").strip()
    try:
        finance_fact_store.record_fact(
            org_id, (form.get("measure") or "").strip(), form.get("amount"), as_of, form.get("source_ref"),
            currency=form.get("currency"), stated_by_user_id=current_user.id, recorded_by_id=current_user.id,
            subject_element_id=int(subject) if subject.isdigit() else None,
            replaces_id=int(replaces) if replaces.isdigit() else None,
        )
    except finance_fact_store.FinanceFactError as exc:
        return _money_page(org_id, form=form, error=str(exc), status=422)
    return redirect(url_for("procurement.money", saved=1))
