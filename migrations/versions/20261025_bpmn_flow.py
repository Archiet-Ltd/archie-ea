"""Business process diagram flow: the ``bpmn_flow`` column.

Adds one nullable JSON column to ``business_processes`` holding the BPMN flow
drawn for that process. Nullable, so no backfill; existing rows read as having
no diagram. Imports nothing from ``app``.

Idempotent: the column is added or dropped only when its presence says so.

Revision ID: 20261025_bpmn_flow
Revises: 20261025_vendor_naming
Create Date: 2026-10-25
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261025_bpmn_flow"
down_revision = "20261025_vendor_naming"
branch_labels = None
depends_on = None


def _columns(bind) -> set:
    return {c["name"] for c in inspect(bind).get_columns("business_processes")}


def upgrade():
    if "bpmn_flow" not in _columns(op.get_bind()):
        op.add_column("business_processes", sa.Column("bpmn_flow", sa.JSON(), nullable=True))


def downgrade():
    if "bpmn_flow" in _columns(op.get_bind()):
        op.drop_column("business_processes", "bpmn_flow")
