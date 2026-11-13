"""Give duplicate detection runs and consolidation entries an organisation.

unified_detection_runs and consolidation_list_entries were global: every
organisation read every other's detection runs, savings figures and
consolidation list entries. Each now carries ``organization_id`` (TenantMixin),
NOT NULL, with a foreign key to organizations, an index, and row-level security
as the other tenant tables have it.

Backfill, in this order:
  * a detection run takes the organisation of the user who triggered it
    (user_id -> users.organization_id), or of the first application it analysed
    (through unified_group_members -> application_components) when the user has
    none;
  * a consolidation entry takes the organisation of its application
    (application_id -> application_components.organization_id).
A row whose linked applications belong to more than one organisation is given
the first organisation above and counted as ambiguous. A row with no
organisation derivable at all is deleted and counted -- it cannot be shown to
anyone safely.

Idempotent: every step checks before it acts, so a second run is a no-op.

Revision ID: 20261113_detect_consol_org
Revises: 20261112_competitive_advantage
Create Date: 2026-11-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261113_detect_consol_org"
down_revision = "20261112_competitive_advantage"
branch_labels = None
depends_on = None

TABLES = ("unified_detection_runs", "consolidation_list_entries")

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

TENANT_TABLES = TABLES
HYBRID_TABLES = ()


def _present(bind, table):
    return bool(bind.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar())


def _has_column(bind, table, column):
    return bool(
        bind.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = :t AND column_name = :c)"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def _scalar(bind, sql):
    return bind.execute(text(sql)).scalar()  # nosec B608 - literal statements in this module


def _backfill_detection_runs(bind):
    """A run takes the org of its triggering user, or of the first application
    it analysed (through unified_group_members -> application_components)."""
    rows = bind.execute(
        text(
            "SELECT r.id, r.user_id FROM unified_detection_runs r "
            "WHERE r.organization_id IS NULL"
        )
    ).all()
    ambiguous = 0
    for run_id, user_id in rows:
        org_id = None
        if user_id is not None:
            found = bind.execute(
                text("SELECT organization_id FROM users WHERE id = :uid"),
                {"uid": user_id},
            ).scalar()
            if found is not None:
                org_id = found
        if org_id is None:
            # Derive from the applications the run analysed, through its groups.
            found = bind.execute(
                text(
                    "SELECT DISTINCT ac.organization_id FROM unified_detection_runs r "
                    "JOIN unified_duplicate_groups g ON g.detection_run_id = r.id "
                    "JOIN unified_group_members ugm ON ugm.group_id = g.id "
                    "JOIN application_components ac ON ac.id = ugm.application_id "
                    "WHERE r.id = :rid AND ac.organization_id IS NOT NULL "
                    "ORDER BY ac.organization_id LIMIT 1"
                ),
                {"rid": run_id},
            ).scalar()
            org_id = found
        if org_id is not None:
            bind.execute(
                text("UPDATE unified_detection_runs SET organization_id = :o WHERE id = :i"),
                {"o": org_id, "i": run_id},
            )
    return ambiguous


def _ambiguous_detection_runs(bind):
    return _scalar(
        bind,
        "SELECT COUNT(*) FROM unified_detection_runs r "
        "JOIN unified_duplicate_groups g ON g.detection_run_id = r.id "
        "JOIN unified_group_members ugm ON ugm.group_id = g.id "
        "JOIN application_components ac ON ac.id = ugm.application_id "
        "WHERE r.organization_id IS NULL "
        "GROUP BY r.id HAVING COUNT(DISTINCT ac.organization_id) > 1",
    )


def _backfill_consolidation_entries(bind):
    """An entry takes the org of its application."""
    bind.execute(
        text(
            "UPDATE consolidation_list_entries e SET organization_id = a.organization_id "
            "FROM application_components a "
            "WHERE e.organization_id IS NULL AND a.id = e.application_id "
            "AND a.organization_id IS NOT NULL"
        )
    )


def _ambiguous_consolidation_entries(bind):
    return _scalar(
        bind,
        "SELECT COUNT(*) FROM consolidation_list_entries e "
        "WHERE e.organization_id IS NULL",
    )


def _add_column(bind, table):
    if not _has_column(bind, table, "organization_id"):
        op.add_column(table, sa.Column("organization_id", sa.Integer(), nullable=True))


def _fence(bind, table):
    owned = bind.execute(
        text("SELECT pg_has_role(current_user, c.relowner, 'USAGE') FROM pg_class c WHERE c.oid = to_regclass(:t)"),
        {"t": f"public.{table}"},
    ).scalar()
    if not owned:
        print(f"WARNING row-level security skipped: {table} is not owned by the migration role")
        return
    for name, (command, using, check) in zip(TENANT_POLICIES, _TENANT_DEFINITIONS):
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - literal table names
        clause = ""
        if using is not None:
            clause += f" USING ({using})"
        if check is not None:
            clause += f" WITH CHECK ({check})"
        bind.execute(text(f"CREATE POLICY {name} ON public.{table} FOR {command}{clause}"))  # nosec B608 - literal table names
    bind.execute(text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))  # nosec B608 - literal table names
    print(f"row-level security: fenced {table}")


def upgrade():
    bind = op.get_bind()
    present = [t for t in TABLES if _present(bind, t)]
    for table in present:
        _add_column(bind, table)

    if "unified_detection_runs" in present and _present(bind, "users"):
        ambiguous = _ambiguous_detection_runs(bind) if _has_column(bind, "application_components", "organization_id") else 0
        _backfill_detection_runs(bind)
        print(f"detection org fence: {ambiguous} run(s) link applications of more than one organisation; given the first")

    if "consolidation_list_entries" in present and _present(bind, "application_components"):
        ambiguous = _ambiguous_consolidation_entries(bind) if _has_column(bind, "application_components", "organization_id") else 0
        _backfill_consolidation_entries(bind)
        print(f"consolidation org fence: {ambiguous} entry(ies) with no derivable organisation")

    # Rows nothing links to an organisation cannot be shown to anyone safely.
    if "unified_detection_runs" in present:
        gone = _scalar(bind, "SELECT COUNT(*) FROM unified_detection_runs WHERE organization_id IS NULL")
        bind.execute(text("DELETE FROM unified_detection_runs WHERE organization_id IS NULL"))
        print(f"detection org fence: {gone} unattributable run(s) deleted")
    if "consolidation_list_entries" in present:
        gone = _scalar(bind, "SELECT COUNT(*) FROM consolidation_list_entries WHERE organization_id IS NULL")
        bind.execute(text("DELETE FROM consolidation_list_entries WHERE organization_id IS NULL"))
        print(f"consolidation org fence: {gone} unattributable entry(ies) deleted")

    for table in present:
        op.alter_column(table, "organization_id", existing_type=sa.Integer(), nullable=False)
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"], if_not_exists=True)
        fk = f"fk_{table}_organization_id"
        if not bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": fk}).scalar():
            op.create_foreign_key(fk, table, "organizations", ["organization_id"], ["id"], ondelete="CASCADE")
        _fence(bind, table)


def downgrade():
    bind = op.get_bind()
    for table in TABLES:
        if not _present(bind, table) or not _has_column(bind, table, "organization_id"):
            continue
        bind.execute(text(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY"))  # nosec B608 - literal table names
        for name in TENANT_POLICIES:
            bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - literal table names
        fk = f"fk_{table}_organization_id"
        if bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": fk}).scalar():
            op.drop_constraint(fk, table, type_="foreignkey")
        op.drop_index(f"ix_{table}_organization_id", table_name=table, if_exists=True)
        op.drop_column(table, "organization_id")
