"""Rate limits an administrator can see and set: allow and deny lists, shared counters, violations.

Three tables: rate_limit_rules (platform-wide allow and deny entries),
rate_limit_counters (the limiter's shared counters, so every worker counts one limit) and
rate_limit_violations (one row per refused request, kept thirty days). The violations table
carries a nullable organization_id (the signed-in user's organisation; NULL for someone who is
not signed in) and is fenced with the standard tenant policies, so a NULL row is visible to the
platform scope only.

Idempotent: a table is created only when missing (a database built from the models has them),
and the fence is dropped and re-created.

Revision ID: 20261030_rate_limits
Revises: 20261029_deliv_wp_nullable
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "20261030_rate_limits"
down_revision = "20261029_deliv_wp_nullable"
branch_labels = None
depends_on = None

TABLE = "rate_limit_violations"

# Identical text to 20261008_row_level_security's constants -- copied, not imported, so this
# revision stays runnable on its own.
_ORG = "NULLIF(current_setting('archie.organization_id', true), '')::integer"
_PLATFORM = "COALESCE(current_setting('archie.platform_scope', true), '') = 'on'"
_TENANT_RULE = f"organization_id = {_ORG} OR {_PLATFORM}"

TENANT_POLICIES = ("tenant_select", "tenant_insert", "tenant_update", "tenant_delete")
_TENANT_DEFINITIONS = (
    ("SELECT", _TENANT_RULE, None),
    ("INSERT", None, _TENANT_RULE),
    ("UPDATE", _TENANT_RULE, _TENANT_RULE),
    ("DELETE", _TENANT_RULE, None),
)

# Read by tests/test_row_level_security.py's guard that every TenantMixin table is fenced.
TENANT_TABLES = (TABLE,)
HYBRID_TABLES = ()


def _may_alter(bind) -> bool:
    return bool(
        bind.execute(
            text(
                "SELECT pg_has_role(current_user, c.relowner, 'USAGE') "
                "FROM pg_class c WHERE c.oid = to_regclass(:t)"
            ),
            {"t": f"public.{TABLE}"},
        ).scalar()
    )


def _fence(bind) -> None:
    for name, (command, using, check) in zip(TENANT_POLICIES, _TENANT_DEFINITIONS):
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{TABLE}"))  # nosec B608 - literal table name
        clause = ""
        if using is not None:
            clause += f" USING ({using})"
        if check is not None:
            clause += f" WITH CHECK ({check})"
        bind.execute(text(f"CREATE POLICY {name} ON public.{TABLE} FOR {command}{clause}"))  # nosec B608 - literal table name
    bind.execute(text(f"ALTER TABLE public.{TABLE} ENABLE ROW LEVEL SECURITY"))  # nosec B608 - literal table name


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("rate_limit_rules"):
        op.create_table(
            "rate_limit_rules",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("action", sa.String(length=8), nullable=False),
            sa.Column("kind", sa.String(length=8), nullable=False),
            sa.Column("value", sa.String(length=64), nullable=False),
            sa.Column("note", sa.String(length=255)),
            sa.Column("created_by_id", sa.Integer()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("action", "kind", "value", name="uq_rate_limit_rules_entry"),
        )

    if not inspector.has_table("rate_limit_counters"):
        op.create_table(
            "rate_limit_counters",
            sa.Column("key", sa.String(length=255), primary_key=True),
            sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("expires_at", sa.Float(), nullable=False),
        )

    if not inspector.has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "organization_id", sa.Integer(),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True,
            ),
            sa.Column("address", sa.String(length=64)),
            sa.Column("user_id", sa.Integer()),
            sa.Column("route", sa.String(length=255)),
            sa.Column("rule", sa.String(length=120), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_rate_limit_violations_organization_id", TABLE, ["organization_id"])
        op.create_index("ix_rate_limit_violations_address", TABLE, ["address"])
        op.create_index("ix_rate_limit_violations_user_id", TABLE, ["user_id"])
        op.create_index("ix_rate_limit_violations_created_at", TABLE, ["created_at"])

    if _may_alter(bind):
        _fence(bind)
        print(f"row-level security: fenced {TABLE}")
    else:
        print(f"WARNING row-level security skipped: {TABLE} is not owned by the migration role")


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for name in (TABLE, "rate_limit_counters", "rate_limit_rules"):
        if inspector.has_table(name):
            op.drop_table(name)
