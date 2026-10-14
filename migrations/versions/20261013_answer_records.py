"""Stored answers: the ``answer_records`` table (R1-B30, TB-0106).

Expand step only: adds one new table. Nothing existing is read differently,
altered or dropped. Idempotent: the table and its indexes are created only when
absent, so a second run (or a database where ``init-db`` got there first) is a
no-op.

Revision ID: 20261013_answer_records
Revises: 20261009_app_comp_elem
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261013_answer_records"
down_revision = "20261009_app_comp_elem"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("answer_records"):
        op.create_table(
            "answer_records",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("organization_id", sa.Integer(),
                      sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("address", sa.String(32), nullable=False),
            sa.Column("question_kind", sa.String(32), nullable=False),
            sa.Column("params", sa.JSON(), nullable=False),
            sa.Column("truth_class", sa.String(16)),
            sa.Column("confidence", sa.Float()),
            sa.Column("confidence_basis", sa.String(24), nullable=False, server_default="no_rows"),
            sa.Column("rows", sa.JSON(), nullable=False),
            sa.Column("summary", sa.JSON(), nullable=False),
            sa.Column("reasons", sa.JSON(), nullable=False),
            sa.Column("elements", sa.JSON(), nullable=False),
            sa.Column("citations", sa.JSON(), nullable=False),
            sa.Column("freshness", sa.JSON(), nullable=False),
            sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("computed_at", sa.DateTime(), nullable=False),
            sa.Column("created_by_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("address", name="uq_answer_record_address"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_answer_records_organization_id "
               "ON answer_records (organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_answer_records_org_created "
               "ON answer_records (organization_id, created_at)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS answer_records")
