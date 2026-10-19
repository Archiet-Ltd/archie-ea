"""Row-level security on the seven tables the tree API now scopes to an organisation.

A new fencing revision on top of the one that gives these tables their
``organization_id``. Same policy text as ``20261008_row_level_security``,
copied rather than imported so this revision runs on its own;
``tests/test_row_level_security.py`` unions every revision's ``TENANT_TABLES``.
A row with a NULL organisation (one whose owner could not be derived) matches
no organisation's session and is visible only under the platform scope.

Revision ID: 20261018_tree_models_rls
Revises: 20261018_tree_models_org_id
Create Date: 2026-10-18
"""
from alembic import op
from sqlalchemy import text

revision = "20261018_tree_models_rls"
down_revision = "20261018_tree_models_org_id"
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
TENANT_TABLES = (
    "tasks",
    "portfolio_initiatives",
    "system_boundaries",
    "compliance_requirements",
    "vendor_capability_hierarchy",
    "vendor_process_hierarchy",
    "required_capabilities",
)
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
        if table == "required_capabilities":
            # Also fenced by 20261018_vendor_analysis_rls, which now runs
            # earlier in this chain and keeps its own policies and row-level
            # security on this table regardless of this revision. Disabling
            # them here on a partial rollback (downgrading past this revision
            # but not past that one) would silently drop that revision's
            # fencing too.
            continue
        if _skip_reason(bind, table) is not None:
            continue
        for name in TENANT_POLICIES:
            bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - table names from a literal constant
        bind.execute(text(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY"))  # nosec B608 - table names from a literal constant
