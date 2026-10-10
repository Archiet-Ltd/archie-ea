"""R1-B34: formula_registers table (TB-0135).

Revision ID: 20261006_formula_registers
Revises: 20261004_acr_escalated_at
Create Date: 2026-10-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "20261006_formula_registers"
down_revision = "20261004_acr_escalated_at"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    # formula_registers is already a mapped model (app/models/formula_register.py),
    # so `flask init-db`'s create_all() can create it before this revision's
    # upgrade() ever runs on a fresh or reconciled database -- the deploy
    # order is init-db, then schema-upgrade (ADR documented in
    # app/commands/schema_migrations.py's module docstring). A bare
    # op.create_table() then fails with "relation already exists". Guard it
    # the same way 20261005_business_function_capability_fk.py and
    # 20261001_adr_canonical_cols.py check for an existing table:
    # to_regclass(...) as a function call, not the ``::regclass`` cast
    # operator, which (per the business-function migration's own
    # _fk_references docstring) silently drops a following bind parameter.
    if not bind.execute(text("SELECT to_regclass('formula_registers')")).scalar():
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
    # Symmetrical with upgrade(): if_exists=True on every drop makes a
    # second/early downgrade() a clean no-op too, rather than failing on an
    # index or table that is already gone.
    op.drop_index("ix_formula_registers_formula_key", table_name="formula_registers", if_exists=True)
    op.drop_index("ix_formula_registers_org_key_active", table_name="formula_registers", if_exists=True)
    op.drop_table("formula_registers", if_exists=True)
