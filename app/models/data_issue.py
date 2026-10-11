"""Data issue model (R1-B81 policy/issue/glossary slice, PB-0292).

A data steward raises an issue against a DataEntity; it is routed for
display to the entity's domain's recorded steward (the legacy free-text
`DataDomain.data_steward` column, read-only here per the brief's own
"never invent data" rule -- the real steward-picker writer is the
ownership slice of this brief, blocked on R1-B03 PR 2, not this one).
Resolved with a recorded fix; the reporter is notified via Notification,
R1-B37's existing mechanism, not a new one.
"""
from datetime import datetime

from .. import db
from .mixins import TenantMixin


class DataIssue(TenantMixin, db.Model):
    """One data-quality or governance issue raised against a DataEntity."""

    __tablename__ = "data_issues"

    id = db.Column(db.Integer, primary_key=True)
    data_entity_id = db.Column(
        db.Integer, db.ForeignKey("data_entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    status = db.Column(db.String(20), default="open", nullable=False)  # open, resolved
    reporter_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    resolution_notes = db.Column(db.Text, nullable=True)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    data_entity = db.relationship("DataEntity")
    reporter = db.relationship("User", foreign_keys=[reporter_id])
    resolved_by = db.relationship("User", foreign_keys=[resolved_by_id])

    def __repr__(self):
        return f"<DataIssue {self.title} ({self.status})>"


__all__ = ["DataIssue"]
