"""Add capability_health_overrides table for manual score overrides

Revision ID: 20260203_add_capability_health_overrides
Revises: 20260202_add_abacus_fields
Create Date: 2026-02-03 23:20:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260203_add_capability_health_overrides"
down_revision = "20260202_add_abacus_fields"
branch_labels = None
depends_on = None


def upgrade():
    # Create capability_health_overrides table
    op.create_table(
        "capability_health_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("capability_id", sa.Integer(), nullable=False),
        sa.Column("original_score", sa.Float(), nullable=False),
        sa.Column("override_score", sa.Float(), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("override_reason", sa.String(50), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, default=True),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["capability_id"], ["business_capabilities.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
    )

    # Create indexes for performance
    op.create_index(
        "ix_capability_health_overrides_capability_id",
        "capability_health_overrides",
        ["capability_id"],
    )
    op.create_index(
        "ix_capability_health_overrides_active", "capability_health_overrides", ["active"]
    )
    op.create_index(
        "ix_capability_health_overrides_created_by_id",
        "capability_health_overrides",
        ["created_by_id"],
    )
    op.create_index(
        "ix_capability_health_overrides_created_at",
        "capability_health_overrides",
        ["created_at"],
    )
    op.create_index(
        "ix_capability_health_overrides_override_reason",
        "capability_health_overrides",
        ["override_reason"],
    )

    # Create unique constraint: only one active override per capability
    op.create_index(
        "uq_capability_health_overrides_capability_active",
        "capability_health_overrides",
        ["capability_id", "active"],
        unique=True,
        postgresql_where=sa.text("active = true"),
    )


def downgrade():
    # Drop table and all indexes
    op.drop_table("capability_health_overrides")
