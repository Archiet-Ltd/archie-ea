"""Add strategic_initiative_capabilities (R1-B38 PR 2): initiative-to-
capability links, mirroring the existing strategic_initiative_goals
pattern. Nullable, TenantMixin-free association (scoping comes from the
initiative and capability rows it joins, same as strategic_initiative_goals).

Revision ID: 20261006_initiative_capabilities
Revises: 20261006_formula_registers
Create Date: 2026-10-06
"""
from alembic import op
from sqlalchemy import text

revision = "20261006_initiative_capabilities"
down_revision = "20261006_formula_registers"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(text(
        """
        CREATE TABLE IF NOT EXISTS strategic_initiative_capabilities (
            strategic_initiative_id INTEGER NOT NULL
                REFERENCES strategic_initiatives(id) ON DELETE CASCADE,
            capability_id INTEGER NOT NULL
                REFERENCES business_capability(id) ON DELETE CASCADE,
            contribution_level VARCHAR(20),
            created_at TIMESTAMP DEFAULT now(),
            PRIMARY KEY (strategic_initiative_id, capability_id)
        )
        """
    ))


def downgrade():
    bind = op.get_bind()
    bind.execute(text("DROP TABLE IF EXISTS strategic_initiative_capabilities"))
