"""Add principles.description so the description a person types on the Principle form is kept.

The form offered a Description box and the table had no column for it, so the text was discarded
while the create answered success. Nullable, no default: existing principles have none and the
record page already says so. Idempotent.

Revision ID: 20261109_principle_description
Revises: 20261108_impact_capability
Create Date: 2026-11-09
"""
from alembic import op
from sqlalchemy import inspect, text

revision = "20261109_principle_description"
down_revision = "20261108_impact_capability"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not inspect(bind).has_table("principles"):
        return
    bind.execute(text("ALTER TABLE principles ADD COLUMN IF NOT EXISTS description TEXT"))


def downgrade():
    bind = op.get_bind()
    if not inspect(bind).has_table("principles"):
        return
    bind.execute(text("ALTER TABLE principles DROP COLUMN IF EXISTS description"))
