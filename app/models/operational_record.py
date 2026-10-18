"""One organisation-scoped operational record: an incident, a problem or a change.

Every service-management source writes this table through an operational
source adapter (``app/modules/intelligence/services/operational_source.py``).
Each row is linked to exactly one twin element, resolved through the external
identifier crosswalk; a record whose configuration item resolves to no element
is reported by the adapter and never stored.
"""

from __future__ import annotations

from datetime import datetime, UTC

from app import db
from app.models.mixins import TenantMixin

KIND_INCIDENT = "incident"
KIND_PROBLEM = "problem"
KIND_CHANGE = "change"
OPERATIONAL_KINDS = (KIND_INCIDENT, KIND_PROBLEM, KIND_CHANGE)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class OperationalRecord(TenantMixin, db.Model):  # migration-exempt
    """An incident, problem or change reported by an external source system."""

    __tablename__ = "operational_records"
    __table_args__ = (
        db.UniqueConstraint(
            "organization_id",
            "source_system",
            "kind",
            "external_id",
            name="uq_operational_records_org_source_kind_external",
        ),
        db.CheckConstraint(
            "kind IN ('incident', 'problem', 'change')",
            name="ck_operational_records_kind",
        ),
        db.Index("ix_operational_records_org_element_opened", "organization_id", "element_id", "opened_at"),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(16), nullable=False, index=True)
    source_system = db.Column(db.String(100), nullable=False, index=True)
    external_id = db.Column(db.String(255), nullable=False)
    reference = db.Column(db.String(100), nullable=True)
    title = db.Column(db.String(500), nullable=True)
    state = db.Column(db.String(100), nullable=True)
    severity = db.Column(db.String(50), nullable=True)
    opened_at = db.Column(db.DateTime, nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    closed_at = db.Column(db.DateTime, nullable=True)
    source_updated_at = db.Column(db.DateTime, nullable=True)
    element_id = db.Column(
        db.Integer,
        db.ForeignKey("archimate_elements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now, onupdate=_now)

    element = db.relationship("ArchiMateElement", lazy="select")

    def __repr__(self) -> str:
        return (
            f"<OperationalRecord {self.organization_id}:{self.source_system}:"
            f"{self.kind}:{self.external_id} -> {self.element_id}>"
        )
