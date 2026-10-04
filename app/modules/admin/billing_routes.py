"""
Billing routes — plan, checkout, limits and invoices for organisation administrators.

Blueprint: billing_bp
URL prefix: /admin/billing  (registered in app/modules/admin/__init__.py)

Routes:
  GET  /admin/billing                   — plan, limits, plans to buy, invoices, billing details
  POST /admin/billing/upgrade           — start hosted checkout for a plan
  GET  /admin/billing/checkout/complete — return from checkout; apply the plan at once
  POST /admin/billing/change-plan       — switch the live subscription's plan / seats
  POST /admin/billing/cancel            — cancel at the end of the paid period
  POST /admin/billing/resume            — withdraw a scheduled cancellation
  POST /admin/billing/details           — billing e-mail and purchase-order number
  GET  /admin/billing/portal            — redirect to the provider's customer portal
  POST /admin/billing/webhook           — provider events (no session; signature verified)
"""

import logging

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from app.extensions import csrf
from app.decorators import admin_required

logger = logging.getLogger(__name__)

billing_bp = Blueprint("billing", __name__)


def _org():
    from app.middleware.tenant_context import current_org

    return current_org()


def _seats(raw):
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1  # rejected by the service with a readable message


def _back(plan=None, interval=None):
    args = {}
    if plan:
        args["plan"] = plan
    if interval:
        args["interval"] = interval
    return redirect(url_for("billing.billing_index", **args) + ("#checkout" if plan else ""))


@billing_bp.route("/")
@login_required
@admin_required
def billing_index():
    from app.services import billing_plans
    from app.services.billing_service import BillingError, BillingService

    org = _org()
    if org is None:
        return render_template(
            "admin/billing.html",
            subscription=None,
            error="No organisation found for your account.",
        )

    sub = billing_plans.current_subscription(org)
    config = billing_plans.configuration_status()
    selected = request.args.get("plan")
    interval = request.args.get("interval") or "year"
    if interval not in billing_plans.INTERVALS:
        interval = "year"
    selected_plan = None
    price = None
    if selected:
        candidate = billing_plans.get_plan(selected)
        if candidate.key == selected:
            selected_plan = candidate
            if candidate.purchasable and config["ready"]:
                price = BillingService.price_summary(candidate.key, interval)

    invoices, invoice_error = None, None
    details, details_error = None, None
    if config["ready"]:
        try:
            invoices = BillingService.list_invoices(org)
        except BillingError as exc:
            invoice_error = str(exc)
        try:
            details = BillingService.get_billing_details(org)
        except BillingError as exc:
            details_error = str(exc)

    return render_template(
        "admin/billing.html",
        subscription=sub,
        org=org,
        plan=billing_plans.effective_plan(sub),
        limits=billing_plans.user_limit_status(org.id),
        plans=billing_plans.PLANS,
        config=config,
        stripe_configured=config["ready"],
        has_live_subscription=BillingService.has_live_subscription(sub),
        selected_plan=selected_plan,
        selected_interval=interval,
        price=price,
        invoices=invoices,
        invoice_error=invoice_error,
        details=details,
        details_error=details_error,
        contact_sales_url=billing_plans.CONTACT_SALES_URL,
    )


@billing_bp.route("/upgrade", methods=["POST"])
@login_required
@admin_required
def billing_upgrade():
    """Start the provider's hosted checkout for the chosen plan."""
    org = _org()
    if org is None:
        return jsonify({"error": "No organisation found"}), 400

    from app.services.billing_service import BillingError, BillingService

    plan = request.form.get("plan", "")
    interval = request.form.get("interval", "year")
    complete_url = request.host_url.rstrip("/") + url_for("billing.billing_checkout_complete")
    cancel_url = request.host_url.rstrip("/") + url_for(
        "billing.billing_index", plan=plan, interval=interval
    )
    try:
        checkout_url = BillingService.create_checkout_session(
            org, plan, interval, _seats(request.form.get("seats")), complete_url, cancel_url
        )
    except BillingError as exc:
        flash(f"{exc} No payment was taken.", "error")
        return _back(plan, interval)
    return redirect(checkout_url, code=303)


