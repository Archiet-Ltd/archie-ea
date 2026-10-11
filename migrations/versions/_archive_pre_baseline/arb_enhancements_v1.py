"""ARB Enhancements - Readiness, Exceptions, Audit, Watchers, Templates

Revision ID: arb_enhancements_v1
Revises: add_arb_decision_fields
Create Date: 2026-01-26 23:30:00.000000

Adds new tables for ARB enhancement features:
- arb_readiness_checks: Pre-submission validation tracking
- arb_exceptions: Exception/waiver requests
- arb_audit_logs: Comprehensive audit trail
- arb_watchers: Watch/subscribe functionality
- arb_session_templates: Recurring session templates

Also adds new columns to arb_review_items for readiness and version tracking.
"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "arb_enhancements_v1"
down_revision = "add_arb_decision_fields"
branch_labels = None
depends_on = None


def upgrade():
    """Create new ARB enhancement tables and columns."""

    # 1. ARB Readiness Checks table
    op.create_table(
        "arb_readiness_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "review_item_id", sa.Integer(), sa.ForeignKey("arb_review_items.id"), nullable=False
        ),
        sa.Column(
            "standard_id", sa.Integer(), sa.ForeignKey("arb_governance_standards.id"), nullable=True
        ),
        sa.Column("check_type", sa.String(50), nullable=False),
        sa.Column("check_key", sa.String(100), nullable=False),
        sa.Column("check_description", sa.String(500), nullable=True),
        sa.Column("is_required", sa.Boolean(), default=True),
        sa.Column("is_satisfied", sa.Boolean(), default=False),
        sa.Column("validation_message", sa.String(500), nullable=True),
        sa.Column("satisfied_at", sa.DateTime(), nullable=True),
        sa.Column("satisfied_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), default=datetime.utcnow),
        sa.Column("updated_at", sa.DateTime(), default=datetime.utcnow, onupdate=datetime.utcnow),
    )

    # 2. ARB Exceptions table
    op.create_table(
        "arb_exceptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exception_number", sa.String(50), unique=True, nullable=False),
        sa.Column(
            "review_item_id", sa.Integer(), sa.ForeignKey("arb_review_items.id"), nullable=True
        ),
        sa.Column(
            "standard_id",
            sa.Integer(),
            sa.ForeignKey("arb_governance_standards.id"),
            nullable=False,
        ),
        sa.Column("exception_type", sa.String(50), nullable=True),
        sa.Column("exception_reason", sa.Text(), nullable=False),
        sa.Column("business_justification", sa.Text(), nullable=True),
        sa.Column("risk_mitigation", sa.Text(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), default="requested"),
        sa.Column("requested_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("requested_at", sa.DateTime(), default=datetime.utcnow),
        sa.Column("reviewed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("approved_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("approval_notes", sa.Text(), nullable=True),
        sa.Column("denied_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("denied_at", sa.DateTime(), nullable=True),
        sa.Column("denial_reason", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column(
            "parent_exception_id", sa.Integer(), sa.ForeignKey("arb_exceptions.id"), nullable=True
        ),
        sa.Column("renewal_count", sa.Integer(), default=0),
        sa.Column("created_at", sa.DateTime(), default=datetime.utcnow),
        sa.Column("updated_at", sa.DateTime(), default=datetime.utcnow, onupdate=datetime.utcnow),
    )

    # 3. ARB Audit Logs table
    op.create_table(
        "arb_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(50), nullable=False, index=True),
        sa.Column("entity_id", sa.Integer(), nullable=False, index=True),
        sa.Column("entity_reference", sa.String(100), nullable=True),
        sa.Column("action", sa.String(30), nullable=False, index=True),
        sa.Column("action_description", sa.String(500), nullable=True),
        sa.Column("old_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("changed_fields", sa.JSON(), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("user_email", sa.String(255), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("request_id", sa.String(100), nullable=True),
        sa.Column("timestamp", sa.DateTime(), default=datetime.utcnow, nullable=False, index=True),
    )
    op.create_index("ix_arb_audit_entity", "arb_audit_logs", ["entity_type", "entity_id"])
    op.create_index("ix_arb_audit_user_time", "arb_audit_logs", ["user_id", "timestamp"])

    # 4. ARB Watchers table
    op.create_table(
        "arb_watchers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("watch_level", sa.String(30), default="all"),
        sa.Column("created_at", sa.DateTime(), default=datetime.utcnow),
        sa.UniqueConstraint("user_id", "entity_type", "entity_id", name="uix_arb_watcher"),
    )
    op.create_index("ix_arb_watcher_entity", "arb_watchers", ["entity_type", "entity_id"])

    # 5. ARB Session Templates table
    op.create_table(
        "arb_session_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("frequency", sa.String(20), nullable=True),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column("time_of_day", sa.Time(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), default=120),
        sa.Column("default_location", sa.String(255), nullable=True),
        sa.Column("default_meeting_link", sa.String(500), nullable=True),
        sa.Column("default_chair_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("default_secretary_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("default_members", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("last_generated_at", sa.DateTime(), nullable=True),
        sa.Column("next_scheduled_date", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), default=datetime.utcnow),
        sa.Column("updated_at", sa.DateTime(), default=datetime.utcnow, onupdate=datetime.utcnow),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )

    # 6. Add new columns to arb_review_items table
    op.add_column("arb_review_items", sa.Column("readiness_score", sa.Float(), nullable=True))
    op.add_column(
        "arb_review_items", sa.Column("readiness_checked_at", sa.DateTime(), nullable=True)
    )
    op.add_column("arb_review_items", sa.Column("readiness_warnings", sa.JSON(), nullable=True))
    op.add_column(
        "arb_review_items", sa.Column("version", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column(
        "arb_review_items",
        sa.Column("amendment_count", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade():
    """Remove ARB enhancement tables and columns."""

    # Remove columns from arb_review_items
    op.drop_column("arb_review_items", "amendment_count")
    op.drop_column("arb_review_items", "version")
    op.drop_column("arb_review_items", "readiness_warnings")
    op.drop_column("arb_review_items", "readiness_checked_at")
    op.drop_column("arb_review_items", "readiness_score")

    # Drop tables in reverse order (respecting foreign keys)
    op.drop_table("arb_session_templates")
    op.drop_index("ix_arb_watcher_entity", "arb_watchers")
    op.drop_table("arb_watchers")
    op.drop_index("ix_arb_audit_user_time", "arb_audit_logs")
    op.drop_index("ix_arb_audit_entity", "arb_audit_logs")
    op.drop_table("arb_audit_logs")
    op.drop_table("arb_exceptions")
    op.drop_table("arb_readiness_checks")
