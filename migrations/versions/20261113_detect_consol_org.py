"""Give duplicate detection runs and consolidation entries an organisation.

unified_detection_runs and consolidation_list_entries were global: every
organisation read every other's detection runs and consolidation list entries. Each now carries ``organization_id`` (TenantMixin),
with a foreign key to organizations, an index, and row-level security as the
other tenant tables have it.

Backfill, in this order:
  * a detection run takes the organisation of the applications it analysed
    (through unified_group_members -> application_components) when those
    applications all belong to one organisation;
  * a run whose analysed applications span more than one organisation is split:
    the original run stays with the first organisation and one copy of the run is
    made for each other organisation; each group moves to the run of the
    organisation its members belong to. A group whose members span several
    organisations, or that has none, is detached from the run and handled by the
    duplicate-group migration that follows;
  * a run with no analysed applications falls back to its triggering user's
    home organisation (user_id -> users.organization_id);
  * a consolidation entry takes the organisation of its application, else of
    its target application, else of the run that produced its source group, else
    of the user who added it.
Only a row that still has no single organisation is removed, and before it is
removed its id and every field are written to detection_fence_removed_rows with
the reason; the downgrade puts those rows back.

Idempotent: every step checks before it acts, so a second run is a no-op.

Revision ID: 20261113_detect_consol_org
Revises: 20261114_subscription_offer
Create Date: 2026-11-13
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261113_detect_consol_org"
down_revision = "20261114_subscription_offer"
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


def _scalar(bind, sql):
    return bind.execute(text(sql)).scalar()  # nosec B608 - literal statements in this module


RECORD_TABLE = "detection_fence_removed_rows"


def _ensure_record_table(bind):
    bind.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {RECORD_TABLE} ("  # nosec B608 - literal table name
            "id SERIAL PRIMARY KEY, source_table VARCHAR(100) NOT NULL, source_id INTEGER, "
            "row_data JSONB NOT NULL, reason TEXT NOT NULL, removed_at TIMESTAMP NOT NULL DEFAULT now())"
        )
    )


def _record_and_delete(bind, table, where, reason):
    """Write each row matching `where` to the record table, then delete it.
    Returns the number of rows removed."""
    _ensure_record_table(bind)
    bind.execute(
        text(
            f"INSERT INTO {RECORD_TABLE} (source_table, source_id, row_data, reason) "  # nosec B608 - literal module strings
            f"SELECT :t, t.id, to_jsonb(t), :r FROM {table} t WHERE {where}"  # nosec B608
        ),
        {"t": table, "r": reason},
    )
    removed = _scalar(bind, f"SELECT COUNT(*) FROM {table} t WHERE {where}")  # nosec B608
    bind.execute(text(f"DELETE FROM {table} t WHERE {where}"))  # nosec B608
    return removed


def _restore_recorded(bind, table):
    """Put back rows recorded for `table` that are no longer present."""
    if not _present(bind, RECORD_TABLE) or not _present(bind, table):
        return 0
    restored = 0
    for rec_id, row_id in bind.execute(
        text(f"SELECT id, source_id FROM {RECORD_TABLE} WHERE source_table = :t ORDER BY id"), {"t": table}  # nosec B608
    ).all():
        exists = bind.execute(text(f"SELECT 1 FROM {table} WHERE id = :i"), {"i": row_id}).first()  # nosec B608
        if exists:
            continue
        cols = [
            r[0]
            for r in bind.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name = :t AND column_name <> 'organization_id' ORDER BY ordinal_position"
                ),
                {"t": table},
            ).all()
        ]
        names = ", ".join(cols)
        bind.execute(
            text(
                f"INSERT INTO {table} ({names}) SELECT {names} "  # nosec B608
                f"FROM jsonb_populate_record(NULL::{table}, "
                f"(SELECT row_data FROM {RECORD_TABLE} WHERE id = :rid))"
            ),
            {"rid": rec_id},
        )
        restored += 1
    return restored


def _split_cross_org_run(bind, run_id, org_ids):
    """Keep the run with the first organisation and copy it for each other one;
    each group follows the organisation of its members."""
    cols = [
        r[0]
        for r in bind.execute(
            text(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = 'unified_detection_runs' AND column_name NOT IN ('id', 'organization_id') "
                "ORDER BY ordinal_position"
            )
        ).all()
    ]
    names = ", ".join(cols)
    org_ids = sorted(org_ids)
    target = {org_ids[0]: run_id}
    bind.execute(
        text("UPDATE unified_detection_runs SET organization_id = :o WHERE id = :i"),
        {"o": org_ids[0], "i": run_id},
    )
    for org_id in org_ids[1:]:
        target[org_id] = bind.execute(
            text(
                f"INSERT INTO unified_detection_runs ({names}, organization_id) "  # nosec B608 - names come from information_schema
                f"SELECT {names}, :o FROM unified_detection_runs WHERE id = :i RETURNING id"
            ),
            {"o": org_id, "i": run_id},
        ).scalar()
    groups = bind.execute(
        text("SELECT id FROM unified_duplicate_groups WHERE detection_run_id = :i"), {"i": run_id}
    ).all()
    for (group_id,) in groups:
        member_orgs = [
            r[0]
            for r in bind.execute(
                text(
                    "SELECT DISTINCT ac.organization_id FROM unified_group_members ugm "
                    "JOIN application_components ac ON ac.id = ugm.application_id "
                    "WHERE ugm.group_id = :g AND ac.organization_id IS NOT NULL"
                ),
                {"g": group_id},
            ).all()
        ]
        new_run = target.get(member_orgs[0]) if len(member_orgs) == 1 else None
        bind.execute(
            text("UPDATE unified_duplicate_groups SET detection_run_id = :r WHERE id = :g"),
            {"r": new_run, "g": group_id},
        )


def _backfill_detection_runs(bind):
    """A run takes the org of the applications it analysed (through
    unified_group_members -> application_components) when those all belong to
    one organisation. A run whose analysed applications span more than one
    organisation is split into one run per organisation. A run with no
    analysed applications falls back to its triggering user's home
    organisation."""
    rows = bind.execute(
        text(
            "SELECT r.id, r.user_id FROM unified_detection_runs r "
            "WHERE r.organization_id IS NULL"
        )
    ).all()
    cross_org = 0
    for run_id, user_id in rows:
        # Derive from the applications the run analysed, through its groups.
        orgs = bind.execute(
            text(
                "SELECT DISTINCT ac.organization_id FROM unified_detection_runs r "
                "JOIN unified_duplicate_groups g ON g.detection_run_id = r.id "
                "JOIN unified_group_members ugm ON ugm.group_id = g.id "
                "JOIN application_components ac ON ac.id = ugm.application_id "
                "WHERE r.id = :rid AND ac.organization_id IS NOT NULL"
            ),
            {"rid": run_id},
        ).fetchall()
        distinct_orgs = [row[0] for row in orgs if row[0] is not None]
        if len(distinct_orgs) == 1:
            org_id = distinct_orgs[0]
            bind.execute(
                text("UPDATE unified_detection_runs SET organization_id = :o WHERE id = :i"),
                {"o": org_id, "i": run_id},
            )
        elif len(distinct_orgs) > 1:
            _split_cross_org_run(bind, run_id, distinct_orgs)
            cross_org += 1
        else:
            # No analysed applications: fall back to the triggering user's org.
            org_id = None
            if user_id is not None:
                found = bind.execute(
                    text("SELECT organization_id FROM users WHERE id = :uid"),
                    {"uid": user_id},
                ).scalar()
                if found is not None:
                    org_id = found
            if org_id is not None:
                bind.execute(
                    text("UPDATE unified_detection_runs SET organization_id = :o WHERE id = :i"),
                    {"o": org_id, "i": run_id},
                )
    return cross_org


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
    # Next the target application, then the run that produced the source group,
    # then the user who added the entry.
    bind.execute(
        text(
            "UPDATE consolidation_list_entries e SET organization_id = a.organization_id "
            "FROM application_components a "
            "WHERE e.organization_id IS NULL AND a.id = e.target_application_id "
            "AND a.organization_id IS NOT NULL"
        )
    )
    if _has_column(bind, "unified_duplicate_groups", "organization_id"):
        bind.execute(
            text(
                "UPDATE consolidation_list_entries e SET organization_id = g.organization_id "
                "FROM unified_duplicate_groups g "
                "WHERE e.organization_id IS NULL AND g.id = e.source_group_id "
                "AND g.organization_id IS NOT NULL"
            )
        )
    bind.execute(
        text(
            "UPDATE consolidation_list_entries e SET organization_id = r.organization_id "
            "FROM unified_duplicate_groups g JOIN unified_detection_runs r ON r.id = g.detection_run_id "
            "WHERE e.organization_id IS NULL AND g.id = e.source_group_id "
            "AND r.organization_id IS NOT NULL"
        )
    )
    bind.execute(
        text(
            "UPDATE consolidation_list_entries e SET organization_id = u.organization_id "
            "FROM users u "
            "WHERE e.organization_id IS NULL AND e.added_by IS NOT NULL "
            "AND lower(u.email) = lower(e.added_by) "
            "AND u.organization_id IS NOT NULL"
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
    bind.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))  # nosec B608 - literal table names
    bind.execute(text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))  # nosec B608 - literal table names
    print(f"row-level security: fenced {table}")


def upgrade():
    bind = op.get_bind()
    present = [t for t in TABLES if _present(bind, t)]
    for table in present:
        _add_column(bind, table)

    if "unified_detection_runs" in present and _present(bind, "users"):
        cross_org = _backfill_detection_runs(bind)
        print(f"detection org fence: {cross_org} run(s) link applications of more than one organisation; deleted below")

    if "consolidation_list_entries" in present and _present(bind, "application_components"):
        _backfill_consolidation_entries(bind)
        ambiguous = _ambiguous_consolidation_entries(bind) if _has_column(bind, "application_components", "organization_id") else 0
        print(f"consolidation org fence: {ambiguous} entry(ies) with no derivable organisation")

    # A row nothing links to any organisation cannot be shown to anyone safely.
    # It is written to the record table first, and the downgrade restores it.
    if "unified_detection_runs" in present:
        if _present(bind, "unified_duplicate_groups"):
            # Groups keep living: they are handled by the duplicate-group migration.
            bind.execute(
                text(
                    "UPDATE unified_duplicate_groups SET detection_run_id = NULL WHERE detection_run_id IN "
                    "(SELECT id FROM unified_detection_runs WHERE organization_id IS NULL)"
                )
            )
        gone = _record_and_delete(
            bind,
            "unified_detection_runs",
            "t.organization_id IS NULL",
            "no organisation could be derived from the analysed applications or the user who ran it",
        )
        print(f"detection org fence: {gone} run(s) with no organisation recorded in {RECORD_TABLE} and removed")
    if "consolidation_list_entries" in present:
        gone = _record_and_delete(
            bind,
            "consolidation_list_entries",
            "t.organization_id IS NULL",
            "no organisation could be derived from the application, target application, source group or adding user",
        )
        print(f"consolidation org fence: {gone} entry(ies) with no organisation recorded in {RECORD_TABLE} and removed")

    for table in present:
        op.alter_column(table, "organization_id", existing_type=sa.Integer(), nullable=False)
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"], if_not_exists=True)
        existing_fk = _fk_name(bind, table, "organization_id")
        if existing_fk:
            op.drop_constraint(existing_fk, table, type_="foreignkey")
        op.create_foreign_key(f"fk_{table}_organization_id", table, "organizations", ["organization_id"], ["id"], ondelete="CASCADE")
        _fence(bind, table)


def downgrade():
    bind = op.get_bind()
    for table in TABLES:
        if not _present(bind, table) or not _has_column(bind, table, "organization_id"):
            continue
        bind.execute(text(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY"))  # nosec B608 - literal table names
        for name in TENANT_POLICIES:
            bind.execute(text(f"DROP POLICY IF EXISTS {name} ON public.{table}"))  # nosec B608 - literal table names
        existing_fk = _fk_name(bind, table, "organization_id")
        if existing_fk:
            op.drop_constraint(existing_fk, table, type_="foreignkey")
        op.drop_index(f"ix_{table}_organization_id", table_name=table, if_exists=True)
        op.drop_column(table, "organization_id")
    for table in TABLES:
        if _present(bind, table):
            restored = _restore_recorded(bind, table)
            if restored:
                print(f"restored {restored} row(s) of {table} from {RECORD_TABLE}")
