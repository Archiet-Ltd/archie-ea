"""Write paths for the Procurement persona.

Until 2026-07-31 this module exposed seven routes, all GET. A Procurement lead
could read contracts and licences but could not create, amend or retire one -
and neither VendorContract nor LicenseEntitlement was constructed anywhere in
the codebase, so on a new tenant every screen rendered permanently empty. The
persona was named for work the product did not let it do.

Kept separate from routes.py so the read and write surfaces stay legible; both
register on the same blueprint.

Every handler re-reads its row through an organisation predicate before writing.
Both models carry TenantMixin now, so a filter is injected on ordinary SELECTs,
but bulk UPDATE/DELETE are documented as NOT covered by that mechanism and a
client-supplied foreign key is never covered by it at all. The explicit predicate
is what makes each handler safe on its own terms rather than by inheritance.
"""

from datetime import date, datetime

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.decorators import requires_procurement
from app.extensions import db
from app.models.application_portfolio import ApplicationComponent, VendorContract
from app.models.contract_application import ContractApplication
from app.models.license_entitlement import LicenseEntitlement
from app.models.vendor.vendor_organization import VendorOrganization
from app.services.feature_flag_service import FeatureFlagService

from . import procurement_bp
from .contract_extraction_service import (
    MAX_CONTRACT_TEXT_CHARS,
    ContractExtractionError,
    extract_contract_terms,
)

CONTRACT_TYPES = ["license", "subscription", "maintenance", "support", "custom_development"]
CONTRACT_CATEGORIES = ["software", "hardware", "service", "consulting"]
CONTRACT_STATUSES = ["active", "expired", "terminated", "pending", "under_negotiation"]


def _parse_date(value):
    """Empty means 'not set'; anything unparseable raises ValueError."""
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_number(value):
    if value is None or str(value).strip() == "":
        return None
    return float(value)


def _owned_contract_or_404(contract_id):
    """A contract in the caller's organisation, or 404.

    404 rather than 403 on someone else's id: confirming a row exists but belongs
    to another tenant is itself a disclosure.
    """
    return VendorContract.query.filter_by(
        id=contract_id, organization_id=current_user.organization_id
    ).first_or_404()


def _owned_license_or_404(license_id):
    return LicenseEntitlement.query.filter_by(
        id=license_id, organization_id=current_user.organization_id
    ).first_or_404()


def _contract_form_context(contract, form):
    return {
        "contract": contract,
        "form": form,
        "vendors": VendorOrganization.query.order_by(VendorOrganization.name).all(),
        "contract_types": CONTRACT_TYPES,
        "contract_categories": CONTRACT_CATEGORIES,
        "contract_statuses": CONTRACT_STATUSES,
    }


def _apply_contract_form(contract, form):
    contract.contract_name = (form.get("contract_name") or "").strip()[:256]
    contract.contract_number = (form.get("contract_number") or "").strip()[:100] or None
    contract.contract_description = (form.get("contract_description") or "").strip() or None
    contract.vendor_id = int(form["vendor_id"]) if form.get("vendor_id") else None

    # Values outside the documented vocabulary are REJECTED, not coerced.
    #
    # These drive the renewal and spend dashboards, and a free-text status simply
    # disappears from every count that groups on it. The first version quietly
    # mapped an unrecognised value to None - which is worse, because
    # VendorContract.status carries default="active": assigning None lets the
    # column default fire, so posting nonsense produced a contract confidently
    # marked active. Silently inventing a meaningful status is a worse failure
    # than storing the junk would have been.
    #
    # Empty is still allowed and means "not specified"; only a non-empty value
    # that is not in the list is an error, and the form's own dropdowns can only
    # produce valid ones.
    for field, allowed in (
        ("contract_type", CONTRACT_TYPES),
        ("contract_category", CONTRACT_CATEGORIES),
        ("status", CONTRACT_STATUSES),
    ):
        value = (form.get(field) or "").strip()
        if value and value not in allowed:
            raise ValueError("%s=%r is not one of %s" % (field, value, allowed))
        setattr(contract, field, value or None)

    contract.contract_value = _parse_number(form.get("contract_value"))
    contract.annual_cost = _parse_number(form.get("annual_cost"))
    contract.currency = (form.get("currency") or "USD").strip()[:10]
    contract.start_date = _parse_date(form.get("start_date"))
    contract.end_date = _parse_date(form.get("end_date"))
    contract.renewal_date = _parse_date(form.get("renewal_date"))
    contract.auto_renewal = form.get("auto_renewal") == "on"
    contract.contract_owner = (form.get("contract_owner") or "").strip()[:100] or None

    # Notice period: blank/empty means "not recorded" (None)
    raw_notice = form.get("notice_period_days")
    if raw_notice is not None and str(raw_notice).strip() != "":
        contract.notice_period_days = int(raw_notice)
    else:
        contract.notice_period_days = None

    if not contract.contract_name:
        raise ValueError("contract_name is required")
    # start_date is NOT NULL with no server default, so an omitted date would fail
    # at flush with an IntegrityError instead of a message anyone can act on.
    if contract.start_date is None:
        contract.start_date = date.today()

    # An end date before the start date produced a contract that two screens
    # disagreed about: the stored status said "Active" while the Renewal Alerts
    # page, which computes expiry independently, said "Expired". The QA audit of
    # 30 Aug 2026 (High #7) created one with an end date of 01/01/2020 and saw
    # exactly that. Two views of the same contract contradicting each other is
    # the worst outcome for a system of record, and the range is nonsense on its
    # own terms regardless.
    if contract.end_date is not None and contract.end_date < contract.start_date:
        raise ValueError(
            "The end date (%s) is before the start date (%s)."
            % (contract.end_date, contract.start_date)
        )
    # A renewal date before the start is the same class of nonsense.
    if contract.renewal_date is not None and contract.renewal_date < contract.start_date:
        raise ValueError(
            "The renewal date (%s) is before the start date (%s)."
            % (contract.renewal_date, contract.start_date)
        )
    return contract


