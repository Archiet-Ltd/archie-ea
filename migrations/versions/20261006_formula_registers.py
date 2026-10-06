"""R1-B34: formula_registers table (TB-0135).

Revision ID: 20261006_formula_registers
Revises: 20261004_acr_escalated_at
Create Date: 2026-10-06
"""
from alembic import op
import sqlalchemy as sa

revision = "20261006_formula_registers"
down_revision = "20261004_acr_escalated_at"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "formula_registers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("formula_key", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("inputs", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewer_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_formula_registers_org_key_active",
        "formula_registers",
        ["organization_id", "formula_key", "is_active"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_formula_registers_formula_key",
        "formula_registers",
        ["formula_key"],
        if_not_exists=True,
    )


def downgrade():
    op.drop_index("ix_formula_registers_formula_key", table_name="formula_registers")
    op.drop_index("ix_formula_registers_org_key_active", table_name="formula_registers")
    op.drop_table("formula_registers")
