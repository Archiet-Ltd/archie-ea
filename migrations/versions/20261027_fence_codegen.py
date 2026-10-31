"""Tenant-fence batch: Codegen.

Each table below belongs to one organisation but had no organization_id (or had one with no
fence), so it relied on every route remembering an owner check. This revision, per table:
adds organization_id when absent, backfills it from the parent row named in BACKFILL (tried in
order), tightens it to NOT NULL when every row was reached, indexes it, adds the foreign key and
switches on the four standard row-level-security policies (identical text to
20261008_row_level_security).

A row whose parent is gone or was never set has no derivable organisation. It is left NULL (and
the column stays nullable on that database) rather than guessed; the policies and the ORM filter
both exclude NULL rows from every tenant. Each table with leftover NULL rows is printed as
``NULL-ROWS <table>: <n>`` so the count is on record.

VERIFY lists tables that already inherit the mixin: the revision only makes sure the column is
NOT NULL (when no NULL rows) and the four policies exist. HYBRID lists shared-plus-override
tables (nullable forever) that get the hybrid policies.

Idempotent: a second run changes nothing. A database built fresh from the models already has the
columns, so the revision then only fences.

Revision ID: 20261027_fence_codegen
Revises: 20261027_fence_solution_family
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "20261027_fence_codegen"
down_revision = "20261027_fence_solution_family"
branch_labels = None
depends_on = None

# (table, ((parent_table, fk_column), ...)): parents before children, sources tried in order.
BACKFILL = (
    ('codegen_chat_messages', (('solutions', 'solution_id'),)),
    ('codegen_connector_credentials', (('solutions', 'solution_id'),)),
    ('codegen_data_imports', (('solutions', 'solution_id'),)),
    ('codegen_drift_reports', (('solutions', 'solution_id'),)),
    ('codegen_generations', (('solutions', 'solution_id'),)),
    ('codegen_scenario_results', (('solutions', 'solution_id'),)),
    ('codegen_solution_connectors', (('solutions', 'solution_id'),)),
    ('codegen_solution_instances', (('solutions', 'solution_id'),)),
    ('codegen_solution_rules', (('solutions', 'solution_id'),)),
    ('codegen_system_boundaries', (('users', 'created_by_id'),)),
    ('codegen_system_boundary_solutions', (('solutions', 'solution_id'),)),
    ('codegen_template_sets', (('users', 'created_by_id'),)),
    ('codegen_test_runs', (('solutions', 'solution_id'),)),
    ('codegen_workflow_designs', (('solutions', 'solution_id'),)),
    ('cross_layer_relationships', (('solutions', 'solution_id'),)),
    ('experience_elements', (('solutions', 'solution_id'),)),
    ('solution_layer_elements', (('solutions', 'solution_id'),)),
    ('codegen_generation_history', (('codegen_generations', 'codegen_generation_id'),)),
    ('codegen_template_files', (('codegen_template_sets', 'set_id'),)),
    ('codegen_test_run_steps', (('codegen_test_runs', 'test_run_id'),)),
)

VERIFY = ()

HYBRID = ()

# Read by tests/test_row_level_security.py's guard that every tenant model is fenced.
TENANT_TABLES = tuple(t for t, _ in BACKFILL) + VERIFY
HYBRID_TABLES = HYBRID

_ORG = "NULLIF(current_setting('archie.organization_id', true), '')::integer"
_PLATFORM = "COALESCE(current_setting('archie.platform_scope', true), '') = 'on'"
_TENANT_RULE = f"organization_id = {_ORG} OR {_PLATFORM}"
_HYBRID_READ = f"organization_id IS NULL OR organization_id = {_ORG} OR {_PLATFORM}"
_HYBRID_WRITE = f"(organization_id IS NOT NULL AND organization_id = {_ORG}) OR {_PLATFORM}"

TENANT_POLICIES = ("tenant_select", "tenant_insert", "tenant_update", "tenant_delete")
HYBRID_POLICIES = ("hybrid_select", "hybrid_insert", "hybrid_update", "hybrid_delete")
_TENANT_DEFINITIONS = (
    ("SELECT", _TENANT_RULE, None),
    ("INSERT", None, _TENANT_RULE),
    ("UPDATE", _TENANT_RULE, _TENANT_RULE),
    ("DELETE", _TENANT_RULE, None),
)
_HYBRID_DEFINITIONS = (
    ("SELECT", _HYBRID_READ, None),
    ("INSERT", None, _HYBRID_WRITE),
    ("UPDATE", _HYBRID_WRITE, _HYBRID_WRITE),
    ("DELETE", _HYBRID_WRITE, None),
)


def _short(prefix, table):
    name = f"{prefix}_{table}_organization_id"
    if len(name) <= 63:
        return name
    import hashlib

    return f"{prefix}_{hashlib.sha1(table.encode()).hexdigest()[:10]}_{table[:30]}_org"[:63]


def _exists(bind, table):
    return bool(bind.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


def _has_column(bind, table, column="organization_id"):
    return bool(
        bind.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def _may_alter(bind, table):
    return bool(
        bind.execute(
            text(
                "SELECT pg_has_role(current_user, c.relowner, 'USAGE') "
                "FROM pg_class c WHERE c.oid = to_regclass(:t)"
            ),
            {"t": f"public.{table}"},
        ).scalar()
    )


def _policy_count(bind, table, names):
    return bind.execute(
        text("SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND tablename = :t AND policyname = ANY(:n)"),
        {"t": table, "n": list(names)},
    ).scalar()


def _fence(bind, table, names, definitions):
    for name, (command, using, check) in zip(names, definitions):
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - literal table name
        clause = ""
        if using is not None:
            clause += f" USING ({using})"
        if check is not None:
            clause += f" WITH CHECK ({check})"
        bind.execute(text(f"CREATE POLICY {name} ON public.{table} FOR {command}{clause}"))  # nosec B608
    bind.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))  # nosec B608


def _column_and_backfill(bind, table, sources):
    if not _has_column(bind, table):
        op.add_column(table, sa.Column("organization_id", sa.Integer(), nullable=True))
    for parent, column in sources:
        if not (_exists(bind, parent) and _has_column(bind, parent) and _has_column(bind, table, column)):
            continue
        bind.execute(
            text(
                f"UPDATE public.{table} AS c SET organization_id = p.organization_id "  # nosec B608
                f"FROM public.{parent} AS p WHERE c.{column} = p.id "
                "AND c.organization_id IS NULL AND p.organization_id IS NOT NULL"
            )
        )


def _tighten_index_fk(bind, table):
    nulls = bind.execute(text(f"SELECT count(*) FROM public.{table} WHERE organization_id IS NULL")).scalar()  # nosec B608
    if nulls == 0:
        op.alter_column(table, "organization_id", existing_type=sa.Integer(), nullable=False)
    else:
        print(f"NULL-ROWS {table}: {nulls}")
    op.create_index(_short("ix", table), table, ["organization_id"], if_not_exists=True)
    covered = bind.execute(
        text(
            "SELECT 1 FROM pg_constraint c JOIN pg_attribute a "
            "ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
            "WHERE c.contype = 'f' AND c.conrelid = to_regclass(:t) AND a.attname = 'organization_id'"
        ),
        {"t": f"public.{table}"},
    ).scalar()
    if not covered:
        op.create_foreign_key(_short("fk", table), table, "organizations", ["organization_id"], ["id"], ondelete="CASCADE")


def upgrade():
    bind = op.get_bind()
    for table, sources in BACKFILL:
        if not _exists(bind, table):
            continue  # create_all / the baseline brings it up with the column
        _column_and_backfill(bind, table, sources)
        _tighten_index_fk(bind, table)
        if _may_alter(bind, table):
            _fence(bind, table, TENANT_POLICIES, _TENANT_DEFINITIONS)
        else:
            print(f"WARNING row-level security skipped: {table} is not owned by the migration role")
    for table in VERIFY:
        if not (_exists(bind, table) and _has_column(bind, table)):
            continue
        nulls = bind.execute(text(f"SELECT count(*) FROM public.{table} WHERE organization_id IS NULL")).scalar()  # nosec B608
        if nulls == 0:
            op.alter_column(table, "organization_id", existing_type=sa.Integer(), nullable=False)
        else:
            print(f"NULL-ROWS {table}: {nulls}")
        if _policy_count(bind, table, TENANT_POLICIES) != 4 and _may_alter(bind, table):
            _fence(bind, table, TENANT_POLICIES, _TENANT_DEFINITIONS)
            print(f"row-level security: re-applied on {table}")
    for table in HYBRID:
        if not (_exists(bind, table) and _has_column(bind, table)):
            continue
        if _policy_count(bind, table, HYBRID_POLICIES) != 4 and _may_alter(bind, table):
            _fence(bind, table, HYBRID_POLICIES, _HYBRID_DEFINITIONS)


def downgrade():
    bind = op.get_bind()
    for table, _ in BACKFILL:
        if not _exists(bind, table):
            continue
        if _may_alter(bind, table):
            for name in TENANT_POLICIES:
                bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608
            bind.execute(text(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY"))  # nosec B608
        # The column is kept: dropping it would discard the only record of ownership.
        op.alter_column(table, "organization_id", existing_type=sa.Integer(), nullable=True)
