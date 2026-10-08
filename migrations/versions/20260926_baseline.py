"""Baseline: the schema the models define, as init-db builds it.

Every database starts its revision history here. On a database ``init-db``
has already run against (every deployed one) this changes nothing; on an
empty database it creates every table the models declare. Revisions before
this one are archived and are not part of the chain.

Revision ID: 20260926_baseline
Revises:
Create Date: 2026-09-26
"""
from alembic import op

from app.commands.schema_migrations import ensure_baseline_schema

revision = "20260926_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    ensure_baseline_schema(op.get_bind())


def downgrade():
    raise RuntimeError(
        "The baseline has no down step: undoing it would drop every table. "
        "Restore from backup instead."
    )
