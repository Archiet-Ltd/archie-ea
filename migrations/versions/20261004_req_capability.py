"""Add requirements.capability_id (R1-B43 PR 2, TB-0188/PB-0190).

No requirement-to-capability association existed before this -- the
guided-design requirements-capture step traces a requirement to the
capability it realises, through unified_capabilities (ADR 0008's
canonical capability store), not a second one.

Revision ID: 20261004_req_capability
Revises: 20261006_agent_registration
Create Date: 2026-10-04
"""
from alembic import op
from sqlalchemy import text

revision = "20261004_req_capability"
down_revision = "20261006_agent_registration"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(text(
        "ALTER TABLE requirements ADD COLUMN IF NOT EXISTS capability_id INTEGER "
        "REFERENCES unified_capabilities(id) ON DELETE SET NULL"
    ))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_requirements_capability_id ON requirements(capability_id)"
    ))
    bind.execute(text(
        "ALTER TABLE requirements ADD COLUMN IF NOT EXISTS solution_id INTEGER "
        "REFERENCES solutions(id) ON DELETE CASCADE"
    ))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_requirements_solution_id ON requirements(solution_id)"
    ))


def downgrade():
    bind = op.get_bind()
    bind.execute(text("DROP INDEX IF EXISTS ix_requirements_solution_id"))
    bind.execute(text("ALTER TABLE requirements DROP COLUMN IF EXISTS solution_id"))
    bind.execute(text("DROP INDEX IF EXISTS ix_requirements_capability_id"))
    bind.execute(text("ALTER TABLE requirements DROP COLUMN IF EXISTS capability_id"))