@procurement_bp.route("/contracts/new", methods=["GET", "POST"])
@login_required
@requires_procurement
def contract_create():
    """Create a vendor contract."""
    if request.method == "POST":
        contract = VendorContract(organization_id=current_user.organization_id)
        try:
            _apply_contract_form(contract, request.form)
            db.session.add(contract)
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            # Say WHICH field is wrong. The catch-all told a user who had put the
            # end date before the start date to "check the dates (YYYY-MM-DD)",
            # which is advice about formatting for a problem that is not one.
            reason = str(exc)
            flash(
                reason if reason.endswith(".")
                else "Check the contract name, dates (YYYY-MM-DD) and amounts.",
                "danger",
            )
            return render_template(
                "procurement/contract_form.html",
                **_contract_form_context(None, request.form)
            ), 400
        except IntegrityError:
            # contract_number is globally unique. Without this the duplicate
            # surfaces as a 500 and the user is told nothing about which field
            # was at fault - browser testing hit it on the second submission of
            # the same reference.
            db.session.rollback()
            flash("That contract number is already in use.", "danger")
            return render_template(
                "procurement/contract_form.html",
                **_contract_form_context(None, request.form)
            ), 400
        flash("Contract created.", "success")
        return redirect(url_for("procurement.contract_detail", contract_id=contract.id))

    return render_template(
        "procurement/contract_form.html", **_contract_form_context(None, {})
    )


@procurement_bp.route("/contracts/<int:contract_id>/edit", methods=["GET", "POST"])
@login_required
@requires_procurement
def contract_edit(contract_id):
    """Amend a vendor contract."""
    contract = _owned_contract_or_404(contract_id)

    if request.method == "POST":
        try:
            _apply_contract_form(contract, request.form)
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            # Say WHICH field is wrong. The catch-all told a user who had put the
            # end date before the start date to "check the dates (YYYY-MM-DD)",
            # which is advice about formatting for a problem that is not one.
            reason = str(exc)
            flash(
                reason if reason.endswith(".")
                else "Check the contract name, dates (YYYY-MM-DD) and amounts.",
                "danger",
            )
            return render_template(
                "procurement/contract_form.html",
                **_contract_form_context(contract, request.form)
            ), 400
        flash("Contract updated.", "success")
        return redirect(url_for("procurement.contract_detail", contract_id=contract.id))

    return render_template(
        "procurement/contract_form.html", **_contract_form_context(contract, {})
    )


