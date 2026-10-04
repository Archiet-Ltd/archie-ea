"""Add a DEFAULT partition to event_log so out-of-range rows are stored.

ID: 20261004_evt_default
Revises: 20261004_agten_cascade
Create Date: 2026-10-04

A DEFAULT partition catches rows whose created_at falls outside every
explicit monthly partition — including outbox events dated before the
first partition was created.
"""
from alembic import op
from sqlalchemy import text

revision = "20261004_evt_default"
down_revision = "20261004_agten_cascade"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    # Idempotent: only create the DEFAULT partition if it does not exist.
    bind.execute(text("""
        DO $add_default$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_class
            WHERE relname = 'event_log_default'
              AND relkind = 'r'
          ) THEN
            CREATE TABLE event_log_default PARTITION OF event_log DEFAULT;
          END IF;
        END
        $add_default$
    """))


def downgrade():
    bind = op.get_bind()
    bind.execute(text("DROP TABLE IF EXISTS event_log_default"))
