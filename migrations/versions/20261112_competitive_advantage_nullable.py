"""Let an application's Competitive Advantage be unset, Yes or No.

The field stored False both for an explicit No and for a field nobody had set, so the page
could not say which. The column becomes a nullable boolean with no default: unset is NULL,
Yes is true, No is false. A False already stored was never chosen by anyone, so it becomes
unset and keeps reading "Not recorded". True rows are untouched.

The downgrade turns unset back into False, the form the column had before; no row is lost.

Revision ID: 20261112_competitive_advantage
Revises: 20261110_founder_charter
Create Date: 2026-11-12
"""
from alembic import op

revision = "20261112_competitive_advantage"
down_revision = "20261110_founder_charter"
branch_labels = None
depends_on = None

_TABLE = "application_components"
_COLUMN = "competitive_advantage"


def _exists(bind):
    return bind.exec_driver_sql(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        (_TABLE, _COLUMN),
    ).first() is not None


def upgrade():
    bind = op.get_bind()
    if not _exists(bind):
        return
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN {_COLUMN} DROP DEFAULT")
    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN {_COLUMN} DROP NOT NULL")
    op.execute(
        f"ALTER TABLE {_TABLE} ALTER COLUMN {_COLUMN} TYPE boolean USING "
        f"CASE WHEN lower({_COLUMN}::text) IN ('true', 't', 'yes', '1') THEN true ELSE NULL END"
    )


def downgrade():
    bind = op.get_bind()
    if not _exists(bind):
        return
    op.execute(f"UPDATE {_TABLE} SET {_COLUMN} = false WHERE {_COLUMN} IS NULL")
