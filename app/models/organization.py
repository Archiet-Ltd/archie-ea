"""
Organization model — tenant boundary for multi-tenancy.

Every piece of business data belongs to exactly one organization.
System-wide config (roles, permissions, feature flags, templates) is shared.
"""

from app import db

#: The one shared organisation every unassigned sign-up lands in (the slug
#: ``app/models/user.py``'s ``_assign_default_organization`` listener seeds on first
#: use). The single place this product names it; every caller that needs to
#: recognise or resolve the Default organisation goes through ``Organization.is_default``
#: / ``Organization.default_organization`` below instead of repeating the slug.
DEFAULT_ORGANIZATION_SLUG = "default"


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
    reporting_currency = db.Column(db.String(3), server_default="GBP")
    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    users = db.relationship("User", backref="organization", lazy="dynamic")

    def __repr__(self):
        return f"<Organization '{self.name}' ({self.slug})>"

    @classmethod
    def is_default(cls, org):
        """True when ``org`` is the Default organisation."""
        return org is not None and org.slug == DEFAULT_ORGANIZATION_SLUG

    @classmethod
    def default_organization(cls, create=False):
        """Return the Default organisation, or ``None`` when no row names it yet.

        With ``create=True``, seeds it first (same name and slug as the
        ``before_insert`` listener on ``User`` creates on first use) when the
        database holds none -- for a caller, such as a test fixture building a
        fresh database, that needs the row to exist rather than merely to look
        for it.
        """
        row = cls.query.filter_by(slug=DEFAULT_ORGANIZATION_SLUG).first()
        if row is not None or not create:
            return row
        row = cls(name="Default Organization", slug=DEFAULT_ORGANIZATION_SLUG)
        db.session.add(row)
        db.session.flush()
        return row


class OrganizationDeletionInProgress(db.Model):  # migration-exempt
    """One row per organisation whose deletion the current transaction is carrying out.

    The delete-refusing guards on append-only history admit a DELETE only while this row exists for
    the row's own organisation, as well as the transaction-local marker. The deletion service writes
    the row first and removes it before it commits, so no other transaction ever sees one; setting
    the marker alone opens nothing.
    """

    __tablename__ = "organization_deletions_in_progress"

    target_organization_id = db.Column(db.Integer, primary_key=True, autoincrement=False)
    started_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    # The transaction that wrote the row; the guards admit a delete only to that transaction.
    written_by_txid = db.Column(db.BigInteger, nullable=False, server_default=db.text("txid_current()"))
