"""
UsageEvent model — per-event metering record for billing and seat enforcement.

Every significant platform action (solution created, AI query, codegen run, etc.)
writes a row here so that monthly summaries and seat-count enforcement can be
computed without touching the core business tables.
"""

from datetime import datetime

from app.extensions import db
from sqlalchemy import Index


class UsageEvent(db.Model):
    """Metering record for a single platform action within an organization."""

    __tablename__ = "usage_events"

    # Valid event_type values (informational — not enforced at DB level)
    EVENT_SOLUTION_CREATED = "solution_created"
    EVENT_SOLUTION_EXPORTED = "solution_exported"
    EVENT_AI_QUERY = "ai_query"
    EVENT_CODEGEN_RUN = "codegen_run"
    EVENT_USER_LOGIN = "user_login"
    EVENT_BLUEPRINT_GENERATED = "blueprint_generated"
    EVENT_MCP_TOOL_CALL = "mcp_tool_call"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer,
        db.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type = db.Column(db.String(100), nullable=False)
    resource_type = db.Column(db.String(100), nullable=True)
    resource_id = db.Column(db.Integer, nullable=True)
    event_metadata = db.Column(db.JSON, default=dict, nullable=False)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Composite index for fast monthly aggregation queries
    __table_args__ = (
        Index("ix_usage_events_org_type_date", "organization_id", "event_type", "recorded_at"),
    )

    def __repr__(self):
        return (
            f"<UsageEvent org={self.organization_id} type={self.event_type!r} "
            f"at={self.recorded_at}>"
        )
