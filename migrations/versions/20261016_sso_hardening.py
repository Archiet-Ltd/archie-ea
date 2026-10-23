"""Domain ownership proof and SAML assertion replay protection for single sign-on.

Expand step only: creates ``sso_domain_claims`` (one row per organisation and listed domain,
with a one-time token and a verified time, and a partial unique index that lets only one
organisation hold a verified claim on a domain), creates ``sso_assertion_ids`` (assertion ids
already used, unique per organisation) and adds the nullable ``idp_entity_id`` column to
``sso_configs``. Existing configurations keep working for password sign-in; their domains
route to single sign-on again once each is verified. Idempotent: every table, column and index
is created only when absent.

Revision ID: 20261016_sso_hardening
Revises: 20261016_compliance_org_rls
Create Date: 2026-10-16
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20261016_sso_hardening"
down_revision = "20261016_compliance_org_rls"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("sso_configs"):
        existing = {c["name"] for c in inspector.get_columns("sso_configs")}
        if "idp_entity_id" not in existing:
            op.add_column("sso_configs", sa.Column("idp_entity_id", sa.String(500)))

    if not inspector.has_table("sso_domain_claims"):
        op.create_table(
            "sso_domain_claims",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("organization_id", sa.Integer(),
                      sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("domain", sa.String(255), nullable=False),
            sa.Column("token", sa.String(64), nullable=False),
            sa.Column("verified_at", sa.DateTime()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("organization_id", "domain", name="uq_sso_domain_claim_org_domain"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_sso_domain_claims_organization_id "
               "ON sso_domain_claims (organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sso_domain_claims_domain ON sso_domain_claims (domain)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_sso_domain_claim_verified_domain "
               "ON sso_domain_claims (domain) WHERE verified_at IS NOT NULL")

    if not inspector.has_table("sso_assertion_ids"):
        op.create_table(
            "sso_assertion_ids",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("organization_id", sa.Integer(),
                      sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("assertion_id", sa.String(255), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("organization_id", "assertion_id", name="uq_sso_assertion_org_id"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_sso_assertion_ids_organization_id "
               "ON sso_assertion_ids (organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sso_assertion_ids_expires_at ON sso_assertion_ids (expires_at)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS sso_assertion_ids")
    op.execute("DROP TABLE IF EXISTS sso_domain_claims")
    op.execute("ALTER TABLE IF EXISTS sso_configs DROP COLUMN IF EXISTS idp_entity_id")
