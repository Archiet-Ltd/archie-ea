"""Add agent_charters and agent_run_records tables.

Revision ID: 20261002_agent_charters
Revises: 20261001_approval_nullable
Create Date: 2026-10-02
"""
from alembic import op
from sqlalchemy import text

from app.commands.schema_migrations import ContractBlocked

revision = "20261002_agent_charters"
down_revision = "20261002_data_domain_org_unique"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # agent_charters
    bind.execute(text("""
        CREATE TABLE IF NOT EXISTS agent_charters (
            id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id),
            persona VARCHAR(80) NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            purpose TEXT NOT NULL DEFAULT '',
            readable_entities JSON NOT NULL DEFAULT '[]',
            proposable_actions JSON NOT NULL DEFAULT '[]',
            forbidden_actions JSON NOT NULL DEFAULT '[]',
            charter_text TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """))
    bind.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_charter_org_persona_version
        ON agent_charters (organization_id, persona, version)
    """))
    bind.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_agent_charters_organization_id
        ON agent_charters (organization_id)
    """))
    bind.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_agent_charters_persona
        ON agent_charters (persona)
    """))

    # agent_run_records
    bind.execute(text("""
        CREATE TABLE IF NOT EXISTS agent_run_records (
            id SERIAL PRIMARY KEY,
            organization_id INTEGER NOT NULL REFERENCES organizations(id),
            user_id INTEGER REFERENCES users(id),
            persona VARCHAR(80),
            charter_version INTEGER,
            inputs JSON,
            tools_called JSON,
            records_read JSON,
            proposals JSON,
            estimated_cost_usd DOUBLE PRECISION,
            outcome TEXT,
            success BOOLEAN DEFAULT TRUE,
            domain VARCHAR(50),
            model_used VARCHAR(100),
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """))
    bind.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_agent_run_records_organization_id
        ON agent_run_records (organization_id)
    """))
    bind.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_agent_run_records_user_id
        ON agent_run_records (user_id)
    """))
    bind.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_agent_run_records_created_at
        ON agent_run_records (created_at)
    """))

    # Add persona column to ai_chat_crud_approvals so the charter can be
    # enforced when a queued tool call is approved and executed.
    bind.execute(text("""
        ALTER TABLE ai_chat_crud_approvals
        ADD COLUMN IF NOT EXISTS persona VARCHAR(80)
    """))


def downgrade():
    bind = op.get_bind()
    # Drop persona column from ai_chat_crud_approvals (safe: nullable, no FK)
    bind.execute(text("""
        ALTER TABLE ai_chat_crud_approvals
        DROP COLUMN IF EXISTS persona
    """))
    charter_rows = bind.execute(text("SELECT count(*) FROM agent_charters")).scalar()
    if charter_rows:
        raise ContractBlocked(
            f"agent_charters: {charter_rows} row(s) hold charter records; "
            "downgrading would discard them. Forward-fix instead."
        )
    run_rows = bind.execute(text("SELECT count(*) FROM agent_run_records")).scalar()
    if run_rows:
        raise ContractBlocked(
            f"agent_run_records: {run_rows} row(s) hold run records; "
            "downgrading would discard them. Forward-fix instead."
        )
    bind.execute(text("DROP INDEX IF EXISTS ix_agent_run_records_created_at"))
    bind.execute(text("DROP INDEX IF EXISTS ix_agent_run_records_user_id"))
    bind.execute(text("DROP INDEX IF EXISTS ix_agent_run_records_organization_id"))
    bind.execute(text("DROP TABLE IF EXISTS agent_run_records"))
    bind.execute(text("DROP INDEX IF EXISTS ix_agent_charters_persona"))
    bind.execute(text("DROP INDEX IF EXISTS ix_agent_charters_organization_id"))
    # Drop the unique constraint first (created by init-db's create_all from the
    # model's __table_args__), which cascades to drop its backing index. If only
    # a standalone unique index exists (from a prior upgrade), drop it directly.
    bind.execute(text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_agent_charter_org_persona_version'
                  AND conrelid = 'agent_charters'::regclass
            ) THEN
                ALTER TABLE agent_charters
                DROP CONSTRAINT uq_agent_charter_org_persona_version;
            END IF;
        END
        $$;
    """))
    bind.execute(text("DROP INDEX IF EXISTS uq_agent_charter_org_persona_version"))
    bind.execute(text("DROP TABLE IF EXISTS agent_charters"))