"""Give six typed element tables an organisation.

constraints, strategy_resources, application_services, application_interactions,
application_collaborations and deliverables carried no organisation column, so
the per-type pages served one organisation's rows to every other. They now carry
``organization_id`` (``TenantMixin``).

Backfill fails closed. A row takes the organisation of the ArchiMate element,
architecture model or application component it is linked to, in that order,
falling further back to the work package for a deliverable or the goal element
for a constraint. A row with none of these has no derivable organisation: none
is guessed, it stays NULL, matches no organisation's filter, and the column is
only tightened to NOT NULL on a table where no NULL remains. The number of
unattributed rows is printed.

The deliverable parent-source list overlaps ``_DELIVERABLE_ORG_SOURCES`` in
``app/models/implementation_migration.py`` (a ``before_insert`` listener for
new rows rather than this migration's one-time backfill of existing ones), but
the two have drifted and are not identical: this migration tries five parents
in archimate-element-first order (``archimate_element_id``,
``unified_work_package_id``, ``work_package_id``, ``application_component_id``,
``architecture_id``); the listener tries four, work-package-first
(``unified_work_package_id``, ``work_package_id``, ``archimate_element_id``,
``application_component_id``), and has no ``architecture_id`` branch at all. A
deliverable with two of these parents set to different organisations can in
principle be backfilled to one organisation by this migration and assigned to
a different one by the listener on a later insert of a similar row -- narrow,
not fixed here since changing the listener's live insert-time behaviour is a
separate decision from an urgent-security migration. Not imported: this file
follows the same
copied-not-imported convention as ``20261018_vendor_analysis_rls`` and
``20261008_row_level_security`` before it, so a migration stays runnable
unchanged even if the model's event-listener logic moves later. This backfill
also does not go through ``flask backfill-layer-tenancy``
(``app/commands/backfill_layer_tenancy.py``), the codebase's general mechanism
for exactly this problem shape (TenantMixin added to an existing populated
table) -- these six tables are not registered in that command's
``_DERIVABLE_ORG``/``_PROVENANCE_ONLY``, the same way ``20261018_vendor_analysis_rls``
took five tables out of that command's coverage, because this migration also
tightens the column to NOT NULL and adds its index in the same pass, which
that command's own docstring says is beyond what it does (ADR-0002: boot-time
reconcile-schema can only add nullable columns).

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
    # constraints has no created_by_id column (checked against app/models/models.py's
    # ConstraintElement) -- architecture_id and goal_id are its two real FK parents
    # beyond archimate_element_id.
    "constraints": (
        ("archimate_elements", "archimate_element_id"),
        ("architecture_models", "architecture_id"),
        ("archimate_elements", "goal_id"),
    ),
    "strategy_resources": (("archimate_elements", "archimate_element_id"),),
    "application_services": (
        ("archimate_elements", "archimate_element_id"),
        ("application_components", "application_component_id"),
    ),
    # source_component_id/target_component_id were missing here, same shape as
    # the application_collaborations fix above: a row linked only through one
    # of those two (no archimate_element_id or application_component_id) was
    # not backfilled.
    "application_interactions": (
        ("archimate_elements", "archimate_element_id"),
        ("application_components", "application_component_id"),
        ("application_components", "source_component_id"),
        ("application_components", "target_component_id"),
    ),
    # application_component_id was missing here: a row linked only through it
    # (archimate_element_id NULL) backfilled to nothing and stayed invisible
    # under RLS, including to the cascade delete that removes a parent
    # application's collaborations.
    "application_collaborations": (
        ("archimate_elements", "archimate_element_id"),
        ("application_components", "application_component_id"),
    ),
    "deliverables": (
        ("archimate_elements", "archimate_element_id"),
        ("unified_work_packages", "unified_work_package_id"),
        ("work_packages", "work_package_id"),
        ("application_components", "application_component_id"),
        ("architecture_models", "architecture_id"),
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
        if _fk_name(bind, table, "organization_id") is None:
            op.create_foreign_key(
                f"fk_{table}_organization_id", table, "organizations", ["organization_id"], ["id"], ondelete="CASCADE"
            )


def downgrade():
    bind = op.get_bind()
    for table in TABLES:
        if not _present(bind, table) or not _has_column(bind, table, "organization_id"):
            continue
        # Drop whatever foreign key actually exists on the column, not just the
        # name this migration itself would have created (same reasoning as
        # upgrade()'s _fk_name check) -- an auto-named one from ensure_baseline_schema
        # would otherwise survive a downgrade silently.
        fk = _fk_name(bind, table, "organization_id")
        if fk:
            op.drop_constraint(fk, table, type_="foreignkey")
        op.drop_index(f"ix_{table}_organization_id", table_name=table, if_exists=True)
        op.drop_column(table, "organization_id")
