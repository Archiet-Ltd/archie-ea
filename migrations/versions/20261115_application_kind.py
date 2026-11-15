"""Record the kind a person gave an application.

Adds three nullable columns to application_components: the kind a person chose, who chose it and
when. Existing rows start with NULL, and a NULL kind is not blank: it is worked out from the record's
composition and its repository facts by the same rule a new record gets. The columns can already
exist when this runs (reconcile-schema adds mapped columns), so each add is guarded.

The downgrade drops the three columns.

Revision ID: 20261115_application_kind
Revises: 20261114_sign_in_identities
Create Date: 2026-11-15
"""
import sqlalchemy as sa
from alembic import op

revision = "20261115_application_kind"
down_revision = "20261114_sign_in_identities"
branch_labels = None
depends_on = None

_TABLE = "application_components"


def upgrade():
    op.add_column(_TABLE, sa.Column("application_kind", sa.String(length=20), nullable=True), if_not_exists=True)
    op.add_column(
        _TABLE,
        sa.Column(
            "application_kind_set_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_application_components_kind_set_by"),
            nullable=True,
        ),
        if_not_exists=True,
    )
    op.add_column(_TABLE, sa.Column("application_kind_set_at", sa.DateTime(), nullable=True), if_not_exists=True)


def downgrade():
    op.execute("ALTER TABLE application_components DROP COLUMN IF EXISTS application_kind_set_at")
    op.execute("ALTER TABLE application_components DROP COLUMN IF EXISTS application_kind_set_by_id")
    op.execute("ALTER TABLE application_components DROP COLUMN IF EXISTS application_kind")
