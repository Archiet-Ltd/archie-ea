"""A licence seat count measured at a source system.

``LicenseEntitlement.quantity_used`` is typed by hand. This record is the
measured counterpart: one row per fetch, naming the entitlement it measures,
the seat count, the source system and the time it was fetched. The two figures
are kept apart; a typed figure is never recorded here and a measurement never
overwrites the typed one.
"""

from datetime import datetime

from .. import db
from .mixins import TenantMixin

SOURCE_M365 = "m365"
SOURCE_LABELS = {SOURCE_M365: "Microsoft 365"}


class LicenseUsageMeasurement(TenantMixin, db.Model):
    __tablename__ = "license_usage_measurements"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entitlement_id = db.Column(
        db.Integer,
        db.ForeignKey("license_entitlements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seats_used = db.Column(db.Integer, nullable=False)
    source = db.Column(db.String(50), nullable=False)
    # The source's own name for what was counted (for Microsoft 365, the SKU).
    source_ref = db.Column(db.String(200), nullable=True)
    connector_id = db.Column(db.String(64), nullable=True)
    fetched_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    entitlement = db.relationship(
        "LicenseEntitlement",
        backref=db.backref("usage_measurements", lazy="dynamic", cascade="all, delete-orphan"),
    )

    @property
    def source_label(self):
        return SOURCE_LABELS.get(self.source, self.source)

    def __repr__(self):
        return f"<LicenseUsageMeasurement {self.entitlement_id}: {self.seats_used} via {self.source}>"
