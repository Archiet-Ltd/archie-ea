"""Create extraction feedback table

Revision ID: f1a2b3c4d5e6
Revises: a1b2c3d4e5f6
Create Date: 2026-01-12 18:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    # Create extraction_feedback table
    op.create_table(
        "extraction_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("document_hash", sa.String(length=64), nullable=True),
        sa.Column("original_element", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("corrected_element", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("correction_type", sa.String(length=50), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("feedback_timestamp", sa.DateTime(), nullable=True),
        sa.Column("confidence_before", sa.Float(), nullable=True),
        sa.Column("confidence_after", sa.Float(), nullable=True),
        sa.Column("pattern_key", sa.String(length=200), nullable=True),
        sa.Column("learned_rule", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("applied_count", sa.Integer(), nullable=True, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes
    op.create_index(
        "ix_extraction_feedback_document_id", "extraction_feedback", ["document_id"], unique=False
    )
    op.create_index(
        "ix_extraction_feedback_document_hash",
        "extraction_feedback",
        ["document_hash"],
        unique=False,
    )
    op.create_index(
        "ix_extraction_feedback_user_id", "extraction_feedback", ["user_id"], unique=False
    )
    op.create_index(
        "ix_extraction_feedback_feedback_timestamp",
        "extraction_feedback",
        ["feedback_timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_extraction_feedback_pattern_key", "extraction_feedback", ["pattern_key"], unique=False
    )

    # Foreign key constraints (if tables exist)
    try:
        op.create_foreign_key(
            "fk_extraction_feedback_document_id",
            "extraction_feedback",
            "document_analyses",
            ["document_id"],
            ["id"],
        )
    except Exception:
        pass  # Table might not exist yet

    try:
        op.create_foreign_key(
            "fk_extraction_feedback_user_id", "extraction_feedback", "users", ["user_id"], ["id"]
        )
    except Exception:
        pass  # Table might not exist yet


def downgrade():
    op.drop_index("ix_extraction_feedback_pattern_key", table_name="extraction_feedback")
    op.drop_index("ix_extraction_feedback_feedback_timestamp", table_name="extraction_feedback")
    op.drop_index("ix_extraction_feedback_user_id", table_name="extraction_feedback")
    op.drop_index("ix_extraction_feedback_document_hash", table_name="extraction_feedback")
    op.drop_index("ix_extraction_feedback_document_id", table_name="extraction_feedback")
    op.drop_table("extraction_feedback")
