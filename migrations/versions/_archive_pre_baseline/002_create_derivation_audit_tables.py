"""Create derivation audit tables for APQC-ArchiMate derivation tracking

Revision ID: 002_derivation_audit
Revises: 001_create_nlp_tables
Create Date: 2026-01-18

This migration creates tables for tracking auto-derived ArchiMate elements
from APQC processes. Supports the new UnifiedDerivationService.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "002_derivation_audit"
down_revision = "001_create_nlp_tables"
branch_labels = None
depends_on = None


def upgrade():
    """Create derivation_session and derivation_audit tables"""

    # Create derivation_session table
    op.create_table(
        "derivation_session",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="in_progress"),
        # Input summary
        sa.Column("input_apqc_codes", sa.Text(), nullable=True),  # JSON list
        sa.Column("input_count", sa.Integer(), nullable=True),
        # Output summary
        sa.Column("elements_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relationships_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_confidence", sa.Float(), nullable=True),
        sa.Column("low_confidence_count", sa.Integer(), nullable=False, server_default="0"),
        # Layer breakdown
        sa.Column("business_elements", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("application_elements", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("technology_elements", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("strategy_elements", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("motivation_elements", sa.Integer(), nullable=False, server_default="0"),
        # Errors/warnings
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_details", sa.Text(), nullable=True),  # JSON
        # User
        sa.Column("initiated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )

    # Create derivation_audit table
    op.create_table(
        "derivation_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # What was derived
        sa.Column("derived_element_id", sa.Integer(), nullable=False),
        sa.Column("derived_element_type", sa.String(50), nullable=False),
        sa.Column("derived_element_name", sa.String(256), nullable=False),
        # Source information
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("source_code", sa.String(50), nullable=True),
        sa.Column("source_name", sa.String(256), nullable=True),
        # Derivation metadata
        sa.Column("derivation_rule", sa.String(100), nullable=False),
        sa.Column("derivation_method", sa.String(50), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        # Review status
        sa.Column("requires_review", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("review_action", sa.String(20), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        # Feedback for learning
        sa.Column("was_correct", sa.Boolean(), nullable=True),
        sa.Column("correction_applied", sa.Text(), nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        # Session grouping
        sa.Column(
            "derivation_session_id",
            sa.String(36),
            sa.ForeignKey("derivation_session.id"),
            nullable=True,
        ),
    )

    # Create indexes for common queries
    op.create_index(
        "ix_derivation_audit_derived_element_id", "derivation_audit", ["derived_element_id"]
    )
    op.create_index(
        "ix_derivation_audit_derived_element_type", "derivation_audit", ["derived_element_type"]
    )
    op.create_index("ix_derivation_audit_source_type", "derivation_audit", ["source_type"])
    op.create_index("ix_derivation_audit_source_id", "derivation_audit", ["source_id"])
    op.create_index("ix_derivation_audit_source_code", "derivation_audit", ["source_code"])
    op.create_index("ix_derivation_audit_derivation_rule", "derivation_audit", ["derivation_rule"])
    op.create_index(
        "ix_derivation_audit_confidence_score", "derivation_audit", ["confidence_score"]
    )
    op.create_index("ix_derivation_audit_requires_review", "derivation_audit", ["requires_review"])
    op.create_index("ix_derivation_audit_reviewed", "derivation_audit", ["reviewed"])
    op.create_index("ix_derivation_audit_created_at", "derivation_audit", ["created_at"])
    op.create_index("ix_derivation_audit_session_id", "derivation_audit", ["derivation_session_id"])

    # Composite indexes
    op.create_index(
        "ix_derivation_audit_review_status", "derivation_audit", ["requires_review", "reviewed"]
    )
    op.create_index("ix_derivation_audit_source", "derivation_audit", ["source_type", "source_id"])
    op.create_index(
        "ix_derivation_audit_confidence_review",
        "derivation_audit",
        ["confidence_score", "requires_review"],
    )


def downgrade():
    """Drop derivation audit tables"""

    # Drop indexes
    op.drop_index("ix_derivation_audit_confidence_review", "derivation_audit")
    op.drop_index("ix_derivation_audit_source", "derivation_audit")
    op.drop_index("ix_derivation_audit_review_status", "derivation_audit")
    op.drop_index("ix_derivation_audit_session_id", "derivation_audit")
    op.drop_index("ix_derivation_audit_created_at", "derivation_audit")
    op.drop_index("ix_derivation_audit_reviewed", "derivation_audit")
    op.drop_index("ix_derivation_audit_requires_review", "derivation_audit")
    op.drop_index("ix_derivation_audit_confidence_score", "derivation_audit")
    op.drop_index("ix_derivation_audit_derivation_rule", "derivation_audit")
    op.drop_index("ix_derivation_audit_source_code", "derivation_audit")
    op.drop_index("ix_derivation_audit_source_id", "derivation_audit")
    op.drop_index("ix_derivation_audit_source_type", "derivation_audit")
    op.drop_index("ix_derivation_audit_derived_element_type", "derivation_audit")
    op.drop_index("ix_derivation_audit_derived_element_id", "derivation_audit")

    # Drop tables
    op.drop_table("derivation_audit")
    op.drop_table("derivation_session")
