"""Provenance origin on ArchiMate elements and relationships.

Adds source_kind, source_ref, source_label and source_recorded_at to both
archimate_elements and archimate_relationships, and custom_properties to
archimate_relationships (the properties an exchange file states for a
relationship). Every column is nullable and nothing is backfilled: an existing
row keeps null and is read as a manual record by its creator.

Idempotent: a column that already exists is left as it is, so a re-run, or a
database that reconcile-schema already extended, upgrades without error.

Revision ID: 20261020_provenance_origin
Revises: 20261020_permission_grants
Create Date: 2026-10-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261020_provenance_origin"
down_revision = "20261020_permission_grants"
branch_labels = None
depends_on = None

_ORIGIN_COLUMNS = (
    ("source_kind", lambda: sa.Column("source_kind", sa.String(20), nullable=True)),
    ("source_ref", lambda: sa.Column("source_ref", sa.Text(), nullable=True)),
    ("source_label", lambda: sa.Column("source_label", sa.Text(), nullable=True)),
    ("source_recorded_at", lambda: sa.Column("source_recorded_at", sa.DateTime(), nullable=True)),
)
_RELATIONSHIP_ONLY = (
    ("custom_properties", lambda: sa.Column("custom_properties", sa.JSON(), nullable=True)),
)
_PLAN = {
    "archimate_elements": _ORIGIN_COLUMNS,
    "archimate_relationships": _ORIGIN_COLUMNS + _RELATIONSHIP_ONLY,
}


def _existing(table: str) -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    for table, columns in _PLAN.items():
        have = _existing(table)
        for name, make in columns:
            if name not in have:
                op.add_column(table, make())


def downgrade():
    for table, columns in _PLAN.items():
        have = _existing(table)
        for name, _make in reversed(columns):
            if name in have:
                op.drop_column(table, name)
