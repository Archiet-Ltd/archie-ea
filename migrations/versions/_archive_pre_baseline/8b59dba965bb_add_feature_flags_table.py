"""Add feature flags table

Revision ID: 8b59dba965bb
Revises: 20260201_api_fix
Create Date: 2026-02-02 01:11:29.006459

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "8b59dba965bb"
down_revision = "20260201_api_fix"
branch_labels = None
depends_on = None


def upgrade():
    # Create feature_flags table (without foreign key to user table for now)
    op.create_table(
        "feature_flags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("feature_type", sa.String(length=50), nullable=False),
        sa.Column("state", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("sidebar_label", sa.String(length=100), nullable=True),
        sa.Column("sidebar_icon", sa.String(length=50), nullable=True),
        sa.Column("routes", sa.JSON(), nullable=True),
        sa.Column("parent_key", sa.String(length=100), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("last_modified_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_key"],
            ["feature_flags.key"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index(op.f("ix_feature_flags_key"), "feature_flags", ["key"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_feature_flags_key"), table_name="feature_flags")
    op.drop_table("feature_flags")
