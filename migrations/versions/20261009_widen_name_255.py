"""Widen architecture_models.name and other_relationships.name to 255 characters.

Expand step: widens architecture_models.name from VARCHAR(100) to VARCHAR(255)
and other_relationships.name from VARCHAR(100) to VARCHAR(255). On PostgreSQL
this rewrites neither the table nor its indexes. The down step narrows back to
100 and refuses, changing nothing, while any name is longer.

Revision ID: 20261009_widen_name_255
Revises: 20261010_arb_change_requests_rls
Create Date: 2026-10-09
"""
from alembic import op

from app.commands.schema_migrations import narrow_varchar, widen_varchar

revision = "20261009_widen_name_255"
down_revision = "20261010_arb_change_requests_rls"
branch_labels = None
depends_on = None

TABLES = [
    ("architecture_models", "name", 100, 255),
    ("other_relationships", "name", 100, 255),
]


def upgrade():
    for table, column, _prev, length in TABLES:
        widen_varchar(op.get_bind(), table, column, length)


def downgrade():
    for table, column, prev, _length in TABLES:
        narrow_varchar(op.get_bind(), table, column, prev)