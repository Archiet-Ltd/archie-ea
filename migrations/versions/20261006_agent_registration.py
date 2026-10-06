"""Add agent_registrations (R1-B56): the one registry row R1-B22's
AgentCharter reuse note says is missing -- owner, charter version and
delegated limits per agent, with activation refused until all three exist.

Revision ID: 20261006_agent_registration
Revises: 20261006_formula_registers
Create Date: 2026-10-06
"""
from alembic import op
from sqlalchemy import text

revision = "20261006_agent_registration"
down_revision = "20261006_formula_registers"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(text(
        """
        CREATE TABLE IF NOT EXISTS agent_registrations (
            id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            name VARCHAR(120) NOT NULL,
            purpose TEXT NOT NULL DEFAULT '',
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            owner_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            charter_persona VARCHAR(80),
            charter_version_id INTEGER REFERENCES agent_charters(id) ON DELETE SET NULL,
            delegated_limits JSON,
            owner_departed_at TIMESTAMP,
            successor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            updated_at TIMESTAMP
        )
        """
    ))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_agent_registrations_organization_id "
        "ON agent_registrations(organization_id)"
    ))


def downgrade():
    bind = op.get_bind()
    bind.execute(text("DROP TABLE IF EXISTS agent_registrations"))
