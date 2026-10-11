from __future__ import annotations

from datetime import datetime, UTC

from app import db
from app.models.mixins import TenantMixin


class ExternalIdentityCrosswalk(TenantMixin, db.Model):  # migration-exempt
    """Tenant-scoped mapping from one connector identifier to one internal element.

    The source-system/external-id pair is authoritative within one organisation:
    a re-import updates the linked element instead of creating a duplicate row.
    """

    __tablename__ = "external_identity_crosswalk"
    __table_args__ = (
        db.UniqueConstraint(
            "organization_id",
            "source_system",
            "external_id",
            name="uq_external_identity_crosswalk_org_source_external",
        ),
        {"extend_existing": True},
    )

    id = db.Column(db.Integer, primary_key=True)
    source_system = db.Column(db.String(100), nullable=False, index=True)
    external_id = db.Column(db.String(255), nullable=False, index=True)
    element_id = db.Column(
        db.Integer,
        db.ForeignKey("archimate_elements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    confidence = db.Column(db.Float, nullable=False, default=1.0, server_default="1")
    first_seen = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))
    last_seen = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )

    element = db.relationship("ArchiMateElement", lazy="select")

    def __repr__(self) -> str:
        return (
            f"<ExternalIdentityCrosswalk {self.organization_id}:"
            f"{self.source_system}:{self.external_id} -> {self.element_id}>"
        )
