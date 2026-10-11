"""
Core Model Mixins

Provides essential mixins for SQLAlchemy models including soft delete,
timestamps, auditing, hierarchy, and status management.
"""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import backref, relationship


def _default_org_id():
    """Column default for tenant organization_id.

    Resolves the current request's organization (set by the tenant_context
    middleware on g.current_org_id) so inserts that bypass the tenant before_flush
    listener — raw Table.insert() in model events, seeders, bulk paths — still get
    an org rather than violating the NOT NULL constraint.

    Outside a request (CLI seeders, background jobs) there is no g.current_org_id.
    For a single-tenant install (exactly one Organization — the default for a
    self-hosted deployment) fall back to that org so `flask seed ...` and similar
    work out of the box. When several organizations exist we cannot safely guess
    the tenant, so we return None and let the NOT NULL constraint surface the
    missing context rather than silently writing into the wrong tenant.
    """
    try:
        from flask import g, has_request_context

        if has_request_context():
            return getattr(g, "current_org_id", None)
    except Exception:
        pass
    try:
        from app import db
        from app.models.organization import Organization

        with db.session.no_autoflush:
            ids = (
                db.session.query(Organization.id)
                .order_by(Organization.id.asc())
                .limit(2)
                .all()
            )
        if len(ids) == 1:
            return ids[0][0]
    except Exception:
        pass
    return None


class TenantMixin:  # migration-exempt
    """Add to every model that holds tenant-specific business data.

    Provides an organization_id FK column and relationship. The SQLAlchemy
    event listener in app.middleware.tenant_isolation automatically filters
    SELECTs and auto-sets organization_id on INSERTs.
    """

    @declared_attr
    def organization_id(cls):
        from app import db
        return db.Column(
            db.Integer,
            db.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
            # Belt-and-suspenders: the tenant before_flush sets this for ORM inserts,
            # but raw Table.insert() (model events, seeders) bypasses it. This column
            # default fills the org from the request context for those paths too.
            default=_default_org_id,
        )

    @declared_attr
    def organization(cls):
        from app import db
        return db.relationship("Organization", lazy="select")


class HybridTenantMixin:  # migration-exempt
    """Add to a shared-catalogue model: platform-wide rows readable by every
    organisation, writable only by the platform, tailored through separate
    per-organisation override rows.

    Generalises the pattern first written as ``HybridCapabilityTenantMixin``
    in app/models/unified_capability.py. Deliberately
    NOT ``TenantMixin``: that mixin's ``do_orm_execute`` equality filter would
    hide every shared row (``organization_id IS NULL``) from every organisation,
    which is the opposite of "shared". A hybrid-scoped model reads and writes
    through its own explicit queries/event handlers instead (the read-only
    enforcement and per-organisation tailoring are a separate, later change,
    not this column).

    Nullable forever, not as an expand-step waiting for a later NOT NULL
    tightening: a shared catalogue row legitimately has no owning organisation.
    """

    @declared_attr
    def organization_id(cls):
        from app import db
        return db.Column(
            db.Integer,
            db.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        )


class OptimisticLockMixin:
    """Mixin for SQLAlchemy-enforced optimistic locking.

    Adds a version column that auto-increments on every UPDATE.
    SQLAlchemy raises StaleDataError if a concurrent session
    modified the row between read and write.
    """

    version = Column(Integer, default=1, nullable=False, server_default="1")

    @declared_attr
    def __mapper_args__(cls):
        return {"version_id_col": cls.__table__.c.version}


class SoftDeleteMixin:
    """Soft delete mixin for models."""

    deleted_at = Column(DateTime, nullable=True)
    is_deleted = Column(Boolean, default=False, nullable=False)

    def soft_delete(self):
        """Mark the record as deleted."""
        self.deleted_at = datetime.utcnow()
        self.is_deleted = True

    def restore(self):
        """Restore a soft-deleted record."""
        self.deleted_at = None
        self.is_deleted = False


class TimestampMixin:
    """Timestamp mixin for models."""

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AuditMixin:
    """Audit mixin for models."""

    created_by = Column(Integer, nullable=True)
    updated_by = Column(Integer, nullable=True)
    audit_notes = Column(Text, nullable=True)


class HierarchyMixin:
    """Hierarchy mixin for models with parent-child relationships."""

    parent_id = Column(Integer, nullable=True)
    level = Column(Integer, default=0, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)

    @declared_attr
    def children(cls):
        return relationship(cls, backref=backref("parent", remote_side=[cls.id]))


class StatusMixin:
    """Status mixin for models."""

    status = Column(String(50), default="active", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    def activate(self):
        """Set the model as active."""
        self.status = "active"
        self.is_active = True

    def deactivate(self):
        """Set the model as inactive."""
        self.status = "inactive"
        self.is_active = False


__all__ = ["OptimisticLockMixin", "SoftDeleteMixin", "TimestampMixin", "AuditMixin", "HierarchyMixin", "StatusMixin"]
