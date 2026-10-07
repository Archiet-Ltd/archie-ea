"""SCIM provisioning and leaver removal (R1-B26 PR 1, TB-0143).

Adds the deactivation state to users (deactivated_at, deactivation_reason,
provisioned_via), the per-organisation SCIM bearer-token table (hash only) and
the SCIM group membership table. Every statement is idempotent so a second
run is a no-op, and the downgrade drops with IF EXISTS.

Revision ID: 20261007_scim_provisioning
Revises: 20261006_owner_element_ref
Create Date: 2026-10-07
"""
from alembic import op
from sqlalchemy import text

revision = "20261007_scim_provisioning"
down_revision = "20261006_owner_element_ref"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMP"))
    bind.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS deactivation_reason VARCHAR(32)"))
    bind.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS provisioned_via VARCHAR(16)"))

    bind.execute(text(
        "CREATE TABLE IF NOT EXISTS scim_tokens ("
        " id SERIAL PRIMARY KEY,"
        " organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,"
        " token_hash VARCHAR(64) NOT NULL,"
        " token_prefix VARCHAR(16) NOT NULL,"
        " created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,"
        " created_at TIMESTAMP NOT NULL,"
        " last_used_at TIMESTAMP,"
        " revoked_at TIMESTAMP"
        ")"
    ))
    bind.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_scim_tokens_token_hash ON scim_tokens (token_hash)"
    ))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_scim_tokens_organization_id ON scim_tokens (organization_id)"
    ))

    bind.execute(text(
        "CREATE TABLE IF NOT EXISTS scim_group_memberships ("
        " id SERIAL PRIMARY KEY,"
        " organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,"
        " group_mapping_id INTEGER NOT NULL REFERENCES sso_group_role_mappings(id) ON DELETE CASCADE,"
        " user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
        " created_at TIMESTAMP NOT NULL"
        ")"
    ))
    bind.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_scim_group_membership "
        "ON scim_group_memberships (organization_id, group_mapping_id, user_id)"
    ))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_scim_group_memberships_organization_id "
        "ON scim_group_memberships (organization_id)"
    ))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_scim_group_memberships_group_mapping_id "
        "ON scim_group_memberships (group_mapping_id)"
    ))
    bind.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_scim_group_memberships_user_id "
        "ON scim_group_memberships (user_id)"
    ))


def downgrade():
    bind = op.get_bind()
    bind.execute(text("DROP TABLE IF EXISTS scim_group_memberships"))
    bind.execute(text("DROP TABLE IF EXISTS scim_tokens"))
    bind.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS provisioned_via"))
    bind.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS deactivation_reason"))
    bind.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS deactivated_at"))
