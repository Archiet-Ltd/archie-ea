"""Add agent_oversight_state table for per-organisation pause/stop-all-writes.

One row per organisation, created on first read (AgentOversightState.get_for_org).
The table is tenant-fenced via TenantMixin.organization_id.

Revision ID: 20261002_agent_oversight_state
Revises: 20261002_wp_cap_nullable
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa

# alembic_version.version_num is VARCHAR(32) (set by the baseline migration
# system in migrations/env.py, not by this revision) -- keep this id at or
# under that length, the same constraint every revision after the baseline
# already satisfies.
revision = "20261002_agent_oversight_state"
down_revision = "20261002_wp_cap_nullable"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "agent_oversight_state" not in inspector.get_table_names():
        op.create_table(
            "agent_oversight_state",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("organization_id", sa.Integer(), nullable=False),
            sa.Column("writes_paused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("paused_by_id", sa.Integer(), nullable=True),
            sa.Column("paused_at", sa.DateTime(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["organizations.id"],
            ),
            sa.ForeignKeyConstraint(
                ["paused_by_id"],
                ["users.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_agent_oversight_state_organization_id",
            "agent_oversight_state",
            ["organization_id"],
            unique=True,
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "agent_oversight_state" in inspector.get_table_names():
        op.drop_index(
            "ix_agent_oversight_state_organization_id",
            table_name="agent_oversight_state",
        )
        op.drop_table("agent_oversight_state")