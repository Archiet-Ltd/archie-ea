"""Fix hybrid table RLS policies to reject NULL organization_id for tenant writes.

The original hybrid_insert/hybrid_update/hybrid_delete policies used:
    WITH CHECK (organization_id = current_setting('archie.organization_id', true)::int)

In PostgreSQL, NULL = value evaluates to UNKNOWN (not FALSE), and WITH CHECK
treats UNKNOWN as passing. This allowed tenants to insert/update/delete
shared rows (organization_id IS NULL). Fixed by adding explicit IS NOT NULL check.

Revision ID: bd01d9e2a5de
Revises: 20261003_row_level_security
Create Date: 2026-10-03
"""
from alembic import op
from sqlalchemy import text

revision = "bd01d9e2a5de"
down_revision = "20261003_row_level_security"
branch_labels = None
depends_on = None


# HybridTenantMixin tables — shared catalogue (organization_id IS NULL) plus
# per-organisation overrides. Read policy admits both; write policies must
# reject NULL organization_id so only platform migrations can write shared rows.
HYBRID_TABLES = [
    "capability_framework_configuration",
    "enterprise_architecture_frameworks",
    "framework_adoptions",
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
    """Check if a table exists in the current schema."""
    return bind.execute(
        text("SELECT to_regclass(:t) IS NOT NULL"),
        {"t": table},
    ).scalar()


def _policy_exists(bind, table: str, policy: str) -> bool:
    """Check if a policy exists on a table."""
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

        # Drop and recreate INSERT policy with explicit IS NOT NULL check
        if _policy_exists(bind, table, "hybrid_insert"):
            bind.execute(text(f"DROP POLICY hybrid_insert ON {table}"))
        bind.execute(text(f"""
            CREATE POLICY hybrid_insert ON {table}
            FOR INSERT
            WITH CHECK (
                organization_id IS NOT NULL
                AND organization_id = current_setting('archie.organization_id', true)::int
            )
        """))

        # Drop and recreate UPDATE policy with explicit IS NOT NULL check
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

        # Drop and recreate DELETE policy with explicit IS NOT NULL check
        if _policy_exists(bind, table, "hybrid_delete"):
            bind.execute(text(f"DROP POLICY hybrid_delete ON {table}"))
        bind.execute(text(f"""
            CREATE POLICY hybrid_delete ON {table}
            FOR DELETE
            USING (
                organization_id IS NOT NULL
                AND organization_id = current_setting('archie.organization_id', true)::int
            )
        """))


def downgrade():
    bind = op.get_bind()

    for table in HYBRID_TABLES:
        if not _table_exists(bind, table):
            continue
        for policy in ("hybrid_insert", "hybrid_update", "hybrid_delete"):
            if _policy_exists(bind, table, policy):
                bind.execute(text(f"DROP POLICY {policy} ON {table}"))
        # Recreate original policies (without IS NOT NULL)
        bind.execute(text(f"""
            CREATE POLICY hybrid_insert ON {table}
            FOR INSERT
            WITH CHECK (organization_id = current_setting('archie.organization_id', true)::int)
        """))
        bind.execute(text(f"""
            CREATE POLICY hybrid_update ON {table}
            FOR UPDATE
            USING (organization_id = current_setting('archie.organization_id', true)::int)
            WITH CHECK (organization_id = current_setting('archie.organization_id', true)::int)
        """))
        bind.execute(text(f"""
            CREATE POLICY hybrid_delete ON {table}
            FOR DELETE
            USING (organization_id = current_setting('archie.organization_id', true)::int)
        """))
