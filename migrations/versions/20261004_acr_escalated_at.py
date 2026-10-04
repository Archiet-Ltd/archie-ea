"""Add architecture_change_requests.escalated_at (R1-B85).

Lets the CTO scorecard escalate an open exception and have the escalation
persist; nullable, so a request that was never escalated reads as "-" rather
than a fabricated date.

Revision ID: 20261004_acr_escalated_at
Revises: 20261003_gap_org_nullable
Create Date: 2026-10-04
"""
from alembic import op
from sqlalchemy import text

revision = "20261004_acr_escalated_at"
down_revision = "20261003_gap_org_nullable"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(text(
        "ALTER TABLE architecture_change_requests ADD COLUMN IF NOT EXISTS escalated_at TIMESTAMP"
    ))


def downgrade():
    bind = op.get_bind()
    bind.execute(text("ALTER TABLE architecture_change_requests DROP COLUMN IF EXISTS escalated_at"))
