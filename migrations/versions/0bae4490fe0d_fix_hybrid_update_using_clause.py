"""Fix hybrid table UPDATE policy USING clause to reject NULL organization_id.

The hybrid_update policy's USING clause was:
    USING (organization_id = current_setting('archie.organization_id', true)::int)

This evaluates to UNKNOWN for shared rows (organization_id IS NULL), so the
policy doesn't apply and UPDATE is allowed. Fixed by adding IS NOT NULL check.

Revision ID: 0bae4490fe0d
Revises: bd01d9e2a5de
Create Date: 2026-10-03
"""
from alembic import op
from sqlalchemy import text

revision = "0bae4490fe0d"
down_revision = "bd01d9e2a5de"
branch_labels = None
depends_on = None


HYBRID_TABLES = [
    "capability_framework_configuration",
    "enterprise_architecture_frameworks",
    "framework_configuration_templates",
    "framework_extensions",
    "framework_migration_mappings",
    "framework_validation_rules",
    "industry_apqc_framework",
    "industry_apqc_process",
    "industry_frameworks",
    "quality_frameworks",
    "reference_model",
    "reference_model_capability",
]


def _table_exists(bind, table: str) -> bool:
    return bind.execute(
        text("SELECT to_regclass(:t) IS NOT NULL"),
        {"t": table},
    ).scalar()


def _policy_exists(bind, table: str, policy: str) -> bool:
    return bind.execute(
        text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM pg_policies"
            "  WHERE schemaname = 'public'"
            "    AND tablename = :t"
            "    AND policyname = :p"
            ")"
        ),
        {"t": table, "p": policy},
    ).scalar()


def upgrade():
    bind = op.get_bind()

    for table in HYBRID_TABLES:
        if not _table_exists(bind, table):
            continue

        # Drop and recreate UPDATE policy with IS NOT NULL in USING clause
        if _policy_exists(bind, table, "hybrid_update"):
            bind.execute(text(f"DROP POLICY hybrid_update ON {table}"))
        bind.execute(text(f"""
            CREATE POLICY hybrid_update ON {table}
            FOR UPDATE
            USING (
                organization_id IS NOT NULL
                AND organization_id = current_setting('archie.organization_id', true)::int
            )
            WITH CHECK (
                organization_id IS NOT NULL
                AND organization_id = current_setting('archie.organization_id', true)::int
            )
        """))


def downgrade():
    bind = op.get_bind()

    for table in HYBRID_TABLES:
        if not _table_exists(bind, table):
            continue
        if _policy_exists(bind, table, "hybrid_update"):
            bind.execute(text(f"DROP POLICY hybrid_update ON {table}"))
        # Recreate with original USING clause (without IS NOT NULL)
        bind.execute(text(f"""
            CREATE POLICY hybrid_update ON {table}
            FOR UPDATE
            USING (organization_id = current_setting('archie.organization_id', true)::int)
            WITH CHECK (
                organization_id IS NOT NULL
                AND organization_id = current_setting('archie.organization_id', true)::int
            )
        """))
