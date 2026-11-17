"""Stakeholder Communication Log model for TPM-012.

Enables TPMs to log communications (meetings, emails, decisions) with
stakeholders against a solution and retrieve a chronological log.
"""

import enum
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, Integer, JSON, String, Text

from app import db
from app.models.mixins.core import TenantMixin


class CommunicationType(enum.Enum):
    MEETING = "meeting"
    EMAIL = "email"
    DECISION = "decision"
    ESCALATION = "escalation"
    STATUS_UPDATE = "status_update"


class StakeholderCommunication(TenantMixin, db.Model):
    """Log entry for a communication event linked to a solution and stakeholder."""

    __tablename__ = "stakeholder_communications"

    id = Column(Integer, primary_key=True)
    # organization_id comes from TenantMixin; it is stamped from the signed-in user.
    solution_id = Column(Integer, nullable=True)
    stakeholder_id = Column(Integer, nullable=True)
    comm_type = Column(Enum(CommunicationType), nullable=False)
    subject = Column(String(255), nullable=False)
    summary = Column(Text, nullable=True)
    outcome = Column(Text, nullable=True)
    # [{"item": str, "owner": str, "due": str}]
    action_items = Column(JSON, default=list)
    logged_at = Column(DateTime, default=datetime.utcnow)
    logged_by = Column(Integer, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "solution_id": self.solution_id,
            "stakeholder_id": self.stakeholder_id,
            "comm_type": self.comm_type.value if self.comm_type else None,
            "subject": self.subject,
            "summary": self.summary,
            "outcome": self.outcome,
            "action_items": self.action_items or [],
            "logged_at": self.logged_at.isoformat() if self.logged_at else None,
            "logged_by": self.logged_by,
        }

    def __repr__(self):
        return f"<StakeholderCommunication {self.id}: {self.comm_type.value} — {self.subject}>"
