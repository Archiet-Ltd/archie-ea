"""WorkPackageResourceDemand: the effort a work package is recorded as needing, per role and period.

Capacity is a comparison of recorded supply to recorded demand over a stated period. Until now
demand existed only as ``UnifiedWorkPackage.required_resources``, an unstructured JSON column
that is not parsed. This table records demand as rows that can be compared, and nothing else:
there is no reader yet, and no row is ever inferred or defaulted, so a work package with no rows
has "demand not recorded", never zero.

FTE rows are converted to person-days over the period at read time (the reader's job), not here.

New table, ADD-only via create_all: no existing-database impact and no backfill.
"""

from datetime import datetime

from app import db
from app.models.mixins import TenantMixin

EFFORT_UNITS = ("person_day", "fte")


class WorkPackageResourceDemand(TenantMixin, db.Model):
    __tablename__ = "work_package_resource_demand"
    __table_args__ = (
        db.CheckConstraint("effort_value > 0", name="ck_wp_demand_effort_positive"),
        db.CheckConstraint("period_end >= period_start", name="ck_wp_demand_period_order"),
        db.CheckConstraint("effort_unit IN ('person_day', 'fte')", name="ck_wp_demand_effort_unit"),
        db.Index("ix_wp_demand_org_work_package", "organization_id", "work_package_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    work_package_id = db.Column(
        db.BigInteger,
        db.ForeignKey("unified_work_packages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = db.Column(db.String(120), nullable=False)
    capability_id = db.Column(
        db.BigInteger, db.ForeignKey("unified_capabilities.id", ondelete="SET NULL"), nullable=True
    )
    effort_value = db.Column(db.Numeric(10, 2), nullable=False)
    effort_unit = db.Column(db.String(12), nullable=False)  # EFFORT_UNITS
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    source = db.Column(db.String(60), nullable=True)  # who or what recorded it
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    work_package = db.relationship(
        "UnifiedWorkPackage",
        backref=db.backref("resource_demand", cascade="all, delete-orphan", passive_deletes=True),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "work_package_id": self.work_package_id,
            "role": self.role,
            "capability_id": self.capability_id,
            "effort_value": str(self.effort_value),
            "effort_unit": self.effort_unit,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "source": self.source,
        }
