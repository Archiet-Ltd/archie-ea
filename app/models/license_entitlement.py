# migration-exempt
"""
License Entitlement Model (NS-004)

Tracks software license entitlements and usage for Procurement persona.
Part of North Star Persona MVP implementation.

ADR Reference: docs/adr/0010-procurement-persona.md
"""

from datetime import datetime
from decimal import Decimal

from .. import db
from .mixins import TenantMixin


class LicenseEntitlement(TenantMixin, db.Model):
    """
    License entitlement tracking for Procurement workflows.

    Tenant-scoped: inherits TenantMixin so the automatic org filter applies.
    It held organization_id but not the mixin, so nothing filtered its SELECTs
    and isolation depended on every caller remembering .filter_by(organization_id=)
    - the same shape of gap that let one tenant delete another's documents. The
    explicit organization_id column below overrides the mixin's declared_attr with
    an identical definition, exactly as VendorContract does, so this is a
    behavioural change only and needs no migration.

    Tracks:
    - License type (named user, concurrent, device, etc.)
    - Quantities (entitled, deployed, used)
    - Compliance status (compliant, over-deployed, under-utilized)
    - Cost per unit

    Enables:
    - Shelfware detection (entitled but not used)
    - Compliance tracking (used > entitled)
    - True-up planning
    """
    __tablename__ = "license_entitlements"
    __table_args__ = {"extend_existing": True}

    id = db.Column(db.Integer, primary_key=True)

    # Foreign keys
    contract_id = db.Column(
        db.Integer,
        db.ForeignKey("vendor_contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    application_id = db.Column(
        db.Integer,
        db.ForeignKey("application_components.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # What the licence is for. license_detail.html renders this as the page title
    # and compliance_dashboard.html as the row heading, but the column did not
    # exist - so every licence displayed a blank name. Nullable, because
    # reconcile-schema only adds nullable columns and existing rows have no value
    # to backfill from; the UI falls back to the linked application's name.
    product_name = db.Column(db.String(200), nullable=True)

    # License details
    license_type = db.Column(
        db.String(50),
        nullable=False,
    )  # 'named_user', 'concurrent', 'device', 'core', 'site'
    license_metric = db.Column(
        db.String(100),
        nullable=True,
    )  # 'per user', 'per core', 'per device', etc.

    # Quantities
    quantity_entitled = db.Column(db.Integer, nullable=False, default=0)
    # Deployed and used are often not known when an entitlement is recorded.
    # NULL means "not recorded" and renders as a dash; a stored 0 would read as
    # a measured "nobody uses this". Existing databases are relaxed by
    # `flask reconcile-schema`.
    quantity_deployed = db.Column(db.Integer, nullable=True)
    quantity_used = db.Column(db.Integer, nullable=True)

    # Cost
    unit_cost = db.Column(db.Numeric(10, 2), nullable=True)

    # Compliance
    # 'compliant', 'over_deployed', 'under_utilized', or NULL while deployment
    # is not recorded. No column default: SQLAlchemy applies a default when the
    # attribute is None, which would file an unmeasured licence as compliant.
    compliance_status = db.Column(db.String(50))

    # Sync tracking
    last_usage_sync = db.Column(db.DateTime, nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    contract = db.relationship(
        "VendorContract",
        backref=db.backref("license_entitlements", lazy="dynamic", cascade="all, delete-orphan"),
    )
    application = db.relationship(
        "ApplicationComponent",
        backref=db.backref("license_entitlements", lazy="dynamic"),
    )
    organization = db.relationship(
        "Organization",
        backref=db.backref("license_entitlements", lazy="dynamic"),
    )

    # Valid values
    LICENSE_TYPES = ["named_user", "concurrent", "device", "core", "site"]
    COMPLIANCE_STATUSES = ["compliant", "over_deployed", "under_utilized"]

    def __repr__(self):
        return f"<LicenseEntitlement {self.id}: {self.license_type} x{self.quantity_entitled}>"

    def to_dict(self):
        return {
            "id": self.id,
            "contract_id": self.contract_id,
            "application_id": self.application_id,
            "license_type": self.license_type,
            "license_metric": self.license_metric,
            "quantity_entitled": self.quantity_entitled,
            "quantity_deployed": self.quantity_deployed,
            "quantity_used": self.quantity_used,
            "unit_cost": float(self.unit_cost) if self.unit_cost else None,
            "compliance_status": self.compliance_status,
            "utilization_percent": self.utilization_percent,
            "organization_id": self.organization_id,
        }

    @property
    def vendor(self):
        """The vendor, through the contract the entitlement is held under."""
        return self.contract.vendor if self.contract is not None else None

    @property
    def utilization_percent(self):
        """Used as a percentage of entitled; None when usage is not recorded."""
        if self.quantity_used is None:
            return None
        if not self.quantity_entitled or self.quantity_entitled == 0:
            return 0
        return round((self.quantity_used / self.quantity_entitled) * 100, 1)

    @property
    def deployment_percent(self):
        """Deployed as a percentage of entitled; None when not recorded."""
        if self.quantity_deployed is None:
            return None
        if not self.quantity_entitled or self.quantity_entitled == 0:
            return 0
        return round((self.quantity_deployed / self.quantity_entitled) * 100, 1)

    @property
    def available_quantity(self):
        """Entitled minus deployed; None when deployment is not recorded."""
        if self.quantity_deployed is None or self.quantity_entitled is None:
            return None
        return self.quantity_entitled - self.quantity_deployed

    @property
    def total_cost(self):
        """Calculate total cost for entitled quantity."""
        if not self.unit_cost or not self.quantity_entitled:
            return Decimal("0.00")
        return self.unit_cost * self.quantity_entitled

    @property
    def unused_value(self):
        """Calculate value of unused licenses (shelfware).

        Without a unit cost or recorded usage there is nothing to value, so
        the entitlement contributes nothing to a shelfware total.
        """
        if not self.unit_cost or self.quantity_used is None:
            return Decimal("0.00")
        unused = max(0, self.quantity_entitled - self.quantity_used)
        return self.unit_cost * unused

    def update_compliance_status(self):
        """Update compliance status based on quantities (None when unknown)."""
        if self.quantity_deployed is None:
            self.compliance_status = None
        elif self.quantity_deployed > self.quantity_entitled:
            self.compliance_status = "over_deployed"
        elif self.utilization_percent is not None and self.utilization_percent < 50:
            self.compliance_status = "under_utilized"
        else:
            self.compliance_status = "compliant"

    @classmethod
    def get_compliance_summary(cls, organization_id):
        """Get compliance summary for organization."""
        entitlements = cls.query.filter(
            cls.organization_id == organization_id,
        ).all()

        summary = {
            "total": len(entitlements),
            "compliant": 0,
            "over_deployed": 0,
            "under_utilized": 0,
            "total_value": Decimal("0.00"),
            "unused_value": Decimal("0.00"),
        }

        for e in entitlements:
            status = e.compliance_status or "unknown"
            summary[status] = summary.get(status, 0) + 1
            summary["total_value"] += e.total_cost
            summary["unused_value"] += e.unused_value

        return summary
