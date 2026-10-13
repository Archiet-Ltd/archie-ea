"""
BillingService — Stripe checkout, customer portal, invoices and signed events.

Keys and price ids come from the environment only (see
app/services/billing_plans.py for the price-id variables). Every action that
needs the payment provider raises ``BillingNotConfigured`` when the keys are
absent and ``BillingError`` when the provider refuses, so a screen can say
what happened instead of redirecting as if it had worked.

The organisation's plan is stored on its ``subscriptions`` row. It is written
from two places only: a completed checkout the administrator returns from,
and the provider's signed events (``handle_webhook``). Plan limits are read
from that row by app/services/billing_plans.py.

Usage:
    from app.services.billing_service import BillingService
    url = BillingService.create_checkout_session(org, "startup", "year", 1, ok, back)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app import db
from app.services import billing_plans, monelytics_provider

logger = logging.getLogger(__name__)

try:
    import stripe

    HAS_STRIPE = True
    StripeError = getattr(stripe, "StripeError", None) or stripe.error.StripeError
    SignatureVerificationError = (
        getattr(stripe, "SignatureVerificationError", None)
        or stripe.error.SignatureVerificationError
    )
except ImportError:
    stripe = None
    HAS_STRIPE = False

    class StripeError(Exception):  # fallback so except clauses stay valid
        pass

    class SignatureVerificationError(Exception):
        pass


NOT_CONFIGURED = "Online payment is not set up on this installation."
PROVIDER_REFUSED = "The payment provider did not accept the request. Nothing was changed."

# Provider subscription status -> stored status. A status not listed here
# leaves the stored status as it was rather than guessing.
_STATUS_MAP = {
    "active": "active",
    "trialing": "trialing",
    "past_due": "past_due",
    "unpaid": "past_due",
    "incomplete": "past_due",
    "paused": "past_due",
    "canceled": "cancelled",
    "incomplete_expired": "cancelled",
}

# Currencies the provider bills in whole units (no minor unit).
_ZERO_DECIMAL = {"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg",
                 "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf"}

PO_FIELD_NAME = "PO number"


class BillingError(Exception):
    """A billing action that did not happen. The message is safe to show."""


class BillingNotConfigured(BillingError):
    """The provider keys or a price id are absent from the environment."""


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """Read *key* from a provider object or a plain dict; None -> default."""
    if obj is None:
        return default
    try:
        value = obj[key]
    except (KeyError, TypeError, IndexError, AttributeError):
        return default
    return default if value is None else value


def _utc(ts: Any) -> Optional[datetime]:
    """Provider unix timestamp -> naive UTC datetime (the column type)."""
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _utc_from_iso(value: Any) -> Optional[datetime]:
    """Monelytics' RFC3339 timestamp string -> naive UTC datetime (the column
    type). Monelytics (a Go service) serialises times this way; Stripe sends
    unix timestamps instead, which is what _utc above is for."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _money(amount: Any, currency: Optional[str]) -> Optional[float]:
    if not isinstance(amount, (int, float)):
        return None
    if (currency or "").lower() in _ZERO_DECIMAL:
        return float(amount)
    return amount / 100.0


def _api():
    """Return the configured provider client or raise BillingNotConfigured."""
    if not HAS_STRIPE:
        raise BillingNotConfigured(NOT_CONFIGURED)
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        raise BillingNotConfigured(NOT_CONFIGURED)
    stripe.api_key = key
    # A local provider mock (stripe-mock, or the browser journeys' stub) in
    # place of the provider's own API. Unset in production.
    base = os.environ.get("STRIPE_API_BASE")
    if base:
        stripe.api_base = base
    return stripe


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except StripeError as exc:
        logger.error("Stripe request %s failed: %s", getattr(fn, "__qualname__", fn), exc)
        raise BillingError(PROVIDER_REFUSED) from exc


