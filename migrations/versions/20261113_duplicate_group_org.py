"""Give duplicate groups an organisation.

unified_duplicate_groups was global: every organisation saw every other's
duplicate groups. It now carries ``organization_id`` NOT NULL,
with a foreign key to organizations, an index, and row-level security.

Backfill: a group takes the organisation of its detection run
(detection_run_id -> unified_detection_runs.organization_id). A group with no
run, or whose run has no organisation, takes the organisation of its members'
applications when all members belong to one organisation. Only groups whose
members span multiple organisations, or that have no members at all, are
removed -- they cannot be shown to anyone safely. Before removal each such
group, and its member rows, is written to detection_fence_removed_rows with the
reason; the downgrade puts them back.

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


def _fk_name(bind, table, column):
    """The name of whatever foreign key actually sits on `column`, detected by
    column presence rather than by the name this migration expects -- the same
    table can carry an auto-named foreign key created by ensure_baseline_schema
    from today's models instead of this migration's own fk_{table}_{column}."""
    return bind.execute(
        text(
            "SELECT con.conname FROM pg_constraint con "
            "JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey) "
            "WHERE con.contype = 'f' AND con.conrelid = to_regclass(:t) "
            "AND att.attname = :c"
        ),
        {"t": f"public.{table}", "c": column},
    ).scalar()


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


RECORD_TABLE = "detection_fence_removed_rows"
MEMBERS = "unified_group_members"


def _ensure_record_table(bind):
    bind.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {RECORD_TABLE} ("  # nosec B608 - literal table name
            "id SERIAL PRIMARY KEY, source_table VARCHAR(100) NOT NULL, source_id INTEGER, "
            "row_data JSONB NOT NULL, reason TEXT NOT NULL, removed_at TIMESTAMP NOT NULL DEFAULT now())"
        )
    )


def _restore_groups(bind):
    """Put back recorded groups, then their member rows."""
    if not _present(bind, RECORD_TABLE):
        return
    cols = [
        r[0]
        for r in bind.execute(
            text(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = :t AND column_name <> 'organization_id' ORDER BY ordinal_position"
            ),
            {"t": TABLE},
        ).all()
    ]
    names = ", ".join(cols)
    for rec_id, group_id in bind.execute(
        text(f"SELECT id, source_id FROM {RECORD_TABLE} WHERE source_table = :t ORDER BY id"), {"t": TABLE}  # nosec B608
    ).all():
        if bind.execute(text(f"SELECT 1 FROM {TABLE} WHERE id = :i"), {"i": group_id}).first():  # nosec B608
            continue
        bind.execute(
            text(
                f"INSERT INTO {TABLE} ({names}) SELECT {names} "  # nosec B608 - names come from information_schema
                f"FROM jsonb_populate_record(NULL::{TABLE}, (SELECT row_data FROM {RECORD_TABLE} WHERE id = :r))"
            ),
            {"r": rec_id},
        )
    if _present(bind, MEMBERS):
        bind.execute(
            text(
                f"INSERT INTO {MEMBERS} (group_id, application_id, similarity_to_primary, is_primary, added_at) "  # nosec B608
                "SELECT m.group_id, m.application_id, m.similarity_to_primary, m.is_primary, m.added_at "
                f"FROM {RECORD_TABLE} r, "  # nosec B608
                f"jsonb_populate_record(NULL::{MEMBERS}, r.row_data) m "
                f"WHERE r.source_table = '{MEMBERS}' "  # nosec B608
                f"AND EXISTS (SELECT 1 FROM {TABLE} g WHERE g.id = m.group_id) "
                f"AND EXISTS (SELECT 1 FROM application_components a WHERE a.id = m.application_id) "
                f"AND NOT EXISTS (SELECT 1 FROM {MEMBERS} x WHERE x.group_id = m.group_id AND x.application_id = m.application_id)"
            )
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
    if gone:
        _ensure_record_table(bind)
        reason = "members belong to more than one organisation, or the group has no members"
        bind.execute(
            text(
                f"INSERT INTO {RECORD_TABLE} (source_table, source_id, row_data, reason) "  # nosec B608
                f"SELECT '{MEMBERS}', m.group_id, to_jsonb(m), :r FROM {MEMBERS} m "  # nosec B608
                "WHERE m.group_id IN (SELECT id FROM unified_duplicate_groups WHERE organization_id IS NULL)"
            ),
            {"r": reason},
        )
        bind.execute(
            text(
                f"INSERT INTO {RECORD_TABLE} (source_table, source_id, row_data, reason) "  # nosec B608
                f"SELECT '{TABLE}', g.id, to_jsonb(g), :r FROM unified_duplicate_groups g WHERE g.organization_id IS NULL"  # nosec B608
            ),
            {"r": reason},
        )
        bind.execute(text("DELETE FROM unified_duplicate_groups WHERE organization_id IS NULL"))
    print(f"duplicate group org fence: {gone} group(s) with no single organisation recorded in {RECORD_TABLE} and removed")

    op.alter_column(TABLE, "organization_id", existing_type=sa.Integer(), nullable=False)
    op.create_index(f"ix_{TABLE}_organization_id", TABLE, ["organization_id"], if_not_exists=True)
    existing_fk = _fk_name(bind, TABLE, "organization_id")
    if existing_fk:
        op.drop_constraint(existing_fk, TABLE, type_="foreignkey")
    op.create_foreign_key(f"fk_{TABLE}_organization_id", TABLE, "organizations", ["organization_id"], ["id"], ondelete="CASCADE")

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
        bind.execute(text(f"ALTER TABLE public.{TABLE} ENABLE ROW LEVEL SECURITY"))  # nosec B608 - literal table names
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
    existing_fk = _fk_name(bind, TABLE, "organization_id")
    if existing_fk:
        op.drop_constraint(existing_fk, TABLE, type_="foreignkey")
    op.drop_index(f"ix_{TABLE}_organization_id", table_name=TABLE, if_exists=True)
    op.drop_column(TABLE, "organization_id")
    _restore_groups(bind)
