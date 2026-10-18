"""Scenario branches: what-if changes laid over the base model.

Creates ``scenario_branches`` (a named branch per organisation) and
``scenario_changes`` (its ordered changes), and fences both with row-level
security like their neighbours. Nothing existing is read, moved or changed:
the tables are new and start empty, and the base element and relationship
tables are not touched by anything here.

The unused ``roadmap_scenarios`` table is left in place; its model was retired
into these two tables.

Idempotent: each table is created only when absent (a database ``init-db``
reached first is a no-op), indexes use ``IF NOT EXISTS`` and the policies are
dropped and recreated.

Revision ID: 20261018_scenario_branches
Revises: 20261016_compliance_org_rls
Create Date: 2026-10-18
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20261018_scenario_branches"
down_revision = "20261016_compliance_org_rls"
branch_labels = None
depends_on = None

# Same policy text as 20261008_row_level_security, copied so this revision runs on
# its own; tests/test_row_level_security.py unions every revision's TENANT_TABLES.
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

TENANT_TABLES = ("scenario_branches", "scenario_changes")
HYBRID_TABLES = ()


def _org_column():
    return sa.Column(
        "organization_id", sa.Integer(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )


def _fence(bind, table: str) -> None:
    # Table names are literal module constants; nothing here is user input.
    for name, (command, using, check) in zip(TENANT_POLICIES, _TENANT_DEFINITIONS):
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - literal table names
        clause = ""
        if using is not None:
            clause += f" USING ({using})"
        if check is not None:
            clause += f" WITH CHECK ({check})"
        bind.execute(text(f"CREATE POLICY {name} ON public.{table} FOR {command}{clause}"))  # nosec B608 - literal table names
    bind.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))  # nosec B608 - literal table names


def _may_alter(bind, table: str) -> bool:
    return bool(
        bind.execute(
            text(
                "SELECT pg_has_role(current_user, c.relowner, 'USAGE') "
                "FROM pg_class c WHERE c.oid = to_regclass(:t)"
            ),
            {"t": f"public.{table}"},
        ).scalar()
    )


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("scenario_branches"):
        op.create_table(
            "scenario_branches",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text()),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            _org_column(),
            sa.UniqueConstraint("organization_id", "name", name="uq_scenario_branches_org_name"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_scenario_branches_organization_id ON scenario_branches (organization_id)")

    if not inspector.has_table("scenario_changes"):
        op.create_table(
            "scenario_changes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "branch_id", sa.Integer(), sa.ForeignKey("scenario_branches.id", ondelete="CASCADE"), nullable=False
            ),
            sa.Column("change_kind", sa.String(10), nullable=False),
            sa.Column("target_kind", sa.String(12), nullable=False),
            sa.Column("target_id", sa.Integer()),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            _org_column(),
        )
    op.execute("CREATE INDEX IF NOT EXISTS ix_scenario_changes_organization_id ON scenario_changes (organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_scenario_changes_branch_id ON scenario_changes (branch_id)")

    for table in TENANT_TABLES:
        if not _may_alter(bind, table):
            print(f"WARNING row-level security skipped: {table} is not owned by the migration role")
            continue
        _fence(bind, table)
        print(f"row-level security: fenced {table}")


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    for table in ("scenario_changes", "scenario_branches"):
        if inspector.has_table(table):
            op.drop_table(table)
