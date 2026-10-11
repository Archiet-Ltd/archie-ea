"""
Organization model — tenant boundary for multi-tenancy.

Every piece of business data belongs to exactly one organization.
System-wide config (roles, permissions, feature flags, templates) is shared.
"""

from app import db


class Organization(db.Model):  # migration-exempt
    """Tenant boundary. Every piece of business data belongs to exactly one org."""

    __tablename__ = "organizations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False, index=True)
    # Retired: the plan and its people limit live on the organisation's
    # subscriptions row (app/services/billing_plans.py). plan / max_users are
    # read only to seed that row for an organisation created before billing
    # existed, and nothing writes them any more.
    plan = db.Column(db.String(50), default="free")
    is_active = db.Column(db.Boolean, default=True)
    settings = db.Column(db.JSON, default=dict)  # org-level config overrides
    max_users = db.Column(db.Integer, default=10)  # retired, see plan above
    # Currency the organisation reports costs in; NULL means the platform default
    # (read through application_cost_accessor.get_reporting_currency).
    reporting_currency = db.Column(db.String(3))
    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    users = db.relationship("User", backref="organization", lazy="dynamic")

    def __repr__(self):
        return f"<Organization '{self.name}' ({self.slug})>"
