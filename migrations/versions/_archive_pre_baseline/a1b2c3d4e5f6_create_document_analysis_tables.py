"""Create document analysis tables

Revision ID: a1b2c3d4e5f6
Revises: 67f4a0d3c0f9
Create Date: 2026-01-08 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "aa30eca8977b"
branch_labels = None
depends_on = None


def upgrade():
    # Create document_analyses table
    op.create_table(
        "document_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_hash", sa.String(length=64), nullable=True),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("analysis_results", sa.Text(), nullable=True),
        sa.Column("application_data", sa.Text(), nullable=True),
        sa.Column("vendor_data", sa.Text(), nullable=True),
        sa.Column("archimate_elements", sa.Text(), nullable=True),
        sa.Column("relationships", sa.Text(), nullable=True),
        sa.Column("validation_results", sa.Text(), nullable=True),
        sa.Column("confidence", sa.String(length=20), nullable=True),
        sa.Column("elements_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("relationships_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("validation_errors_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("status", sa.String(length=30), nullable=True, server_default="completed"),
        sa.Column("applied", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("analyzed_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("llm_interaction_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["analyzed_by_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["llm_interaction_id"],
            ["llm_interactions.id"],
        ),
    )

    # Create indexes for CockroachDB
    op.create_index(
        "ix_document_analyses_entity_type", "document_analyses", ["entity_type"], unique=False
    )
    op.create_index(
        "ix_document_analyses_entity_id", "document_analyses", ["entity_id"], unique=False
    )
    op.create_index(
        "ix_document_analyses_file_hash", "document_analyses", ["file_hash"], unique=False
    )
    op.create_index("ix_document_analyses_applied", "document_analyses", ["applied"], unique=False)
    op.create_index(
        "ix_document_analyses_created_at", "document_analyses", ["created_at"], unique=False
    )
    # Composite index for common queries
    op.create_index(
        "ix_document_analyses_entity",
        "document_analyses",
        ["entity_type", "entity_id"],
        unique=False,
    )

    # Create document_analysis_edits table
    op.create_table(
        "document_analysis_edits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("field_path", sa.String(length=255), nullable=False),
        sa.Column("original_value", sa.Text(), nullable=True),
        sa.Column("edited_value", sa.Text(), nullable=True),
        sa.Column("edit_type", sa.String(length=50), nullable=True),
        sa.Column("edited_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["document_analyses.id"],
        ),
        sa.ForeignKeyConstraint(
            ["edited_by_id"],
            ["users.id"],
        ),
    )

    # Create indexes for edits table
    op.create_index(
        "ix_document_analysis_edits_analysis_id",
        "document_analysis_edits",
        ["analysis_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_analysis_edits_created_at",
        "document_analysis_edits",
        ["created_at"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_document_analysis_edits_created_at", table_name="document_analysis_edits")
    op.drop_index("ix_document_analysis_edits_analysis_id", table_name="document_analysis_edits")
    op.drop_table("document_analysis_edits")

    op.drop_index("ix_document_analyses_entity", table_name="document_analyses")
    op.drop_index("ix_document_analyses_created_at", table_name="document_analyses")
    op.drop_index("ix_document_analyses_applied", table_name="document_analyses")
    op.drop_index("ix_document_analyses_file_hash", table_name="document_analyses")
    op.drop_index("ix_document_analyses_entity_id", table_name="document_analyses")
    op.drop_index("ix_document_analyses_entity_type", table_name="document_analyses")
    op.drop_table("document_analyses")
