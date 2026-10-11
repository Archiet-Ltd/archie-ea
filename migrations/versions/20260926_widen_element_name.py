"""Store element names up to 500 characters.

Expand step: widens archimate_elements.name from VARCHAR(100) to VARCHAR(500).
On PostgreSQL this rewrites neither the table nor its indexes. The down step
narrows back to 100 and refuses, changing nothing, while any name is longer.

Revision ID: 20260926_widen_element_name
Revises: 20260926_relax_owner_app
Create Date: 2026-09-26
"""
from alembic import op

from app.commands.schema_migrations import narrow_varchar, widen_varchar

revision = "20260926_widen_element_name"
down_revision = "20260926_relax_owner_app"
branch_labels = None
depends_on = None

TABLE, COLUMN = "archimate_elements", "name"
PREVIOUS_LENGTH, LENGTH = 100, 500


def upgrade():
    widen_varchar(op.get_bind(), TABLE, COLUMN, LENGTH)


def downgrade():
    narrow_varchar(op.get_bind(), TABLE, COLUMN, PREVIOUS_LENGTH)
