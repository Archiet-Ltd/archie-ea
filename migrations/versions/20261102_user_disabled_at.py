"""Add users.disabled_at, the time an account was switched off without being deleted.

A nullable timestamp, set by ``flask disable-unconfirmed-signups``. A disabled account cannot
sign in; nothing is deleted. Idempotent: the column is added only when absent (a database
built from the models already has it), and the downgrade drops it only when present.

Revision ID: 20261102_user_disabled_at
Revises: 20261102_consolidation_org
Create Date: 2026-11-02
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261102_user_disabled_at"
down_revision = "20261102_consolidation_org"
branch_labels = None
depends_on = None


def _has_column(bind) -> bool:
    return "disabled_at" in {c["name"] for c in inspect(bind).get_columns("users")}


def upgrade():
    if not _has_column(op.get_bind()):
        op.add_column("users", sa.Column("disabled_at", sa.DateTime(), nullable=True))


def downgrade():
    if _has_column(op.get_bind()):
        op.drop_column("users", "disabled_at")