def _invoice_subscription_id(invoice: Any) -> Optional[str]:
    """The subscription an invoice bills, across provider API versions."""
    sub_id = _field(invoice, "subscription")
    if isinstance(sub_id, str):
        return sub_id
    if sub_id is not None:
        return _field(sub_id, "id")
    details = _field(_field(invoice, "parent"), "subscription_details")
    sub_id = _field(details, "subscription")
    return sub_id if isinstance(sub_id, str) else _field(sub_id, "id")


class BillingService:
    """Stripe billing operations for Entelim organisations."""

    # ------------------------------------------------------------------ #
    # Customer                                                             #
    # ------------------------------------------------------------------ #

    @classmethod
    def create_customer(cls, org) -> str:
        """Return the org's provider customer id, creating the customer once."""
        api = _api()
        sub = billing_plans.ensure_subscription(org)
        if sub.stripe_customer_id:
            return sub.stripe_customer_id
        customer = _call(
            api.Customer.create,
            name=org.name,
            metadata={"org_id": str(org.id), "org_slug": org.slug},
        )
        sub.stripe_customer_id = customer["id"]
        db.session.commit()
        logger.info("Created Stripe customer %s for org %s", sub.stripe_customer_id, org.id)
        return sub.stripe_customer_id

    # ------------------------------------------------------------------ #
    # Checkout                                                             #
    # ------------------------------------------------------------------ #

    @classmethod
    def _validate_plan_and_interval(cls, plan_key: str, interval: str):
        """The Plan for *plan_key*, once it is confirmed purchasable and
        *interval* is one billing_plans knows. Raises BillingError otherwise.

        Shared by both checkout paths: this does not look up a price id,
        since Monelytics resolves its own plan/variant ids and the direct
        Stripe path needs a price id in addition (see _purchasable below).
        """
        plan = billing_plans.get_plan(plan_key)
        if plan.key != plan_key or not plan.purchasable:
            if plan_key == "enterprise":
                raise BillingError("Enterprise is sold by annual contract. Contact sales to buy it.")
            raise BillingError("Choose Startup or Team to buy online.")
        if interval not in billing_plans.INTERVALS:
            raise BillingError("Choose monthly or annual billing.")
        return plan

    @classmethod
    def _purchasable(cls, plan_key: str, interval: str):
        plan = cls._validate_plan_and_interval(plan_key, interval)
        price_id = billing_plans.price_id_for(plan.key, interval)
        if not price_id:
            raise BillingNotConfigured(
                f"The {plan.name} {'annual' if interval == 'year' else 'monthly'} price is "
                "not set up on this installation."
            )
        return plan, price_id

    @staticmethod
    def _quantity(plan, seats: Optional[int]) -> int:
        if not plan.per_seat:
            return 1
        if seats is None:
            return plan.default_seats
        if not 1 <= seats <= 10000:
            raise BillingError("Choose between 1 and 10,000 editors.")
        return seats

    @staticmethod
    def has_live_subscription(sub) -> bool:
        from app.models.subscription import SubscriptionStatus

        return bool(
            sub is not None
            and (sub.stripe_subscription_id or sub.monelytics_subscription_id)
            and sub.status != SubscriptionStatus.cancelled
        )

    @classmethod
    def create_checkout_session(
        cls,
        org,
        plan_key: str,
        interval: str,
        seats: Optional[int],
        success_url: str,
        cancel_url: str,
    ) -> str:
        """Start a hosted checkout for *org* and return its URL.

        Dispatches to Monelytics (Archiet's shared billing service) when
        MONELYTICS_BASE_URL is set; the direct Stripe path below is the
        fallback when it is not.
        """
        if monelytics_provider.configured():
            return cls._create_checkout_session_via_monelytics(
                org, plan_key, interval, seats, success_url, cancel_url
            )

        api = _api()
        plan, price_id = cls._purchasable(plan_key, interval)
        quantity = cls._quantity(plan, seats)
        sub = billing_plans.ensure_subscription(org)
        if cls.has_live_subscription(sub):
            raise BillingError(
                "Your organisation already has a subscription. Use Change plan to switch."
            )
        customer_id = cls.create_customer(org)
        params: Dict[str, Any] = {
            "mode": "subscription",
            "customer": customer_id,
            "client_reference_id": str(org.id),
            "line_items": [{"price": price_id, "quantity": quantity}],
            "success_url": success_url + ("&" if "?" in success_url else "?")
            + "session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": cancel_url,
            "metadata": {"org_id": str(org.id), "plan": plan.key},
            "subscription_data": {"metadata": {"org_id": str(org.id), "plan": plan.key}},
            "billing_address_collection": "required",
            "tax_id_collection": {"enabled": True},
            "customer_update": {"address": "auto", "name": "auto"},
            "allow_promotion_codes": True,
        }
        if os.environ.get("STRIPE_AUTOMATIC_TAX", "").lower() in ("1", "true", "yes"):
            params["automatic_tax"] = {"enabled": True}
        session = _call(api.checkout.Session.create, **params)
        logger.info("Checkout %s started for org %s plan %s/%s", session["id"], org.id, plan.key, interval)
        return session["url"]

    @classmethod
    def _create_checkout_session_via_monelytics(
        cls,
        org,
        plan_key: str,
        interval: str,
        seats: Optional[int],
        success_url: str,
        cancel_url: str,
    ) -> str:
        plan = cls._validate_plan_and_interval(plan_key, interval)
        quantity = cls._quantity(plan, seats)
        sub = billing_plans.current_subscription(org)  # read-only: nothing is written here
        if cls.has_live_subscription(sub):
            raise BillingError(
                "Your organisation already has a subscription. Use Change plan to switch."
            )
        try:
            checkout_url = monelytics_provider.start_checkout(
                org, plan.key, interval, quantity if plan.per_seat else None, success_url, cancel_url
            )
        except monelytics_provider.MonelyticsNotConfigured as exc:
            raise BillingNotConfigured(str(exc)) from exc
        except monelytics_provider.MonelyticsError as exc:
            raise BillingError(str(exc)) from exc
        logger.info("Monelytics checkout started for org %s plan %s/%s", org.id, plan.key, interval)
        return checkout_url

    @classmethod
    def refresh_from_monelytics(cls, org) -> None:
        """Pull *org*'s subscription state from Monelytics onto its local
        subscriptions row, so the billing page and plan limits read what
        Monelytics currently holds. A no-op when Monelytics has no
        subscription for this organisation yet (a fresh signup still on
        Community, or an abandoned checkout)."""
        try:
            remote = monelytics_provider.refresh_subscription(org)
        except monelytics_provider.MonelyticsNotConfigured as exc:
            raise BillingNotConfigured(str(exc)) from exc
        except monelytics_provider.MonelyticsError as exc:
            raise BillingError(str(exc)) from exc
        if remote is None:
            return
        sub = billing_plans.ensure_subscription(org)
        cls._apply_monelytics_subscription(sub, remote)
        db.session.commit()

    @staticmethod
    def _apply_monelytics_subscription(sub, remote: Dict[str, Any]) -> None:
        """Copy a Monelytics schemas.Subscription object onto the stored row."""
        from app.models.subscription import SubscriptionPlan, SubscriptionStatus

        plan_key = monelytics_provider.plan_key_for_code(remote.get("planCode"))
        if plan_key is not None:
            sub.plan = SubscriptionPlan[plan_key]
        else:
            logger.warning(
                "Monelytics plan code %r on subscription %s is not a configured plan; "
                "plan left unchanged",
                remote.get("planCode"), remote.get("id"),
            )

        interval = monelytics_provider.interval_for_variant(remote.get("billingInterval"))
        if interval is not None:
            sub.billing_interval = interval

        seats = remote.get("seatCount")
        if isinstance(seats, int) and seats > 0:
            sub.seats_purchased = seats

        remote_id = remote.get("id")
        if remote_id:
            sub.monelytics_subscription_id = remote_id

        status = monelytics_provider.STATUS_MAP.get(remote.get("status"))
        if status is not None:
            sub.status = SubscriptionStatus(status)
        else:
            logger.warning("Unrecognised Monelytics status %r; stored status kept", remote.get("status"))

        period_end = _utc_from_iso(remote.get("currentPeriodEnd"))
        if period_end is not None:
            sub.current_period_end = period_end
        sub.cancel_at_period_end = bool(remote.get("cancelAtPeriodEnd", False))

    @classmethod
    def complete_checkout(cls, org, session_id: str):
        """Apply a finished checkout to *org* at once, ahead of the provider event.

        Refuses a checkout that belongs to another organisation.
        """
        api = _api()
        if not session_id:
            raise BillingError("The payment reference is missing.")
        session = _call(api.checkout.Session.retrieve, session_id, expand=["subscription"])
        if str(_field(session, "client_reference_id", "")) != str(org.id):
            logger.warning("Checkout %s does not belong to org %s", session_id, org.id)
            raise BillingError("This payment belongs to a different organisation.")
        if _field(session, "status") != "complete":
            raise BillingError("The payment was not completed. Nothing was changed.")
        stripe_sub = _field(session, "subscription")
        if isinstance(stripe_sub, str):
            stripe_sub = _call(api.Subscription.retrieve, stripe_sub)
        if stripe_sub is None:
            raise BillingError("The payment provider returned no subscription.")
        sub = billing_plans.ensure_subscription(org)
        customer = _field(session, "customer")
        if isinstance(customer, str):
            sub.stripe_customer_id = customer
        cls._apply_subscription(sub, stripe_sub)
        db.session.commit()
        return billing_plans.effective_plan(sub)

    @classmethod
    def _invoiceable(cls, plan_key: str, interval: str):
        """Like _purchasable, but for the platform-admin invoice-billing
        path (R1-B95 PR 2): a plan the self-serve checkout refuses (today,
        only Enterprise -- "sold by annual contract, contact sales") can
        still be invoiced by net terms once an administrator starts that
        contract, since invoice billing never goes through the self-serve
        page _purchasable guards. Any plan with a configured price for
        *interval* qualifies, purchasable or not; a plan with no price
        configured at all (not just not self-serve) still raises.
        """
        plan = billing_plans.get_plan(plan_key)
        if plan.key != plan_key:
            raise BillingError("Choose a plan from the list.")
        if interval not in billing_plans.INTERVALS:
            raise BillingError("Choose monthly or annual billing.")
        price_id = billing_plans.price_id_for(plan.key, interval)
        if not price_id:
            raise BillingNotConfigured(
                f"The {plan.name} {'annual' if interval == 'year' else 'monthly'} price is "
                "not set up on this installation."
            )
        return plan, price_id

    @classmethod
    def start_invoice_billing(
        cls,
        org,
        plan_key: str,
        interval: str,
        seats: Optional[int] = None,
        days_until_due: int = 30,
    ):
        """Start net-terms invoice billing for *org* (R1-B95 PR 2, TB-0193):
        an enterprise organisation invoiced instead of paying by card.

        Creates a real Stripe subscription directly (not through hosted
        checkout, which only ever offers a card) with
        ``collection_method="send_invoice"`` and the given
        ``days_until_due`` (30 for the brief's net-30 case). Stripe emails
        the customer an invoice for each billing period rather than
        charging a card on file; this platform never touches the
        settlement itself, the same posture as the existing card path.

        Refuses when the organisation already has a live subscription --
        same rule as create_checkout_session, enforced here too so this
        platform-admin path cannot silently override an org's existing,
        self-managed subscription.

        The caller must hold platform-admin authority; this method itself
        does not check it (see billing_routes.py/admin_routes.py callers).
        """
        api = _api()
        plan, price_id = cls._invoiceable(plan_key, interval)
        quantity = cls._quantity(plan, seats)
        sub = billing_plans.ensure_subscription(org)
        if cls.has_live_subscription(sub):
            raise BillingError(
                "Your organisation already has a subscription. Cancel or change it first."
            )
        customer_id = cls.create_customer(org)
        stripe_sub = _call(
            api.Subscription.create,
            customer=customer_id,
            items=[{"price": price_id, "quantity": quantity}],
            collection_method="send_invoice",
            days_until_due=days_until_due,
            metadata={"org_id": str(org.id), "plan": plan.key},
        )
        cls._apply_subscription(sub, stripe_sub)
        db.session.commit()
        logger.info(
            "Invoice billing started for org %s: plan %s/%s, net-%s",
            org.id, plan.key, interval, days_until_due,
        )
        return billing_plans.effective_plan(sub)

    @classmethod
    def price_summary(cls, plan_key: str, interval: str) -> Optional[Dict]:
        """The configured price as the provider holds it, or None when unknown."""
        try:
            api = _api()
            _plan, price_id = cls._purchasable(plan_key, interval)
            price = _call(api.Price.retrieve, price_id)
        except BillingError:
            return None
        currency = _field(price, "currency")
        return {
            "amount": _money(_field(price, "unit_amount"), currency),
            "currency": (currency or "").upper() or None,
            "interval": _field(_field(price, "recurring"), "interval"),
        }

    # ------------------------------------------------------------------ #
    # Changing, cancelling, portal                                         #
    # ------------------------------------------------------------------ #

    @classmethod
    def _live_subscription_row(cls, org):
        sub = billing_plans.ensure_subscription(org)
        if not cls.has_live_subscription(sub):
            raise BillingError("Your organisation has no paid subscription to change.")
        return sub

    @classmethod
    def change_plan(cls, org, plan_key: str, interval: str, seats: Optional[int] = None):
        """Move the live subscription to another plan, interval or seat count.

        The new limits apply as soon as this returns; the provider prorates.
        """
        api = _api()
        plan, price_id = cls._purchasable(plan_key, interval)
        quantity = cls._quantity(plan, seats)
        sub = cls._live_subscription_row(org)
        current = _call(api.Subscription.retrieve, sub.stripe_subscription_id)
        items = _field(_field(current, "items"), "data", [])
        if not items:
            raise BillingError("The subscription has no plan line to change.")
        updated = _call(
            api.Subscription.modify,
            sub.stripe_subscription_id,
            items=[{"id": items[0]["id"], "price": price_id, "quantity": quantity}],
            proration_behavior="create_prorations",
            cancel_at_period_end=False,
            metadata={"org_id": str(org.id), "plan": plan.key},
        )
        cls._apply_subscription(sub, updated)
        db.session.commit()
        logger.info("Org %s changed plan to %s/%s x%s", org.id, plan.key, interval, quantity)
        return billing_plans.effective_plan(sub)

    @classmethod
    def cancel_subscription(cls, org) -> Optional[datetime]:
        """Cancel at the end of the paid period; returns that date.

        The plan and its limits stay until the provider's deletion event.
        """
        api = _api()
        sub = cls._live_subscription_row(org)
        updated = _call(api.Subscription.modify, sub.stripe_subscription_id, cancel_at_period_end=True)
        cls._apply_subscription(sub, updated)
        sub.cancel_at_period_end = True
        db.session.commit()
        logger.info("Scheduled cancellation for subscription %s org %s", sub.stripe_subscription_id, org.id)
        return sub.current_period_end

    @classmethod
    def resume_subscription(cls, org) -> None:
        """Withdraw a cancellation scheduled for the period end."""
        api = _api()
        sub = cls._live_subscription_row(org)
        updated = _call(api.Subscription.modify, sub.stripe_subscription_id, cancel_at_period_end=False)
        cls._apply_subscription(sub, updated)
        sub.cancel_at_period_end = False
        db.session.commit()

    @classmethod
    def get_portal_url(cls, org, return_url: str = "/admin/billing") -> str:
        """Return a customer-portal URL (payment method, billing address, cancel)."""
        api = _api()
        customer_id = cls.create_customer(org)
        session = _call(api.billing_portal.Session.create, customer=customer_id, return_url=return_url)
        return session["url"]

    # ------------------------------------------------------------------ #
    # Invoices and billing details                                         #
    # ------------------------------------------------------------------ #

    @classmethod
    def list_invoices(cls, org, limit: int = 24) -> List[Dict]:
        """The organisation's invoices, newest first, as the provider holds them."""
        api = _api()
        sub = billing_plans.current_subscription(org)
        if not sub.stripe_customer_id:
            return []
        result = _call(api.Invoice.list, customer=sub.stripe_customer_id, limit=limit)
        invoices = []
        for inv in _field(result, "data", []):
            currency = _field(inv, "currency")
            tax = _field(inv, "tax")
            if tax is None:
                parts = _field(inv, "total_taxes") or _field(inv, "total_tax_amounts")
                if isinstance(parts, list) and parts:
                    tax = sum(_field(p, "amount", 0) for p in parts)
            invoices.append({
                "number": _field(inv, "number"),
                "date": _utc(_field(inv, "created")),
                "total": _money(_field(inv, "total"), currency),
                "tax": _money(tax, currency),
                "currency": (currency or "").upper() or None,
                "status": _field(inv, "status"),
                "pdf_url": _field(inv, "invoice_pdf"),
                "hosted_url": _field(inv, "hosted_invoice_url"),
            })
        return invoices

    @classmethod
    def get_billing_details(cls, org) -> Dict:
        """Billing e-mail and purchase-order number held on the customer."""
        api = _api()
        sub = billing_plans.current_subscription(org)
        if not sub.stripe_customer_id:
            return {"email": None, "po_number": None, "country": None}
        customer = _call(api.Customer.retrieve, sub.stripe_customer_id)
        po_number = None
        for item in _field(_field(customer, "invoice_settings"), "custom_fields", []) or []:
            if _field(item, "name") == PO_FIELD_NAME:
                po_number = _field(item, "value")
        return {
            "email": _field(customer, "email"),
            "po_number": po_number,
            "country": _field(_field(customer, "address"), "country"),
        }

    @classmethod
    def update_billing_details(cls, org, email: str, po_number: str) -> None:
        """Set the invoice e-mail and the PO number printed on future invoices."""
        api = _api()
        email = (email or "").strip()
        po_number = (po_number or "").strip()
        if email and ("@" not in email or len(email) > 254):
            raise BillingError("Enter a valid billing e-mail address.")
        if len(po_number) > 140:
            raise BillingError("The purchase-order number can be at most 140 characters.")
        customer_id = cls.create_customer(org)
        fields = [{"name": PO_FIELD_NAME, "value": po_number}] if po_number else ""
        params: Dict[str, Any] = {"invoice_settings": {"custom_fields": fields}}
        if email:
            params["email"] = email
        _call(api.Customer.modify, customer_id, **params)

    # ------------------------------------------------------------------ #
    # Signed provider events                                               #
    # ------------------------------------------------------------------ #

    @classmethod
    def handle_webhook(cls, payload: bytes, sig_header: str) -> dict:
        """Verify the signature on a provider event, then apply it once.

        Returns ``{"ok": True, ...}`` or ``{"ok": False, "status": <http>, "error": ...}``.
        """
        secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
        if not HAS_STRIPE or not secret:
            return {"ok": False, "status": 503, "error": "Billing is not configured"}
        try:
            stripe.Webhook.construct_event(payload, sig_header, secret)
        except SignatureVerificationError as exc:
            logger.warning("Stripe webhook signature invalid: %s", exc)
            return {"ok": False, "status": 400, "error": "Invalid signature"}
        except ValueError as exc:
            logger.warning("Stripe webhook payload unreadable: %s", exc)
            return {"ok": False, "status": 400, "error": "Invalid payload"}
        from app.jobs.tenant_safe_job import platform_scope

        # A signed provider event carries no signed-in user; it names the
        # organisation by customer id, so the lookup that finds the organisation
        # and the billing_events rows it writes need the platform scope.
        with platform_scope("billing webhook: a signed provider event names the organisation by customer id"):
            return cls.process_event(json.loads(payload))

    _HANDLED = (
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.paid",
        "invoice.payment_failed",
    )

    @classmethod
    def process_event(cls, event: Dict) -> dict:
        """Apply one already-verified event. Never call with an unverified body."""
        from sqlalchemy.exc import IntegrityError

        from app.models.organization import Organization
        from app.models.subscription import BillingEvent

        event_id = event.get("id")
        event_type = event.get("type")
        obj = (event.get("data") or {}).get("object") or {}
        if not event_id or event_type not in cls._HANDLED:
            return {"ok": True, "ignored": "event type not handled"}

        org_id = cls._org_for_object(event_type, obj)
        org = db.session.get(Organization, org_id) if org_id is not None else None
        if org is None:
            logger.info("Stripe event %s (%s) names no known organisation", event_id, event_type)
            return {"ok": True, "ignored": "no matching organisation"}

        already = BillingEvent.query.filter_by(
            organization_id=org.id, provider_event_id=event_id
        ).first()
        if already is not None:
            return {"ok": True, "duplicate": True}

        try:
            sub = billing_plans.ensure_subscription(org)
            cls._apply_event(sub, event_type, obj, _utc(event.get("created")))
            db.session.add(BillingEvent(
                organization_id=org.id, provider_event_id=event_id, event_type=event_type
            ))
            db.session.commit()
        except IntegrityError:
            # The same event id was recorded by a concurrent delivery.
            db.session.rollback()
            return {"ok": True, "duplicate": True}
        except Exception as exc:  # noqa: BLE001 — the provider retries on non-2xx
            db.session.rollback()
            logger.error("Error applying Stripe event %s (%s): %s", event_id, event_type, exc)
            return {"ok": False, "status": 500, "error": "Event could not be applied"}
        return {"ok": True}

    @staticmethod
    def _org_for_object(event_type: str, obj: Dict) -> Optional[int]:
        """Which organisation an event concerns, by the customer id stored for it.

        The customer is created and stored before checkout starts, so every
        event about our own sale names a known customer. The organisation id
        in the event's own reference fields is not trusted on its own: another
        installation sharing the provider account would send the same ids.
        """
        from app.models.subscription import Subscription

        customer = obj.get("customer")
        if isinstance(customer, dict):
            customer = customer.get("id")
        if not customer:
            return None
        # tenant-scoping-ok: stripe_customer_id is a globally-unique provider
        # key; a signed provider event carries no signed-in organisation.
        row = Subscription.query.filter_by(stripe_customer_id=customer).first()
        if row is None:
            return None
        reference = obj.get("client_reference_id")
        if event_type == "checkout.session.completed" and reference not in (None, str(row.organization_id)):
            logger.warning("Checkout for customer %s names another organisation; ignored", customer)
            return None
        return row.organization_id

    @classmethod
    def _apply_event(cls, sub, event_type: str, obj: Dict, created: Optional[datetime]) -> None:
        from app.models.subscription import SubscriptionStatus

        if event_type == "checkout.session.completed":
            if isinstance(obj.get("customer"), str):
                sub.stripe_customer_id = obj["customer"]
            if isinstance(obj.get("subscription"), str) and not cls.has_live_subscription(sub):
                sub.stripe_subscription_id = obj["subscription"]
            return

        if event_type.startswith("customer.subscription."):
            same = sub.stripe_subscription_id in (None, obj.get("id"))
            if not same and cls.has_live_subscription(sub):
                logger.warning(
                    "Ignoring %s for %s: org %s is on subscription %s",
                    event_type, obj.get("id"), sub.organization_id, sub.stripe_subscription_id,
                )
                return
            if cls._older_than_applied(sub, event_type, created):
                return
            if event_type == "customer.subscription.deleted":
                cls._apply_deleted(sub, obj)
            else:
                cls._apply_subscription(sub, obj)
            if created is not None:
                sub.last_event_at = created
            return

        # invoice.paid / invoice.payment_failed. Ordered like the subscription
        # events: a failure delivered after the payment that recovered it must
        # not put a recovered subscription back to past due.
        if _invoice_subscription_id(obj) != sub.stripe_subscription_id or not sub.stripe_subscription_id:
            return
        if cls._older_than_applied(sub, event_type, created):
            return
        if event_type == "invoice.payment_failed":
            sub.status = SubscriptionStatus.past_due
        elif sub.status == SubscriptionStatus.past_due:
            sub.status = SubscriptionStatus.active
        if created is not None:
            sub.last_event_at = created

    @staticmethod
    def _older_than_applied(sub, event_type: str, created: Optional[datetime]) -> bool:
        if created is not None and sub.last_event_at is not None and created < sub.last_event_at:
            logger.info("Ignoring %s older than the last applied event", event_type)
            return True
        return False

    @staticmethod
    def _apply_deleted(sub, obj: Dict) -> None:
        from app.models.subscription import SubscriptionPlan, SubscriptionStatus

        sub.stripe_subscription_id = obj.get("id") or sub.stripe_subscription_id
        sub.plan = SubscriptionPlan.free
        sub.status = SubscriptionStatus.cancelled
        sub.cancel_at_period_end = False
        sub.billing_interval = None
        sub.seats_purchased = billing_plans.get_plan("free").user_limit
        logger.info("Subscription %s ended; org %s is on Community", sub.stripe_subscription_id, sub.organization_id)

    @staticmethod
    def _apply_subscription(sub, obj: Any) -> None:
        """Copy a provider subscription object onto the stored row."""
        from app.models.subscription import SubscriptionPlan, SubscriptionStatus

        items = _field(_field(obj, "items"), "data", []) or []
        item = items[0] if items else None
        price_id = _field(_field(item, "price"), "id")
        match = billing_plans.plan_for_price(price_id)
        if match is not None:
            plan, interval = match
            sub.plan = SubscriptionPlan[plan.key]
            sub.billing_interval = interval
        else:
            logger.warning(
                "Stripe price %s on subscription %s is not a configured plan; plan left unchanged",
                price_id, _field(obj, "id"),
            )
        quantity = _field(item, "quantity")
        if isinstance(quantity, int) and quantity > 0:
            sub.seats_purchased = quantity

        sub_id = _field(obj, "id")
        if isinstance(sub_id, str):
            sub.stripe_subscription_id = sub_id
        customer = _field(obj, "customer")
        if isinstance(customer, str):
            sub.stripe_customer_id = customer

        status = _STATUS_MAP.get(_field(obj, "status"))
        if status is not None:
            sub.status = SubscriptionStatus(status)
        else:
            logger.warning("Unrecognised Stripe status %r; stored status kept", _field(obj, "status"))

        period_end = _utc(_field(obj, "current_period_end")) or _utc(_field(item, "current_period_end"))
        if period_end is not None:
            sub.current_period_end = period_end
        sub.cancel_at_period_end = bool(_field(obj, "cancel_at_period_end", False) or _field(obj, "cancel_at"))

        # R1-B95 PR 2 (TB-0193): mirror Stripe's own collection_method/
        # days_until_due rather than inferring them, so a subscription
        # provider events apply to later (e.g. a dunning-triggered switch
        # back to charge_automatically) stays in sync without this module
        # having to special-case that transition.
        collection_method = _field(obj, "collection_method")
        if isinstance(collection_method, str):
            sub.collection_method = collection_method
        days_until_due = _field(obj, "days_until_due")
        if isinstance(days_until_due, int):
            sub.days_until_due = days_until_due
