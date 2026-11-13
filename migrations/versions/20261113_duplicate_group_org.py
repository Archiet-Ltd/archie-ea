"""Give duplicate groups an organisation.

unified_duplicate_groups was global: every organisation saw every other's
duplicate groups. It now carries ``organization_id`` (TenantMixin), NOT NULL,
with a foreign key to organizations, an index, and row-level security.

Backfill: a group takes the organisation of its detection run
(detection_run_id -> unified_detection_runs.organization_id). A group with no
run, or whose run has no organisation, takes the organisation of its members'
applications when all members belong to one organisation. Only groups whose
members span multiple organisations, or that have no members at all, are
deleted -- they cannot be shown to anyone safely.

Idempotent: every step checks before it acts, so a second run is a no-op.

Revision ID: 20261113_duplicate_group_org
Revises: 20261113_detect_consol_org
Create Date: 2026-11-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261113_duplicate_group_org"
down_revision = "20261113_detect_consol_org"
branch_labels = None
depends_on = None

TABLE = "unified_duplicate_groups"

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


def upgrade():
    bind = op.get_bind()
    if not _present(bind, TABLE):
        return

    if not _has_column(bind, TABLE, "organization_id"):
        op.add_column(TABLE, sa.Column("organization_id", sa.Integer(), nullable=True))

    # Backfill: a group takes the org of its detection run.
    bind.execute(
        text(
            "UPDATE unified_duplicate_groups g SET organization_id = r.organization_id "
            "FROM unified_detection_runs r "
            "WHERE g.organization_id IS NULL AND r.id = g.detection_run_id "
            "AND r.organization_id IS NOT NULL"
        )
    )

    # Groups with no run, or whose run has no organisation: try to assign from
    # their members' organisation. A group whose members all belong to one
    # organisation takes that organisation.
    bind.execute(
        text(
            "UPDATE unified_duplicate_groups g SET organization_id = sub.org_id "
            "FROM ("
            "  SELECT g.id as gid, MIN(ac.organization_id) as org_id "
            "  FROM unified_duplicate_groups g "
            "  JOIN unified_group_members ugm ON ugm.group_id = g.id "
            "  JOIN application_components ac ON ac.id = ugm.application_id "
            "  WHERE g.organization_id IS NULL AND ac.organization_id IS NOT NULL "
            "  GROUP BY g.id "
            "  HAVING COUNT(DISTINCT ac.organization_id) = 1"
            ") sub "
            "WHERE g.id = sub.gid"
        )
    )

    # Groups still without an organisation are unattributable: their members
    # span multiple organisations, or they have no members at all.
    gone = bind.execute(text("SELECT COUNT(*) FROM unified_duplicate_groups WHERE organization_id IS NULL")).scalar()
    bind.execute(text("DELETE FROM unified_duplicate_groups WHERE organization_id IS NULL"))
    print(f"duplicate group org fence: {gone} unattributable group(s) deleted")

    op.alter_column(TABLE, "organization_id", existing_type=sa.Integer(), nullable=False)
    op.create_index(f"ix_{TABLE}_organization_id", TABLE, ["organization_id"], if_not_exists=True)
    fk = f"fk_{TABLE}_organization_id"
    if not bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": fk}).scalar():
        op.create_foreign_key(fk, TABLE, "organizations", ["organization_id"], ["id"], ondelete="CASCADE")

    # Row-level security
    owned = bind.execute(
        text("SELECT pg_has_role(current_user, c.relowner, 'USAGE') FROM pg_class c WHERE c.oid = to_regclass(:t)"),
        {"t": f"public.{TABLE}"},
    ).scalar()
    if owned:
        for name, (command, using, check) in zip(TENANT_POLICIES, _TENANT_DEFINITIONS):
            bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{TABLE}"))  # nosec B608 - literal table names
            clause = ""
            if using is not None:
                clause += f" USING ({using})"
            if check is not None:
                clause += f" WITH CHECK ({check})"
            bind.execute(text(f"CREATE POLICY {name} ON public.{TABLE} FOR {command}{clause}"))  # nosec B608 - literal table names
        bind.execute(text(f"ALTER TABLE public.{TABLE} FORCE ROW LEVEL SECURITY"))  # nosec B608 - literal table names
        print(f"row-level security: fenced {TABLE}")
    else:
        print(f"WARNING row-level security skipped: {TABLE} is not owned by the migration role")


def downgrade():
    bind = op.get_bind()
    if not _present(bind, TABLE) or not _has_column(bind, TABLE, "organization_id"):
        return
    bind.execute(text(f"ALTER TABLE public.{TABLE} DISABLE ROW LEVEL SECURITY"))  # nosec B608 - literal table names
    for name in TENANT_POLICIES:
        bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{TABLE}"))  # nosec B608 - literal table names
    fk = f"fk_{TABLE}_organization_id"
    if bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": fk}).scalar():
        op.drop_constraint(fk, TABLE, type_="foreignkey")
    op.drop_index(f"ix_{TABLE}_organization_id", table_name=TABLE, if_exists=True)
    op.drop_column(TABLE, "organization_id")
