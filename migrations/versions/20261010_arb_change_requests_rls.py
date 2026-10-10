"""Row-level security on arb_change_requests (R1-B20 PR 3 fencing follow-up).

A new fencing revision, not an edit to the applied ``20261008_row_level_security``
(Alembic runs a revision once): ``ChangeRequest`` gained ``TenantMixin`` and its
``organization_id`` column only after 20261008_row_level_security was written
(PR 297 defects 2-3, migration 20261008_cr_organization_id), so it was not and
could not have been in that revision's own ``TENANT_TABLES`` list.
``tests/test_row_level_security.py::test_every_tenant_and_hybrid_model_is_listed_or_excluded``
unions every revision carrying a ``TENANT_TABLES``/``HYBRID_TABLES`` constant,
so this file alone is enough for the guard to pick it up; the base revision's
own helper functions and policy text are copied here rather than imported, per
that test's own instruction ("copy 20261008_row_level_security, list only the
new tables").

``organization_id`` on this table is nullable (20261008_cr_organization_id's own
backfill note: a change request with no linked review item has no derivable
tenant and is left NULL rather than guessed). That is the same situation every
other ``TenantMixin`` table in the base revision is already written to handle:
the ``NULLIF``-based session-organisation comparison is never true against a
NULL column, so a tenant-less row is invisible under every organisation's
session and visible only under ``platform_scope`` -- not a special case, just
the base revision's existing ``_TENANT_RULE`` applied here too.

Revision ID: 20261010_arb_change_requests_rls
Revises: 20261008_row_level_security
Create Date: 2026-10-10
"""
from alembic import op
from sqlalchemy import text

revision = "20261010_arb_change_requests_rls"
down_revision = "20261008_row_level_security"
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

# Only the table this revision exists to fence. The guard unions this with
# every other revision's TENANT_TABLES/HYBRID_TABLES -- do not add tables
# already listed in 20261008_row_level_security here.
TENANT_TABLES = ("arb_change_requests",)
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
