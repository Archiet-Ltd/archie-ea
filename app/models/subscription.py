"""
Subscription model — billing record for each organization.

Stores Stripe customer/subscription IDs and plan metadata.
One subscription per organization; the org FK is the tenant boundary.
"""

import enum

from app import db
from app.models.mixins.core import TenantMixin


class SubscriptionPlan(enum.Enum):
    free = "free"
    # Retired label: rows written before the Startup/Team catalogue carry it.
    # Its limits are Team's (see app/services/billing_plans.py).
    pro = "pro"
    enterprise = "enterprise"
    startup = "startup"
    team = "team"


class SubscriptionStatus(enum.Enum):
    active = "active"
    past_due = "past_due"
    cancelled = "cancelled"
    trialing = "trialing"


class Subscription(db.Model):  # migration-exempt
    """Billing record for an organization. One row per org."""

    __tablename__ = "subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    stripe_customer_id = db.Column(db.String(255), nullable=True, index=True)
    stripe_subscription_id = db.Column(db.String(255), nullable=True, index=True)
    # Set only once a refresh call confirms Monelytics (Archiet's shared
    # billing service, app/services/monelytics_provider.py) holds an active
    # subscription for this organisation — never at checkout initiation,
    # before the shopper has paid. NULL for every organisation on the direct
    # Stripe path, and for one still on Community.
    monelytics_subscription_id = db.Column(db.String(255), nullable=True, index=True)
    plan = db.Column(
        db.Enum(SubscriptionPlan),
        nullable=False,
        default=SubscriptionPlan.free,
    )
    status = db.Column(
        db.Enum(SubscriptionStatus),
        nullable=False,
        default=SubscriptionStatus.active,
    )
    current_period_end = db.Column(db.DateTime, nullable=True)
    seats_purchased = db.Column(db.Integer, nullable=False, default=5)
    # True while a cancellation is scheduled for current_period_end; the
    # subscription stays active until then. NULL on rows written before this.
    cancel_at_period_end = db.Column(db.Boolean, nullable=True)
    # "month" or "year" — the billing interval of the price the customer
    # bought. NULL when unknown (free plan, or rows written before this).
    billing_interval = db.Column(db.String(10), nullable=True)
    # Stripe's own subscription field, mirrored here (R1-B95 PR 2, TB-0193):
    # "charge_automatically" (card on file, the hosted-checkout default) or
    # "send_invoice" (net-terms collection, BillingService.start_invoice_
    # billing). NULL means "charge_automatically" -- every row written
    # before this column existed used a card, and every self-serve checkout
    # still does.
    collection_method = db.Column(db.String(20), nullable=True)
    # Net-terms window in days for a send_invoice subscription (e.g. 30 for
    # "net-30"). NULL when collection_method is not send_invoice.
    days_until_due = db.Column(db.Integer, nullable=True)
    # Creation time of the newest provider event applied to this row, so an
    # older event delivered late cannot roll the plan back.
    last_event_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=db.func.now(),
        onupdate=db.func.now(),
        nullable=False,
    )

    organization = db.relationship(
        "Organization",
        backref=db.backref("subscription", uselist=False, lazy="select"),
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<Subscription org={self.organization_id} "
            f"plan={self.plan.value} status={self.status.value}>"
        )


class BillingEvent(TenantMixin, db.Model):  # migration-exempt
    """One row per payment-provider event already applied to an organisation.

    The provider retries deliveries, so the same event can arrive more than
    once; a second delivery of a recorded event id is acknowledged and
    ignored.
    """

    __tablename__ = "billing_events"

    id = db.Column(db.Integer, primary_key=True)
    provider_event_id = db.Column(db.String(255), nullable=False, unique=True, index=True)
    event_type = db.Column(db.String(100), nullable=False)
    received_at = db.Column(db.DateTime, default=db.func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<BillingEvent {self.provider_event_id} {self.event_type}>"
