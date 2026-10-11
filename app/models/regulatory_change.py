"""
Regulatory change tracker.

Records amendments to regulatory frameworks and tracks which obligations,
controls and element owners are affected when the applicability engine re-runs.
"""

from datetime import datetime, timezone

from app import db
from app.models.mixins import TenantMixin


def _now_utc_naive():
    """Return naive UTC datetime for TIMESTAMP WITHOUT TIME ZONE columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RegulatoryChange(TenantMixin, db.Model):
    """A recorded amendment or update to a regulatory framework.

    When a regulator issues a change, the regulator liaison records it here.
    The applicability engine then re-runs to identify affected obligations,
    controls and element owners.
    """

    __tablename__ = "regulatory_changes"

    id = db.Column(db.Integer, primary_key=True)

    framework_id = db.Column(
        db.Integer, db.ForeignKey("regulatory_frameworks.id"), nullable=False, index=True
    )
    change_type = db.Column(
        db.String(30), nullable=False, default="amendment"
    )  # amendment, new_version, deprecation, guidance_update

    title = db.Column(db.String(500), nullable=False)
    description = db.Column(db.Text)
    effective_date = db.Column(db.Date, nullable=True)
    source_url = db.Column(db.String(500))

    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=_now_utc_naive)
    updated_at = db.Column(db.DateTime, default=_now_utc_naive, onupdate=_now_utc_naive)

    # Relationships
    framework = db.relationship("RegulatoryFramework", backref="regulatory_changes")
    recorded_by = db.relationship("User", foreign_keys=[recorded_by_id])
    affected_items = db.relationship(
        "RegulatoryChangeImpact", back_populates="change", lazy="dynamic", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<RegulatoryChange {self.title}>"


class RegulatoryChangeImpact(TenantMixin, db.Model):
    """An element affected by a regulatory change, as computed by the applicability engine."""

    __tablename__ = "regulatory_change_impacts"

    id = db.Column(db.Integer, primary_key=True)

    change_id = db.Column(
        db.Integer, db.ForeignKey("regulatory_changes.id"), nullable=False, index=True
    )

    element_type = db.Column(
        db.String(50), nullable=False
    )  # control, application, node, capability
    element_id = db.Column(db.Integer, nullable=False)
    element_name = db.Column(db.String(500))

    impact_assessment = db.Column(db.Text)
    owner_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=_now_utc_naive)

    # Relationships
    change = db.relationship("RegulatoryChange", back_populates="affected_items")
    owner = db.relationship("User", foreign_keys=[owner_id])

    def __repr__(self):
        return f"<RegulatoryChangeImpact {self.element_type}:{self.element_id}>"