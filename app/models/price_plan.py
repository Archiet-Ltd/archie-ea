"""One price plan store.

A ``PricePlan`` is what a product or service costs the customer who buys it: a
price in a currency per billing period, with the share of revenue it brings in
where that is recorded. It belongs to an ArchiMate element (the product or
business service), so the Business Model Canvas's Revenue Streams box reads it
rather than keeping an amount as a free property of the element.
"""

from datetime import datetime

from .. import db
from .mixins import TenantMixin

__all__ = ["PricePlan", "BILLING_PERIODS", "PRICING_MECHANISMS"]

BILLING_PERIODS = ("month", "year", "use")
PRICING_MECHANISMS = ("usage fee", "subscription", "licensing", "brokerage", "sale")


class PricePlan(TenantMixin, db.Model):
    __tablename__ = "price_plans"

    id = db.Column(db.Integer, primary_key=True)
    element_id = db.Column(db.Integer, db.ForeignKey("archimate_elements.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    price = db.Column(db.Numeric(18, 4), nullable=True)
    currency = db.Column(db.String(3), nullable=True)
    billing_period = db.Column(db.String(16), nullable=True)
    pricing_mechanism = db.Column(db.String(32), nullable=True)
    revenue_share = db.Column(db.Numeric(5, 2), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
