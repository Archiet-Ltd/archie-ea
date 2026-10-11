"""Allow an ownership record without an application.

Expand step so one ownership record can later point at any element, not only
an application. Existing rows are untouched. The down step restores NOT NULL
and refuses, changing nothing, while any row holds NULL.

Revision ID: 20260926_relax_owner_app
Revises: 20260926_baseline
Create Date: 2026-09-26
"""
from alembic import op

from app.commands.schema_migrations import relax_not_null, tighten_not_null

revision = "20260926_relax_owner_app"
down_revision = "20260926_baseline"
branch_labels = None
depends_on = None

TABLE, COLUMN = "application_owners", "application_id"


def upgrade():
    relax_not_null(op.get_bind(), TABLE, COLUMN)


def downgrade():
    tighten_not_null(op.get_bind(), TABLE, COLUMN)
