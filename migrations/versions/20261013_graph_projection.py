"""Per-organisation graph projection snapshots, with row-level security.

Adds ``graph_projection_snapshots``: one row per organisation holding a
compressed copy of that organisation's graph projection and the event-log
offset it reached. The relational store remains the authority; the row is a
disposable cache. The same revision fences the table with the four tenant
policies the other tenant tables carry (policy text copied from the earlier
fencing revisions, listing only this table).

Idempotent: the table is created only when absent, and the
policies are dropped and recreated, so a second run (or a database where
``init-db`` created the table first) converges.

Revision ID: 20261013_graph_projection
Revises: 20261009_app_comp_elem
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "20261013_graph_projection"
down_revision = "20261009_app_comp_elem"
branch_labels = None
depends_on = None


# Identical text to 20261008_row_level_security's own constants -- copied, not
# imported, so this revision stays runnable on its own.
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

# Only the table this revision exists to fence.
TENANT_TABLES = ("graph_projection_snapshots",)
HYBRID_TABLES = ()


def _skip_reason(bind, table: str):
    present = bind.execute(
        text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}
    ).scalar()
    if not present:
        return "absent"
    has_column = bind.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t "
            "AND column_name = 'organization_id')"
        ),
        {"t": table},
    ).scalar()
    if not has_column:
        print(f"WARNING row-level security skipped: {table} has no organization_id column")
        return "no organization_id column"
    may_alter = bind.execute(
        text(
            "SELECT pg_has_role(current_user, c.relowner, 'USAGE') "
            "FROM pg_class c WHERE c.oid = to_regclass(:t)"
        ),
        {"t": f"public.{table}"},
    ).scalar()
    if not may_alter:
        print(f"WARNING row-level security skipped: {table} is not owned by the migration role")
        return "not owned by the migration role"
    return None


def _fence(bind, table: str) -> None:
    # Table name is a literal module constant; nothing here is user input.
    for name, (command, using, check) in zip(TENANT_POLICIES, _TENANT_DEFINITIONS):
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - table names from a literal constant
        clause = ""
        if using is not None:
            clause += f" USING ({using})"
        if check is not None:
            clause += f" WITH CHECK ({check})"
        bind.execute(
            text(f"CREATE POLICY {name} ON public.{table} FOR {command}{clause}")  # nosec B608 - table names from a literal constant
        )
    bind.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))  # nosec B608 - table names from a literal constant


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("graph_projection_snapshots"):
        op.create_table(
            "graph_projection_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("organization_id", sa.Integer(),
                      sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("format_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("last_ordinal", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("node_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("edge_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("fingerprint", sa.String(64), nullable=False),
            sa.Column("payload", sa.LargeBinary(), nullable=False),
            sa.Column("built_at", sa.DateTime(), nullable=False),
            sa.Column("verified_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
            sa.UniqueConstraint("organization_id", name="uq_graph_projection_snapshot_org"),
        )
    # No separate index on organization_id: the unique constraint above already indexes it.
    # (Databases created from the model also get ix_graph_projection_snapshots_organization_id, from
    # the tenant mixin's index=True; it is redundant with the constraint and harmless.)

    for table in TENANT_TABLES:
        reason = _skip_reason(bind, table)
        if reason is not None:
            print(f"row-level security: skipped {table} ({reason})")
            continue
        _fence(bind, table)
        print(f"row-level security: fenced {table}")


def downgrade():
    bind = op.get_bind()
    for table in TENANT_TABLES:
        if _skip_reason(bind, table) is not None:
            continue
        for name in TENANT_POLICIES:
            bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - table names from a literal constant
        bind.execute(text(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY"))  # nosec B608 - table names from a literal constant
    op.execute("DROP TABLE IF EXISTS graph_projection_snapshots")