@procurement_bp.route("/contracts/<int:contract_id>/delete", methods=["POST"])
@login_required
@requires_procurement
def contract_delete(contract_id):
    """Delete a contract and the licences held under it."""
    contract = _owned_contract_or_404(contract_id)

    # license.contract_id is NOT NULL, so the children have to go first or the
    # flush fails. The organisation predicate is repeated here deliberately: a
    # bulk delete bypasses the tenant filter entirely, which is documented
    # behaviour and exactly the kind of thing that is easy to forget.
    LicenseEntitlement.query.filter_by(
        contract_id=contract.id, organization_id=current_user.organization_id
    ).delete(synchronize_session=False)

    db.session.delete(contract)
    db.session.commit()
    flash("Contract deleted.", "success")
    return redirect(url_for("procurement.contracts_list"))


@procurement_bp.route("/api/contracts/ai-extract", methods=["POST"])
@login_required
@requires_procurement
def contract_ai_extract():
    """POST /procurement/api/contracts/ai-extract

    Body: {"text": "<pasted contract text>"}
    200: {"extracted": {...}} - every field the pasted text doesn't state is
         null; the caller (contract_form.html) must not treat null as "no
         change needed" and must not invent a display value for it.
    400: empty or oversized text.
    502: the LLM call failed, or its response could not be trusted as the
         expected JSON shape. Never 200 with a fabricated body.
    503: AI is not configured for this deployment (FeatureFlagService gate).
    """
    feature_guard = FeatureFlagService.require_ai_for_route(
        FeatureFlagService.FEATURE_SUGGESTIONS, endpoint_name="procurement.contract_ai_extract"
    )
    if feature_guard:
        return feature_guard

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    if len(text) > MAX_CONTRACT_TEXT_CHARS:
        return jsonify({
            "error": f"text is too long (max {MAX_CONTRACT_TEXT_CHARS} characters)"
        }), 400

    try:
        extracted = extract_contract_terms(text)
    except ContractExtractionError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"extracted": extracted}), 200


def _license_form_context(entitlement, form):
    return {
        "license": entitlement,
        "form": form,
        "contracts": VendorContract.query.filter_by(
            organization_id=current_user.organization_id
        ).order_by(VendorContract.contract_name).all(),
        "license_types": LicenseEntitlement.LICENSE_TYPES,
    }


def _apply_license_form(entitlement, form):
    entitlement.product_name = (form.get("product_name") or "").strip()[:200] or None
    entitlement.license_type = form.get("license_type") or "named_user"
    entitlement.license_metric = (form.get("license_metric") or "").strip()[:50] or None
    raw = form.get("quantity_entitled")
    entitlement.quantity_entitled = int(raw) if raw not in (None, "") else 0
    # Deployed and used are left unrecorded (None -> "—") when blank, never 0:
    # a zero would read as a measured "nobody uses this".
    for field in ("quantity_deployed", "quantity_used"):
        raw = form.get(field)
        setattr(entitlement, field, int(raw) if raw not in (None, "") else None)
    entitlement.unit_cost = _parse_number(form.get("unit_cost"))
    return _recompute_compliance(entitlement)


def _recompute_compliance(entitlement):
    """Derive compliance from the quantities instead of trusting the form.

    compliance_status drives the dashboard an auditor reads. Accepting it from the
    client would let a licence be filed as compliant while over-deployed - the
    exact condition that screen exists to surface.
    """
    entitled = entitlement.quantity_entitled or 0
    deployed = entitlement.quantity_deployed
    if deployed is None:
        # Nothing to judge compliance against until deployment is recorded.
        entitlement.compliance_status = None
    elif deployed > entitled:
        entitlement.compliance_status = "over_deployed"
    elif entitled and deployed < entitled * 0.5:
        entitlement.compliance_status = "under_utilized"
    else:
        entitlement.compliance_status = "compliant"
    return entitlement


@procurement_bp.route("/licenses/new", methods=["GET", "POST"])
@login_required
@requires_procurement
def license_create():
    """Record a licence entitlement under a contract."""
    if request.method == "POST":
        contract_id = request.form.get("contract_id")
        if not contract_id:
            flash("A licence must belong to a contract.", "danger")
            return render_template(
                "procurement/license_form.html",
                **_license_form_context(None, request.form)
            ), 400

        # contract_id arrives from the client, so re-read it through the tenant
        # predicate: otherwise a crafted post could hang a licence off another
        # organisation's contract, where its holder would then see it.
        _owned_contract_or_404(int(contract_id))

        entitlement = LicenseEntitlement(
            organization_id=current_user.organization_id,
            contract_id=int(contract_id),
        )
        try:
            _apply_license_form(entitlement, request.form)
            db.session.add(entitlement)
            db.session.commit()
        except ValueError:
            db.session.rollback()
            flash("Quantities must be whole numbers and unit cost numeric.", "danger")
            return render_template(
                "procurement/license_form.html",
                **_license_form_context(None, request.form)
            ), 400

        flash("Licence recorded.", "success")
        return redirect(url_for("procurement.license_detail", license_id=entitlement.id))

    return render_template(
        "procurement/license_form.html", **_license_form_context(None, {})
    )


