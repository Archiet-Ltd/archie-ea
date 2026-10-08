"""
Work package consolidation: backfill-work-package-org, merge-work-package-stores.

`unified_work_packages` (app/models/unified_work_package.py) predates
TenantMixin, so it has no organisation column at all -- every row is currently
unreadable through the tenant filter used by every other model.  Three other
stores answer the same "work package" question with no tenant column either:
`technology_roadmap_initiatives`, `roadmap_work_packages` and
`implementation_work_packages`.  A fourth, `work_packages`, already carries
TenantMixin and is left alone here except for gaining the retirement marker.

This module gives `unified_work_packages` an organisation the same way
`app/commands/backfill_principle_org.py` and the `EnterpriseInitiative`
override in app/models/vendor/vendor_organization.py already do it for their
own tables: reconcile-schema adds the column as plain nullable (ADR 0002),
and a row that cannot be attributed an owning organisation from anything it
links to is left NULL -- the tenant filter's `=` comparison then matches no
organisation, which is quarantine, not a guess.

Attribution rule: the organisation of the work package's linked
programme or element, else its creator, else quarantine. Run in this order:

    flask --app manage reconcile-schema
    flask --app manage backfill-work-package-org
    flask --app manage merge-work-package-stores
    flask --app manage backfill-work-package-org   # second pass: a merged
                                                    # row may resolve through
                                                    # a link its source table
                                                    # copied in that its own
                                                    # attribution chain did not
                                                    # try (e.g. an
                                                    # application_component_id
                                                    # carried over from
                                                    # implementation_work_packages).

Both commands are idempotent: `--dry-run` reports counts and changes nothing;
a normal run only ever touches rows whose organization_id (or, for the merge,
whose retired_into_id) is still NULL.
"""

import click
from flask.cli import with_appcontext
from sqlalchemy import text

from app import db

# (label, organization-owning parent table, join column on unified_work_packages)
_BACKFILL_STEPS = (
    ("enterprise_initiative_id -> enterprise_initiatives", "enterprise_initiatives", "enterprise_initiative_id"),
    ("archimate_element_id -> archimate_elements", "archimate_elements", "archimate_element_id"),
    ("application_component_id -> application_components", "application_components", "application_component_id"),
)


def _count(conn, sql, **params):
    return conn.execute(text(sql), params).scalar()


@click.command("backfill-work-package-org")
@click.option("--dry-run", is_flag=True, help="Report what would change; change nothing.")
@with_appcontext
def backfill_work_package_org(dry_run):
    """Attribute unified_work_packages.organization_id: linked programme or
    element, else creator, else quarantine (left NULL)."""
    conn = db.session.connection()

    if not dry_run and _ensure_business_capability_nullable(conn):
        click.echo("  + unified_work_packages.business_capability: dropped NOT NULL")

    before = _count(conn, "SELECT count(*) FROM unified_work_packages WHERE organization_id IS NULL")
    click.echo(f"unified_work_packages: {before} row(s) with no organisation")

    for label, parent_table, fk_column in _BACKFILL_STEPS:
        eligible = _count(
            conn,
            f'SELECT count(*) FROM unified_work_packages t '
            f'JOIN "{parent_table}" p ON p.id = t.{fk_column} '
            f'WHERE t.organization_id IS NULL AND p.organization_id IS NOT NULL',
        )
        if not eligible:
            continue
        if dry_run:
            click.echo(f"  - would attribute {eligible} row(s) via {label}")
            continue
        conn.execute(
            text(
                f'UPDATE unified_work_packages AS t SET organization_id = p.organization_id '
                f'FROM "{parent_table}" AS p '
                f'WHERE p.id = t.{fk_column} AND t.organization_id IS NULL '
                f'AND p.organization_id IS NOT NULL'
            )
        )
        click.echo(f"  + attributed {eligible} row(s) via {label}")

    eligible_creator = _count(
        conn,
        "SELECT count(*) FROM unified_work_packages t "
        "JOIN users u ON u.id = t.created_by "
        "WHERE t.organization_id IS NULL AND u.organization_id IS NOT NULL",
    )
    if eligible_creator:
        if dry_run:
            click.echo(f"  - would attribute {eligible_creator} row(s) via created_by -> users")
        else:
            conn.execute(
                text(
                    "UPDATE unified_work_packages AS t SET organization_id = u.organization_id "
                    "FROM users AS u "
                    "WHERE u.id = t.created_by AND t.organization_id IS NULL "
                    "AND u.organization_id IS NOT NULL"
                )
            )
            click.echo(f"  + attributed {eligible_creator} row(s) via created_by -> users")

    if dry_run:
        click.echo("dry-run: no changes committed.")
        db.session.rollback()
        return

    remaining = _count(conn, "SELECT count(*) FROM unified_work_packages WHERE organization_id IS NULL")
    db.session.commit()
    click.echo(f"backfill-work-package-org: done. {remaining} row(s) quarantined (unattributable).")


def _ensure_business_capability_nullable(conn):
    """Drop the historical NOT NULL: a row merged in from a store with no
    capability link legitimately has none, and fabricating one would violate
    the no-invented-data rule. reconcile-schema only ever ADDs columns, so
    this constraint change has to happen here."""
    row = conn.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'unified_work_packages' AND column_name = 'business_capability'"
        )
    ).first()
    if row and row[0] == "NO":
        conn.execute(
            text("ALTER TABLE unified_work_packages ALTER COLUMN business_capability DROP NOT NULL")
        )
        return True
    return False


