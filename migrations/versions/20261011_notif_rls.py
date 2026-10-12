"""Row-level security on notifications (PR 1 fencing).

The notifications table gained organization_id in
20261011_notification_columns. This revision fences it with the same
tenant-isolation policies as every other tenant table.

Revision ID: 20261011_notif_rls
Revises: 20261011_notification_columns
Create Date: 2026-10-11
"""
from alembic import op
from sqlalchemy import text

revision = "20261011_notif_rls"
down_revision = "20261011_notification_columns"
branch_labels = None
depends_on = None


# Identical text to 20261008_row_level_security's own constants -- copied, not
# imported, so this revision stays runnable on its own if the base file ever
# changes.
_ORG = "NULLIF(current_setting('archie.organization_id', true), '')::integer"
_PLATFORM = "COALESCE(current_setting('archie.platform_scope', true), '') = 'on'"
_TENANT_RULE = f"organization_id = {_ORG} OR {_PLATFORM}"

# notifications gained organization_id by backfilling from the recipient
# user, but legacy rows for a user with no organisation stay NULL. The runtime
# role is NOBYPASSRLS, so a pure `organization_id = _ORG` SELECT would make
# those rows vanish from that user's own bell. The bell still filters by
# user_id in the app, so admitting a NULL-org row whose owning user belongs to
# the session organisation keeps the row visible to its owner without leaking
# it to any other organisation's context.
_NOTIF_SELECT_RULE = (
    f"organization_id = {_ORG} OR "
    f"(organization_id IS NULL AND user_id IN "
    f"(SELECT id FROM users WHERE organization_id = {_ORG})) OR "
    f"{_PLATFORM}"
)

TENANT_POLICIES = ("tenant_select", "tenant_insert", "tenant_update", "tenant_delete")

# Only the table this revision exists to fence. Its SELECT differs from the
# standard tenant rule (see _NOTIF_SELECT_RULE above); INSERT/UPDATE/DELETE
# keep the standard rule, so a new notification is stamped with the session
# organisation (NotificationService.create resolves it from the recipient).
TENANT_TABLES = ("notifications",)
HYBRID_TABLES = ()


def _select_rule(table: str) -> str:
    if table == "notifications":
        return _NOTIF_SELECT_RULE
    return _TENANT_RULE


_TENANT_DEFINITIONS = (
    ("SELECT", None, None),   # USING filled per-table by _fence via _select_rule
    ("INSERT", None, _TENANT_RULE),
    ("UPDATE", _TENANT_RULE, _TENANT_RULE),
    ("DELETE", _TENANT_RULE, None),
)


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
    for name, (command, using, check) in zip(TENANT_POLICIES, _TENANT_DEFINITIONS):
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - table names from a literal constant
        if command == "SELECT":
            using = _select_rule(table)
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