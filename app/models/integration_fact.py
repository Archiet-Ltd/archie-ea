"""Integration Fact Model

Records facts derived from external systems (GitHub, Jira, etc.) before they are
applied to the ArchiMate backbone.
"""
from datetime import datetime
from enum import Enum

from app import db
from app.models.mixins.core import TenantMixin


class IntegrationFactStatus(Enum):
    """Status of an integration fact."""
    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"


class IntegrationFact(TenantMixin, db.Model):
    """
    Records a single fact derived from an external system.

    A fact is one ArchiMate element, relationship, or owner derived from an
    external item. Facts are recorded first, then approved/rejected, then applied
    through the canonical writers.
    """

    __tablename__ = "integration_facts"
    __table_args__ = (
        db.UniqueConstraint(
            "organization_id", "system", "external_id", "path",
            name="uq_integration_fact_system_id_path"
        ),
        db.Index("ix_integration_facts_org_system_status", "organization_id", "system", "status"),
    )

    id = db.Column(db.Integer, primary_key=True)

    # Which external system this fact came from
    system = db.Column(db.String(50), nullable=False)  # github, jira, servicenow, etc.

    # The external item's ID in that system
    external_id = db.Column(db.String(255), nullable=False)

    # Path to this fact within the mapped item (e.g., "element", "relationships.0")
    path = db.Column(db.String(100), nullable=False)

    # Type of fact
    fact_type = db.Column(db.String(20), nullable=False)  # element, relationship, owner

    # The fact data (JSON)
    fact_data = db.Column(db.Text, nullable=False)

    # Source metadata
    source_url = db.Column(db.Text, nullable=True)
    source_item_type = db.Column(db.String(50), nullable=True)  # repo, issue, service, etc.
    source_read_at = db.Column(db.DateTime, nullable=True)

    # Status
    status = db.Column(db.Enum(IntegrationFactStatus), default=IntegrationFactStatus.PENDING, nullable=False)

    # When applied
    applied_at = db.Column(db.DateTime, nullable=True)
    applied_element_id = db.Column(db.Integer, nullable=True)  # For element facts
    # The link an applied relationship or team-owner fact wrote, so a citation of
    # that link names the system and item it was read from.
    applied_relationship_id = db.Column(db.Integer, nullable=True, index=True)

    # When rejected
    rejection_reason = db.Column(db.Text, nullable=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self):
        """Convert to dictionary."""
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "system": self.system,
            "external_id": self.external_id,
            "path": self.path,
            "fact_type": self.fact_type,
            "fact_data": self.fact_data,
            "source_url": self.source_url,
            "source_item_type": self.source_item_type,
            "source_read_at": self.source_read_at.isoformat() if self.source_read_at else None,
            "status": self.status.value,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "applied_element_id": self.applied_element_id,
            "applied_relationship_id": self.applied_relationship_id,
            "rejection_reason": self.rejection_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