@procurement_bp.route("/licenses/<int:license_id>/edit", methods=["GET", "POST"])
@login_required
@requires_procurement
def license_edit(license_id):
    """Amend a licence entitlement."""
    entitlement = _owned_license_or_404(license_id)

    if request.method == "POST":
        try:
            contract_id = request.form.get("contract_id")
            if contract_id:
                _owned_contract_or_404(int(contract_id))
                entitlement.contract_id = int(contract_id)
            _apply_license_form(entitlement, request.form)
            db.session.commit()
        except ValueError:
            db.session.rollback()
            flash("Quantities must be whole numbers and unit cost numeric.", "danger")
            return render_template(
                "procurement/license_form.html",
                **_license_form_context(entitlement, request.form)
            ), 400

        flash("Licence updated.", "success")
        return redirect(url_for("procurement.license_detail", license_id=entitlement.id))

    return render_template(
        "procurement/license_form.html", **_license_form_context(entitlement, {})
    )


@procurement_bp.route("/licenses/<int:license_id>/delete", methods=["POST"])
@login_required
@requires_procurement
def license_delete(license_id):
    """Delete a licence entitlement."""
    entitlement = _owned_license_or_404(license_id)
    db.session.delete(entitlement)
    db.session.commit()
    flash("Licence deleted.", "success")
    return redirect(url_for("procurement.licenses_list"))


@procurement_bp.route("/contracts/<int:contract_id>/link-application", methods=["POST"])
@login_required
@requires_procurement
def contract_link_application(contract_id):
    """Link an application to a contract.

    Both the contract and the application must belong to the caller's
    organisation. A cross-organisation link is refused with 404.
    """
    contract = _owned_contract_or_404(contract_id)
    application_id = request.form.get("application_id")
    if not application_id:
        flash("Application id is required.", "danger")
        return redirect(url_for("procurement.contract_detail", contract_id=contract.id))

    application = ApplicationComponent.query.filter_by(
        id=int(application_id),
        organization_id=current_user.organization_id,
    ).first()
    if not application:
        return "", 404

    existing = ContractApplication.query.filter_by(
        contract_id=contract.id,
        application_id=application.id,
        organization_id=current_user.organization_id,
    ).first()
    if existing:
        flash("Application is already linked to this contract.", "info")
        return redirect(url_for("procurement.contract_detail", contract_id=contract.id))

    link = ContractApplication(
        contract_id=contract.id,
        application_id=application.id,
        organization_id=current_user.organization_id,
    )
    db.session.add(link)
    db.session.commit()
    flash("Application linked to contract.", "success")
    return redirect(url_for("procurement.contract_detail", contract_id=contract.id))


@procurement_bp.route("/contracts/<int:contract_id>/unlink-application", methods=["POST"])
@login_required
@requires_procurement
def contract_unlink_application(contract_id):
    """Remove a link between an application and a contract.

    Both the contract and the link must belong to the caller's organisation.
    """
    contract = _owned_contract_or_404(contract_id)
    application_id = request.form.get("application_id")
    if not application_id:
        flash("Application id is required.", "danger")
        return redirect(url_for("procurement.contract_detail", contract_id=contract.id))

    link = ContractApplication.query.filter_by(
        contract_id=contract.id,
        application_id=int(application_id),
        organization_id=current_user.organization_id,
    ).first()
    if not link:
        return "", 404

    db.session.delete(link)
    db.session.commit()
    flash("Application unlinked from contract.", "success")
    return redirect(url_for("procurement.contract_detail", contract_id=contract.id))


@procurement_bp.route("/")
@login_required
@requires_procurement
def index():
    """Section landing page.

    /procurement had no route of its own, so the section root 404'd while every
    page beneath it worked - a dead link from any breadcrumb, bookmark or sidebar
    entry pointing at the section rather than a leaf.
    """
    return redirect(url_for("procurement.contracts_list"))