@billing_bp.route("/checkout/complete")
@login_required
@admin_required
def billing_checkout_complete():
    """Where the provider returns the administrator after paying."""
    org = _org()
    if org is None:
        return redirect(url_for("billing.billing_index"))

    from app.services.billing_service import BillingError, BillingService

    try:
        plan = BillingService.complete_checkout(org, request.args.get("session_id", ""))
    except BillingError as exc:
        flash(str(exc), "error")
        return redirect(url_for("billing.billing_index"))
    flash(f"Payment received. Your organisation is on the {plan.name} plan.", "success")
    return redirect(url_for("billing.billing_index"))


@billing_bp.route("/change-plan", methods=["POST"])
@login_required
@admin_required
def billing_change_plan():
    org = _org()
    if org is None:
        return redirect(url_for("billing.billing_index"))

    from app.services import billing_plans
    from app.services.billing_service import BillingError, BillingService

    plan_key = request.form.get("plan", "")
    interval = request.form.get("interval", "year")
    try:
        plan = BillingService.change_plan(org, plan_key, interval, _seats(request.form.get("seats")))
    except BillingError as exc:
        flash(str(exc), "error")
        return redirect(url_for("billing.billing_index"))
    limits = billing_plans.user_limit_status(org.id)
    message = f"Your organisation is now on the {plan.name} plan."
    if limits["limit"] is not None and limits["used"] > limits["limit"]:
        message += (
            f" It admits {limits['limit']} and you have {limits['used']}: everyone keeps access,"
            " but no one new can be added until you are within the limit."
        )
    flash(message, "success")
    return redirect(url_for("billing.billing_index"))


@billing_bp.route("/cancel", methods=["POST"])
@login_required
@admin_required
def billing_cancel():
    org = _org()
    if org is None:
        return redirect(url_for("billing.billing_index"))

    from app.services.billing_service import BillingError, BillingService

    try:
        ends = BillingService.cancel_subscription(org)
    except BillingError as exc:
        flash(str(exc), "error")
        return redirect(url_for("billing.billing_index"))
    when = ends.strftime("%d %b %Y") if ends else "the end of the paid period"
    flash(f"Your subscription is cancelled. The plan stays in place until {when}.", "success")
    return redirect(url_for("billing.billing_index"))


@billing_bp.route("/resume", methods=["POST"])
@login_required
@admin_required
def billing_resume():
    org = _org()
    if org is None:
        return redirect(url_for("billing.billing_index"))

    from app.services.billing_service import BillingError, BillingService

    try:
        BillingService.resume_subscription(org)
    except BillingError as exc:
        flash(str(exc), "error")
        return redirect(url_for("billing.billing_index"))
    flash("The cancellation is withdrawn. Your subscription renews as normal.", "success")
    return redirect(url_for("billing.billing_index"))


@billing_bp.route("/details", methods=["POST"])
@login_required
@admin_required
def billing_details():
    org = _org()
    if org is None:
        return redirect(url_for("billing.billing_index"))

    from app.services.billing_service import BillingError, BillingService

    try:
        BillingService.update_billing_details(
            org, request.form.get("billing_email", ""), request.form.get("po_number", "")
        )
    except BillingError as exc:
        flash(str(exc), "error")
        return redirect(url_for("billing.billing_index") + "#billing-details")
    flash("Billing details saved. Future invoices carry them.", "success")
    return redirect(url_for("billing.billing_index") + "#billing-details")


@billing_bp.route("/portal")
@login_required
@admin_required
def billing_portal():
    """Redirect to the provider's customer portal for self-service billing."""
    org = _org()
    if org is None:
        return redirect(url_for("billing.billing_index"))

    from app.services.billing_service import BillingError, BillingService

    return_url = request.host_url.rstrip("/") + url_for("billing.billing_index")
    try:
        portal_url = BillingService.get_portal_url(org, return_url=return_url)
    except BillingError as exc:
        flash(str(exc), "error")
        return redirect(url_for("billing.billing_index"))
    return redirect(portal_url)


@billing_bp.route("/webhook", methods=["POST"])
@csrf.exempt
def billing_webhook():
    """Provider event endpoint — signature verified inside BillingService."""
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    from app.services.billing_service import BillingService

    result = BillingService.handle_webhook(payload, sig_header)
    if result.get("ok"):
        return jsonify({"received": True}), 200
    return jsonify({"error": result.get("error", "Webhook processing failed")}), result.get("status", 400)
