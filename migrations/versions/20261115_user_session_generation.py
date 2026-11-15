"""Add the per-person sign-in count that ends remember cookies.

Expand step: a nullable integer with a default of 0. Existing people hold 0
and a missing value is read as 0, so a remember cookie issued before this
revision keeps working until the count first moves. The down step drops the
column.

Revision ID: 20261115_user_session_gen
Revises: 20261114_sign_in_identities
Create Date: 2026-11-15
"""
from alembic import op

revision = "20261115_user_session_gen"
down_revision = "20261114_sign_in_identities"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS session_generation "
        "INTEGER DEFAULT 0"
    )


def downgrade():
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS session_generation")
