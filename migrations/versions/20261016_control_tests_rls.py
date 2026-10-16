"""Row-level security on control_tests.

A new fencing revision following ``20261016_control_evidence``, which creates
``control_tests``. ``tests/test_row_level_security.py`` unions every revision
carrying a ``TENANT_TABLES``/``HYBRID_TABLES`` constant, so this file alone
brings the table under the guard; the base revision's helper functions and
policy text are copied here rather than imported. A row is visible only under
its own organisation's session; the platform scope sees all rows.

Revision ID: 20261016_control_tests_rls
Revises: 20261016_control_evidence
Create Date: 2026-10-16
"""
from alembic import op
from sqlalchemy import text

revision = "20261016_control_tests_rls"
down_revision = "20261016_control_evidence"
branch_labels = None
depends_on = None


# Identical text to 20261008_row_level_security's own constants -- copied, not
# imported, so this revision stays runnable on its own if the base file ever
# changes.
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

# The one table this revision fences; the base revision cannot name a table
# created after it ran.
TENANT_TABLES = ("control_tests",)
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
