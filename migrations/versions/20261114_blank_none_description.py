"""Empty the description of work packages the old edit form saved as the word "None".

The edit form used to put the text "None" in the description box when a package had no
description, and saving stored it. This sets those descriptions back to empty. Only a
description that is exactly "None" is changed; running it twice changes nothing more.
It cannot be undone, because the word carried no information.

Revision ID: 20261114_blank_none_desc
Revises: 20261114_sign_in_identities
Create Date: 2026-11-14
"""
import sqlalchemy as sa
from alembic import op

revision = "20261114_blank_none_desc"
down_revision = "20261114_sign_in_identities"
branch_labels = None
depends_on = None

_TABLES = ("unified_work_packages", "work_packages")


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in _TABLES:
        if table not in inspector.get_table_names():
            continue
        if "description" not in {c["name"] for c in inspector.get_columns(table)}:
            continue
        op.execute(f"UPDATE {table} SET description = NULL WHERE description = 'None'")


def downgrade():
    pass
