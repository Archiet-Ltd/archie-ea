"""Add the stage-time measurement columns to unified_value_stream_stages.

All nullable: NULL means "not recorded". Idempotent: each column is added only when absent
(a database built from the models, or one reconcile-schema already touched, has them), and the
downgrade drops only what is present.

Revision ID: 20261107_stage_duration
Revises: 20261104_force_rls
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261107_stage_duration"
down_revision = "20261104_force_rls"
branch_labels = None
depends_on = None

TABLE = "unified_value_stream_stages"
COLUMNS = (
    ("jira_statuses", sa.Text()),
    ("duration_unit", sa.String(10)),
    ("duration_source", sa.String(10)),
    ("duration_median_hours", sa.Float()),
    ("duration_sample_size", sa.Integer()),
    ("duration_measured_at", sa.DateTime()),
    ("duration_recorded_by", sa.String(256)),
    ("duration_samples", sa.Text()),
)


def _columns(bind):
    # init-db creates the table; a database built from revisions alone does not have it yet,
    # and the model then creates it with these columns.
    inspector = inspect(bind)
    if not inspector.has_table(TABLE):
        return None
    return {c["name"] for c in inspector.get_columns(TABLE)}


def upgrade():
    have = _columns(op.get_bind())
    if have is None:
        return
    for name, kind in COLUMNS:
        if name not in have:
            op.add_column(TABLE, sa.Column(name, kind, nullable=True))


def downgrade():
    have = _columns(op.get_bind())
    if have is None:
        return
    for name, _ in reversed(COLUMNS):
        if name in have:
            op.drop_column(TABLE, name)