# Each entry: source table, the columns copied straight across (same name on
# both sides), and how organization_id is derived for that source's own rows.
# `org_expr` / `org_joins` are raw SQL fragments deliberately -- this is a
# one-shot data migration, not a query the ORM's tenant filter ever runs.
_MERGE_SOURCES = (
    {
        "table": "work_packages",
        "columns": (
            "name", "description", "status", "priority", "togaf_phase",
            "owner_id", "estimated_cost", "actual_cost", "plateau_id",
            "capability_id", "element_type", "archimate_element_id",
            "application_component_id", "enterprise_initiative_id", "goal_id",
            "triggering_business_event_id", "created_at", "updated_at",
        ),
        "select_extra": (
            "s.percent_complete AS progress_percentage",
            "s.start_date::timestamp AS start_date",
            "s.target_date::timestamp AS end_date",
            "s.dependencies AS work_dependencies",
        ),
        "extra_columns": ("progress_percentage", "start_date", "end_date", "work_dependencies"),
        "org_joins": "",
        "org_expr": "s.organization_id",
    },
    {
        "table": "technology_roadmap_initiatives",
        "columns": ("name", "description", "status", "created_at", "updated_at"),
        "select_extra": ("s.investment_budget::float AS estimated_cost",),
        "extra_columns": ("estimated_cost",),
        "org_joins": (
            "LEFT JOIN solutions sol ON sol.id = s.solution_id "
            "LEFT JOIN architecture_models am ON am.id = s.architecture_id"
        ),
        "org_expr": "COALESCE(sol.organization_id, am.organization_id)",
    },
    {
        "table": "roadmap_work_packages",
        "columns": (
            "name", "description", "status", "business_capability", "assigned_to",
            "start_date", "end_date", "progress_percentage", "estimated_cost",
            "actual_cost", "priority", "risk_level", "created_at", "updated_at",
        ),
        "select_extra": (),
        "extra_columns": (),
        "org_joins": "LEFT JOIN users u ON u.id = s.created_by",
        "org_expr": "u.organization_id",
    },
    {
        "table": "implementation_work_packages",
        "columns": (
            "name", "description", "documentation", "element_type", "layer",
            "status", "priority", "risk_level", "risk_mitigation", "start_date",
            "end_date", "progress_percentage", "assigned_to", "estimated_cost",
            "actual_cost", "required_resources", "work_dependencies",
            "prerequisites", "application_component_id", "created_at", "updated_at",
        ),
        "select_extra": (),
        "extra_columns": (),
        "org_joins": (
            "LEFT JOIN architecture_models am ON am.id = s.architecture_id "
            "LEFT JOIN application_components ac ON ac.id = s.application_component_id"
        ),
        "org_expr": "COALESCE(am.organization_id, ac.organization_id)",
    },
)


def _merge_one(conn, spec, dry_run):
    table = spec["table"]
    eligible = _count(conn, f'SELECT count(*) FROM "{table}" WHERE retired_into_id IS NULL')
    if not eligible:
        click.echo(f"  {table}: nothing to merge")
        return
    if dry_run:
        click.echo(f"  - {table}: would merge {eligible} row(s) into unified_work_packages")
        return

    insert_columns = list(spec["columns"]) + list(spec["extra_columns"]) + [
        "context", "scope", "organization_id", "source_table", "source_id",
    ]
    select_columns = (
        [f"s.{c}" for c in spec["columns"]]
        + list(spec["select_extra"])
        # context/scope have only a Python-side ORM default (no
        # server_default), so a raw INSERT bypassing the ORM must supply
        # both explicitly or trip their NOT NULL constraint.
        + ["'architecture'", "'enterprise'", spec["org_expr"], f"'{table}'", "s.id"]
    )
    sql = (
        "WITH inserted AS ("
        f"INSERT INTO unified_work_packages ({', '.join(insert_columns)}) "
        f"SELECT {', '.join(select_columns)} "
        f'FROM "{table}" s {spec["org_joins"]} '
        "WHERE s.retired_into_id IS NULL "
        "AND NOT EXISTS ("
        "SELECT 1 FROM unified_work_packages uwp "
        "WHERE uwp.source_table = :source_table AND uwp.source_id = s.id"
        ") "
        "RETURNING id AS new_id, source_id AS old_id"
        ") "
        f'UPDATE "{table}" SET retired_into_id = inserted.new_id '
        f'FROM inserted WHERE "{table}".id = inserted.old_id'
    )
    conn.execute(text(sql), {"source_table": table})
    click.echo(f"  + {table}: merged {eligible} row(s), marked retired_into_id")


@click.command("merge-work-package-stores")
@click.option("--dry-run", is_flag=True, help="Report what would change; change nothing.")
@with_appcontext
def merge_work_package_stores(dry_run):
    """Merge technology_roadmap_initiatives, roadmap_work_packages,
    implementation_work_packages and work_packages into unified_work_packages,
    recording provenance (source_table/source_id) and marking each merged
    source row with retired_into_id. Never drops a source row or table."""
    conn = db.session.connection()

    if dry_run:
        made_nullable = False
    else:
        made_nullable = _ensure_business_capability_nullable(conn)
    if made_nullable:
        click.echo("  + unified_work_packages.business_capability: dropped NOT NULL")

    for spec in _MERGE_SOURCES:
        _merge_one(conn, spec, dry_run)

    if dry_run:
        click.echo("dry-run: no changes committed.")
        db.session.rollback()
        return

    db.session.commit()
    click.echo("merge-work-package-stores: done.")


def init_app(app):
    """Register the work package consolidation CLI commands."""
    app.cli.add_command(backfill_work_package_org)
    app.cli.add_command(merge_work_package_stores)
