"""
BillingService — Stripe subscription management.

All Stripe calls are wrapped in try/except so the platform works gracefully
when STRIPE_SECRET_KEY is absent (free-tier / unconfigured deployments).

Usage:
    from app.services.billing_service import BillingService
    url = BillingService.get_portal_url(org)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import stripe
    from stripe.error import StripeError

    HAS_STRIPE = True
except ImportError:
    HAS_STRIPE = False
    StripeError = Exception  # fallback so except clause is always valid


def _stripe_configured() -> bool:
    """Return True only when stripe is importable and a secret key is set."""
    if not HAS_STRIPE:
        return False
    import os

    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def _init_stripe() -> None:
    """Set stripe.api_key from the environment (called lazily)."""
    import os

    if HAS_STRIPE:
        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")


from app import db

class BillingService:
    """Stripe billing operations for Entelim organizations."""

    # Price IDs map — override via env vars or extend for live keys
    PLAN_PRICE_IDS = {
        "free": None,
        "pro": None,       # set STRIPE_PRICE_PRO in env
        "enterprise": None,  # set STRIPE_PRICE_ENTERPRISE in env
    }

    @classmethod
    def _get_price_id(cls, plan: str) -> Optional[str]:
        import os

        env_map = {
            "pro": os.environ.get("STRIPE_PRICE_PRO"),
            "enterprise": os.environ.get("STRIPE_PRICE_ENTERPRISE"),
        }
        return env_map.get(plan)

    @classmethod
    def create_customer(cls, org) -> Optional[str]:
        """Create a Stripe customer for *org* and persist the customer_id.

        Returns the Stripe customer ID string, or None when Stripe is not configured.
        """
        if not _stripe_configured():
            logger.debug("Stripe not configured — skipping create_customer for org %s", org.id)
            return None

        _init_stripe()
        try:
            customer = stripe.Customer.create(
                name=org.name,
                metadata={"org_id": str(org.id), "org_slug": org.slug},
            )
            customer_id: str = customer["id"]

            from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

            sub = Subscription.query.filter_by(organization_id=org.id).first()
            if sub is None:
                sub = Subscription(
                    organization_id=org.id,
                    plan=SubscriptionPlan.free,
                    status=SubscriptionStatus.active,
                )
                db.session.add(sub)
            sub.stripe_customer_id = customer_id
            db.session.commit()

            logger.info("Created Stripe customer %s for org %s", customer_id, org.id)
            return customer_id
        except StripeError as exc:
            logger.error("Stripe error in create_customer for org %s: %s", org.id, exc)
            return None

    @classmethod
    def create_subscription(cls, org, plan: str, seats: int = 5) -> Optional[str]:
        """Create a Stripe subscription for *org* on the given *plan*.

        Returns the Stripe subscription ID, or None on error / unconfigured.
        """
        if not _stripe_configured():
            logger.debug("Stripe not configured — skipping create_subscription")
            return None

        price_id = cls._get_price_id(plan)
        if not price_id:
            logger.warning("No Stripe price ID configured for plan '%s'", plan)
            return None

        _init_stripe()
        try:
            from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

            sub = Subscription.query.filter_by(organization_id=org.id).first()
            customer_id = sub.stripe_customer_id if sub else None
            if not customer_id:
                customer_id = cls.create_customer(org)
            if not customer_id:
                return None

            stripe_sub = stripe.Subscription.create(
                customer=customer_id,
                items=[{"price": price_id, "quantity": seats}],
                metadata={"org_id": str(org.id), "plan": plan},
            )
            sub_id: str = stripe_sub["id"]
            period_end_ts = stripe_sub.get("current_period_end")
            period_end = (
                datetime.fromtimestamp(period_end_ts, tz=timezone.utc)
                if period_end_ts
                else None
            )

            if sub is None:
                sub = Subscription(organization_id=org.id)
                db.session.add(sub)
            sub.stripe_subscription_id = sub_id
            sub.plan = SubscriptionPlan[plan]
            sub.status = SubscriptionStatus.active
            sub.seats_purchased = seats
            sub.current_period_end = period_end
            db.session.commit()

            logger.info("Created Stripe subscription %s for org %s plan %s", sub_id, org.id, plan)
            return sub_id
        except StripeError as exc:
            logger.error("Stripe error in create_subscription for org %s: %s", org.id, exc)
            return None

    @classmethod
    def cancel_subscription(cls, org) -> bool:
        """Cancel the org's Stripe subscription at period end.

        Returns True on success, False on error or when unconfigured.
        """
        if not _stripe_configured():
            logger.debug("Stripe not configured — skipping cancel_subscription")
            return False

        _init_stripe()
        try:
            from app.models.subscription import Subscription, SubscriptionStatus

            sub = Subscription.query.filter_by(organization_id=org.id).first()
            if not sub or not sub.stripe_subscription_id:
                logger.warning("No active Stripe subscription to cancel for org %s", org.id)
                return False

            stripe.Subscription.modify(
                sub.stripe_subscription_id,
                cancel_at_period_end=True,
            )
            sub.status = SubscriptionStatus.cancelled
            db.session.commit()

            logger.info("Scheduled cancellation for subscription %s org %s", sub.stripe_subscription_id, org.id)
            return True
        except StripeError as exc:
            logger.error("Stripe error in cancel_subscription for org %s: %s", org.id, exc)
            return False

    @classmethod
    def get_portal_url(cls, org, return_url: str = "/admin/billing") -> Optional[str]:
        """Return a Stripe customer portal URL for self-service billing management.

        Returns None when Stripe is not configured or on error.
        """
        if not _stripe_configured():
            logger.debug("Stripe not configured — no portal URL for org %s", org.id)
            return None

        _init_stripe()
        try:
            from app.models.subscription import Subscription

            sub = Subscription.query.filter_by(organization_id=org.id).first()
            if not sub or not sub.stripe_customer_id:
                customer_id = cls.create_customer(org)
                if not customer_id:
                    return None
            else:
                customer_id = sub.stripe_customer_id

            session = stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=return_url,
            )
            return session["url"]
        except StripeError as exc:
            logger.error("Stripe error in get_portal_url for org %s: %s", org.id, exc)
            return None

    @classmethod
    def handle_webhook(cls, payload: bytes, sig_header: str) -> dict:
        """Verify and process a Stripe webhook event.

        Handles:
          - customer.subscription.updated
          - customer.subscription.deleted
          - invoice.payment_failed

        Returns a dict with ``{"ok": True}`` on success or ``{"ok": False, "error": "..."}``
        on failure / unconfigured.
        """
        if not _stripe_configured():
            return {"ok": False, "error": "Stripe not configured"}

        import os

        _init_stripe()
        webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except stripe.error.SignatureVerificationError as exc:
            logger.warning("Stripe webhook signature invalid: %s", exc)
            return {"ok": False, "error": "Invalid signature"}
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to parse Stripe webhook: %s", exc)
            return {"ok": False, "error": str(exc)}

        event_type: str = event["type"]
        data_object = event["data"]["object"]

        try:
            if event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
                cls._handle_subscription_event(data_object, event_type)
            elif event_type == "invoice.payment_failed":
                cls._handle_payment_failed(data_object)
            else:
                logger.debug("Unhandled Stripe event type: %s", event_type)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error processing Stripe event %s: %s", event_type, exc)
            return {"ok": False, "error": str(exc)}

        return {"ok": True}

    @classmethod
    def _handle_subscription_event(cls, stripe_sub: dict, event_type: str) -> None:
        from app.models.subscription import Subscription, SubscriptionStatus

        stripe_sub_id: str = stripe_sub.get("id", "")
        # tenant-scoping-ok: stripe_subscription_id is a globally-unique Stripe
        # key; this is a webhook handler with no request/org context.
        sub = Subscription.query.filter_by(stripe_subscription_id=stripe_sub_id).first()
        if not sub:
            logger.debug("No local subscription found for Stripe sub %s", stripe_sub_id)
            return

        if event_type == "customer.subscription.deleted":
            sub.status = SubscriptionStatus.cancelled
        else:
            raw_status = stripe_sub.get("status", "active")
            status_map = {
                "active": SubscriptionStatus.active,
                "past_due": SubscriptionStatus.past_due,
                "canceled": SubscriptionStatus.cancelled,
                "trialing": SubscriptionStatus.trialing,
            }
            sub.status = status_map.get(raw_status, SubscriptionStatus.active)

        period_end_ts = stripe_sub.get("current_period_end")
        if period_end_ts:
            sub.current_period_end = datetime.fromtimestamp(period_end_ts, tz=timezone.utc)

        db.session.commit()
        logger.info("Updated subscription %s status to %s", stripe_sub_id, sub.status.value)

    @classmethod
    def _handle_payment_failed(cls, invoice: dict) -> None:
        from app.models.subscription import Subscription, SubscriptionStatus

        stripe_sub_id: str = invoice.get("subscription", "")
        if not stripe_sub_id:
            return

        # tenant-scoping-ok: stripe_subscription_id is a globally-unique Stripe
        # key; this is a webhook handler with no request/org context.
        sub = Subscription.query.filter_by(stripe_subscription_id=stripe_sub_id).first()
        if not sub:
            return

        sub.status = SubscriptionStatus.past_due
        db.session.commit()
        logger.info("Marked subscription %s as past_due after payment failure", stripe_sub_id)
