"""Add architecture_change_requests.capability_id and solution_id, the capability and solution a request is about.

Both nullable foreign keys that become NULL when the record they name is deleted. Idempotent: each column
is added only when absent (reconcile-schema may already have added it in production), and the downgrade
drops only what is present.

Revision ID: 20261107_change_request_links
Revises: 20261104_force_rls
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261107_change_request_links"
down_revision = "20261104_force_rls"
branch_labels = None
depends_on = None

TABLE = "architecture_change_requests"
LINKS = (
    ("capability_id", "unified_capabilities"),
    ("solution_id", "solutions"),
)


def _columns(bind):
    return {c["name"] for c in inspect(bind).get_columns(TABLE)}


def upgrade():
    have = _columns(op.get_bind())
    for column, target in LINKS:
        if column not in have:
            op.add_column(TABLE, sa.Column(column, sa.Integer(), nullable=True))
            op.create_foreign_key(
                f"{TABLE}_{column}_fkey", TABLE, target, [column], ["id"], ondelete="SET NULL"
            )


def downgrade():
    have = _columns(op.get_bind())
    for column, _target in reversed(LINKS):
        if column in have:
            op.drop_column(TABLE, column)
