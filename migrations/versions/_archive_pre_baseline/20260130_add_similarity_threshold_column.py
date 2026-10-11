"""Add missing similarity_threshold column to unified_duplicate_groups.

The column was defined in the SQLAlchemy model (UnifiedDuplicateGroup) but
never added via migration. Its absence causes CASCADE failures when
deleting applications that are referenced in unified_group_members.

Revision ID: 20260130_sim_threshold
Create Date: 2026-01-30
"""

import sqlalchemy as sa
from alembic import op

revision = "20260130_sim_threshold"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Add similarity_threshold column to unified_duplicate_groups if missing."""
    conn = op.get_bind()

    # Check if the table exists first
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "unified_duplicate_groups" not in tables:
        # Table doesn't exist — create it with all columns from the model
        op.create_table(
            "unified_duplicate_groups",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(256), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("detection_run_id", sa.Integer(), nullable=True),
            sa.Column(
                "duplicate_type",
                sa.String(20),
                nullable=False,
                server_default="fuzzy",
            ),
            sa.Column("similarity_score", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column(
                "similarity_threshold",
                sa.Float(),
                nullable=False,
                server_default="0.8",
            ),
            sa.Column("match_details", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("resolution_action", sa.String(20), nullable=True),
            sa.Column("resolution_notes", sa.Text(), nullable=True),
            sa.Column("resolved_by", sa.Integer(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("estimated_savings", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("risk_level", sa.String(20), nullable=False, server_default="medium"),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        return

    # Table exists — check if the column is missing
    columns = [col["name"] for col in inspector.get_columns("unified_duplicate_groups")]

    if "similarity_threshold" not in columns:
        op.add_column(
            "unified_duplicate_groups",
            sa.Column(
                "similarity_threshold",
                sa.Float(),
                nullable=False,
                server_default="0.8",
            ),
        )

    # Also add any other columns that may be missing from the model
    missing_columns = {
        "match_details": sa.Column("match_details", sa.JSON(), nullable=True),
        "estimated_savings": sa.Column(
            "estimated_savings", sa.Float(), nullable=False, server_default="0.0"
        ),
        "risk_level": sa.Column(
            "risk_level", sa.String(20), nullable=False, server_default="medium"
        ),
    }
    for col_name, col_def in missing_columns.items():
        if col_name not in columns:
            try:
                op.add_column("unified_duplicate_groups", col_def)
            except Exception:
                pass  # Column may already exist


def downgrade():
    """Remove similarity_threshold column (reversible)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "unified_duplicate_groups" not in tables:
        return

    columns = [col["name"] for col in inspector.get_columns("unified_duplicate_groups")]
    if "similarity_threshold" in columns:
        op.drop_column("unified_duplicate_groups", "similarity_threshold")
