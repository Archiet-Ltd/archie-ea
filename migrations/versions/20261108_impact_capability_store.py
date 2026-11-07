"""Point change_impact_assessments.affected_capability_id at unified_capabilities.

The column named the empty capabilities table, so no impact could ever be recorded; the change request
itself links to unified_capabilities. A number that names no unified capability is cleared first so the
new constraint can be added. Idempotent: the constraint is swapped only when it does not already point
at unified_capabilities.

Revision ID: 20261108_impact_capability
Revises: 20261107_change_request_links
Create Date: 2026-11-08
"""
from alembic import op
from sqlalchemy import inspect, text

revision = "20261108_impact_capability"
down_revision = "20261107_change_request_links"
branch_labels = None
depends_on = None

TABLE = "change_impact_assessments"
COLUMN = "affected_capability_id"


def _capability_foreign_keys(bind):
    return [
        fk for fk in inspect(bind).get_foreign_keys(TABLE) if fk["constrained_columns"] == [COLUMN]
    ]


def _swap(target):
    bind = op.get_bind()
    if not inspect(bind).has_table(TABLE):
        return
    existing = _capability_foreign_keys(bind)
    if any(fk["referred_table"] == target for fk in existing):
        return
    for fk in existing:
        op.drop_constraint(fk["name"], TABLE, type_="foreignkey")
    op.execute(
        text(
            f"UPDATE {TABLE} SET {COLUMN} = NULL WHERE {COLUMN} IS NOT NULL "
            f"AND {COLUMN} NOT IN (SELECT id FROM {target})"
        )
    )
    op.create_foreign_key(
        f"{TABLE}_{COLUMN}_fkey", TABLE, target, [COLUMN], ["id"], ondelete="SET NULL"
    )


def upgrade():
    _swap("unified_capabilities")


def downgrade():
    _swap("capabilities")
