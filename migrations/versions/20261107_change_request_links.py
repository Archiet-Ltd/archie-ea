"""Add architecture_change_requests.capability_id and solution_id, the capability and solution a request is about.

Both nullable foreign keys that become NULL when the record they name is deleted. Idempotent: each column
is added only when absent, and a column reconcile-schema already added without constraints gets its foreign
key here, so a deleted capability or solution leaves no dangling number. The downgrade drops only what is
present.

Revision ID: 20261107_change_request_links
Revises: 20261107_stage_duration
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261107_change_request_links"
down_revision = "20261107_stage_duration"
branch_labels = None
depends_on = None

TABLE = "architecture_change_requests"
LINKS = (
    ("capability_id", "unified_capabilities"),
    ("solution_id", "solutions"),
)


def _columns(bind):
    return {c["name"] for c in inspect(bind).get_columns(TABLE)}


def _has_foreign_key(bind, column):
    return any(
        fk["constrained_columns"] == [column] for fk in inspect(bind).get_foreign_keys(TABLE)
    )


def upgrade():
    bind = op.get_bind()
    have = _columns(bind)
    for column, target in LINKS:
        if column not in have:
            op.add_column(TABLE, sa.Column(column, sa.Integer(), nullable=True))
        elif not _has_foreign_key(bind, column):
            # A number naming a record that no longer exists cannot satisfy the new constraint.
            op.execute(
                sa.text(
                    f"UPDATE {TABLE} SET {column} = NULL WHERE {column} IS NOT NULL "
                    f"AND {column} NOT IN (SELECT id FROM {target})"
                )
            )
        if not _has_foreign_key(bind, column):
            op.create_foreign_key(
                f"{TABLE}_{column}_fkey", TABLE, target, [column], ["id"], ondelete="SET NULL"
            )


def downgrade():
    have = _columns(op.get_bind())
    for column, _target in reversed(LINKS):
        if column in have:
            op.drop_column(TABLE, column)
