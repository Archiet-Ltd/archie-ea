"""Add requirements.source_table and requirements.source_id, the record a ranked copy was made from.

Both nullable. A requirement made from another record names it, so the writer that made it only
ever changes or deletes its own copy. Idempotent: each column is added only when absent (a
database built from the models already has them), and the downgrade drops only what is present.

Revision ID: 20261103_requirement_source
Revises: 20261103_holding_organisation
Create Date: 2026-11-03
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261103_requirement_source"
down_revision = "20261103_holding_organisation"
branch_labels = None
depends_on = None


def _columns(bind):
    return {c["name"] for c in inspect(bind).get_columns("requirements")}


def upgrade():
    have = _columns(op.get_bind())
    if "source_table" not in have:
        op.add_column("requirements", sa.Column("source_table", sa.String(64), nullable=True))
    if "source_id" not in have:
        op.add_column("requirements", sa.Column("source_id", sa.Integer(), nullable=True))


def downgrade():
    have = _columns(op.get_bind())
    if "source_id" in have:
        op.drop_column("requirements", "source_id")
    if "source_table" in have:
        op.drop_column("requirements", "source_table")
