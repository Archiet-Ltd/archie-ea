"""Give six typed element tables an organisation.

constraints, strategy_resources, application_services, application_interactions,
application_collaborations and deliverables carried no organisation column, so
the per-type pages served one organisation's rows to every other. They now carry
``organization_id`` (``TenantMixin``).

Backfill fails closed. A row takes the organisation of the ArchiMate element it
is linked to; failing that, of the application component or work package it
belongs to; for a constraint, of the user who created it. A row with none of
these has no derivable organisation: none is guessed, it stays NULL, matches no
organisation's filter, and the column is only tightened to NOT NULL on a table
where no NULL remains. The number of unattributed rows is printed.

Idempotent: every step checks before it acts, so a second run is a no-op.

Revision ID: 20261018_typed_tables_org_id
Revises: 20261018_vendor_analysis_rls
Create Date: 2026-10-18
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "20261018_typed_tables_org_id"
down_revision = "20261018_vendor_analysis_rls"
branch_labels = None
depends_on = None

TABLES = (
    "constraints",
    "strategy_resources",
    "application_services",
    "application_interactions",
    "application_collaborations",
    "deliverables",
)

# (table, source table, join column on `table`) tried in order; each source
# table is assumed to carry organization_id.
SOURCES = {
    "constraints": (("archimate_elements", "archimate_element_id"), ("users", "created_by_id")),
    "strategy_resources": (("archimate_elements", "archimate_element_id"),),
    "application_services": (
        ("archimate_elements", "archimate_element_id"),
        ("application_components", "application_component_id"),
    ),
    "application_interactions": (
        ("archimate_elements", "archimate_element_id"),
        ("application_components", "application_component_id"),
    ),
    "application_collaborations": (("archimate_elements", "archimate_element_id"),),
    "deliverables": (
        ("archimate_elements", "archimate_element_id"),
        ("unified_work_packages", "unified_work_package_id"),
        ("work_packages", "work_package_id"),
        ("application_components", "application_component_id"),
    ),
}


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
    for table in TABLES:
        if not _present(bind, table):
            continue
        if not _has_column(bind, table, "organization_id"):
            op.add_column(table, sa.Column("organization_id", sa.Integer(), nullable=True))

        for source, column in SOURCES[table]:
            if not (
                _present(bind, source)
                and _has_column(bind, source, "organization_id")
                and _has_column(bind, table, column)
            ):
                continue
            bind.execute(
                text(
                    f"UPDATE {table} AS t SET organization_id = s.organization_id "  # nosec B608 - literal names
                    f"FROM {source} AS s WHERE t.{column} = s.id "
                    "AND t.organization_id IS NULL AND s.organization_id IS NOT NULL"
                )
            )

        remaining = bind.execute(text(f"SELECT COUNT(*) FROM {table} WHERE organization_id IS NULL")).scalar()  # nosec B608 - literal names
        if remaining:
            print(f"typed table org fence: {remaining} row(s) in {table} have no derivable organisation; left NULL")
        else:
            op.alter_column(table, "organization_id", existing_type=sa.Integer(), nullable=False)
        op.create_index(f"ix_{table}_organization_id", table, ["organization_id"], if_not_exists=True)
        # Detect by column, not by name: on a database where ensure_baseline_schema
        # (migrations/versions/20260926_baseline.py) already built this table from
        # today's models -- which now carry TenantMixin for these six tables -- the
        # column arrives with its own, differently-named foreign key already in
        # place. Checking only for fk_{table}_organization_id missed that one and
        # created a second foreign key beside it on every one of the six tables.
        has_fk = bind.execute(
            text(
                "SELECT 1 FROM pg_constraint con "
                "JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey) "
                "WHERE con.contype = 'f' AND con.conrelid = to_regclass(:t) "
                "AND att.attname = 'organization_id'"
            ),
            {"t": f"public.{table}"},
        ).scalar()
        if not has_fk:
            op.create_foreign_key(
                f"fk_{table}_organization_id", table, "organizations", ["organization_id"], ["id"], ondelete="CASCADE"
            )


def downgrade():
    bind = op.get_bind()
    for table in TABLES:
        if not _present(bind, table) or not _has_column(bind, table, "organization_id"):
            continue
        fk = f"fk_{table}_organization_id"
        if bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": fk}).scalar():
            op.drop_constraint(fk, table, type_="foreignkey")
        op.drop_index(f"ix_{table}_organization_id", table_name=table, if_exists=True)
        op.drop_column(table, "organization_id")
