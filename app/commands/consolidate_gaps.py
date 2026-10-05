"""
Gap register consolidation: merge-gap-stores.

The one gap register (`gaps`, app.models.implementation_migration.Gap) already
carries TenantMixin -- every row created through the register has an
organisation. Three other stores answer the same "gap" question with no
tenant column at all: `roadmap_gaps` (RoadmapGap), `implementation_gaps`
(ImplementationGap) and `compliance_gaps` (ComplianceGap). This module merges
all three into `gaps`, recording provenance (source_table/source_id) and
marking each merged source row with retired_into_id. It never drops a source
row or table (CLAUDE.md).

`capability_gap_analysis` and `capability_gap_details` are a different
concept -- maturity gaps, not planned architecture gaps -- and stay on
capability_heatmap_service.py under the capability-gap concept; they are
deliberately not merged here (excluded from this wave).
excluded from wave 1).

Attribution rule per source, in this order, else quarantine (organization_id
left NULL -- the tenant filter's `=` comparison then matches no organisation,
visible to no tenant, never a guess):

    roadmap_gaps           source_application_id -> application_components,
                            else source_capability_id -> unified_capabilities
                            (source_org_id; NULL for a shared/enterprise
                            capability, so this often falls through),
                            else created_by -> users
    implementation_gaps    architecture_id -> architecture_models
    compliance_gaps        assigned_to_id -> users,
                            else identified_by_id -> users

Run:

    flask --app manage merge-gap-stores [--dry-run]

Idempotent: only ever touches a source row whose retired_into_id is still
NULL, and only inserts a Gap when no existing row already carries that
(source_table, source_id) pair (checked with NOT EXISTS -- reconcile-schema
is ADD-COLUMN-nullable-only, ADR 0002, so this is a code-level uniqueness
check, not a database constraint).
"""

import click
from flask.cli import with_appcontext
from sqlalchemy import text

from app import db


def _count(conn, sql, **params):
    return conn.execute(text(sql), params).scalar()


# Every SQL string below is a single literal -- no f-string, no +, no % at
# runtime -- so bandit sees no string-built SQL.  The only runtime values are
# :source_table (bound parameter, used only in WHERE/NOT EXISTS comparisons
# against the varchar column gaps.source_table) and :source_table_val (used
# in the SELECT clause as a literal value, cast explicitly to avoid the
# "inconsistent types deduced for parameter" error from PostgreSQL).
_MERGE_SOURCES = (
    {
        "table": "roadmap_gaps",
        "count_sql": (
            'SELECT count(*) FROM "roadmap_gaps" WHERE retired_into_id IS NULL'
        ),
        "insert_sql": (
            "WITH inserted AS ("
            "INSERT INTO gaps (name, description, gap_type, severity, priority, "
            "resolution_status, current_state_ref, target_state_ref, created_at, "
            "updated_at, context, auto_generated, gap_kind, organization_id, "
            "source_table, source_id) "
            "SELECT "
            "LEFT(s.name, 255) AS name, "
            "COALESCE(s.description, '') "
            "|| CASE WHEN s.impact_assessment IS NOT NULL "
            "THEN E'\\n\\nImpact: ' || s.impact_assessment ELSE '' END "
            "AS description, "
            "LEFT(s.gap_type, 30) AS gap_type, "
            "s.risk_level AS severity, "
            "s.priority AS priority, "
            "CASE WHEN s.status = 'open' THEN 'identified' ELSE s.status END AS resolution_status, "
            "LEFT(s.current_state, 255) AS current_state_ref, "
            "LEFT(s.target_state, 255) AS target_state_ref, "
            "COALESCE(s.created_at, now()) AS created_at, "
            "COALESCE(s.updated_at, s.created_at, now()) AS updated_at, "
            "'architecture', false, 'capability_shortfall', "
            "COALESCE(ac.organization_id, uc.source_org_id, u.organization_id), "
            "CAST(:source_table_val AS varchar), s.id "
            'FROM "roadmap_gaps" s '
            "LEFT JOIN application_components ac ON ac.id = s.source_application_id "
            "LEFT JOIN unified_capabilities uc ON uc.id = s.source_capability_id "
            "LEFT JOIN users u ON u.id = s.created_by "
            "WHERE s.retired_into_id IS NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM gaps g WHERE g.source_table = :source_table AND g.source_id = s.id"
            ") "
            "RETURNING id AS new_id, source_id AS old_id"
            ") "
            'UPDATE "roadmap_gaps" SET retired_into_id = inserted.new_id '
            'FROM inserted WHERE "roadmap_gaps".id = inserted.old_id'
        ),
    },
    {
        "table": "implementation_gaps",
        "count_sql": (
            'SELECT count(*) FROM "implementation_gaps" WHERE retired_into_id IS NULL'
        ),
        "insert_sql": (
            "WITH inserted AS ("
            "INSERT INTO gaps (name, description, gap_type, severity, priority, "
            "resolution_status, current_state_ref, target_state_ref, created_at, "
            "updated_at, context, auto_generated, gap_kind, organization_id, "
            "source_table, source_id) "
            "SELECT "
            "LEFT(s.name, 255) AS name, "
            "COALESCE(s.gap_description, s.description, '') "
            "|| CASE WHEN s.impact_description IS NOT NULL "
            "THEN E'\\n\\nImpact: ' || s.impact_description ELSE '' END "
            "|| CASE WHEN s.business_impact IS NOT NULL "
            "THEN E'\\n\\nBusiness impact: ' || s.business_impact ELSE '' END "
            "AS description, "
            "LEFT(s.gap_type, 30) AS gap_type, "
            "s.impact_level AS severity, "
            "s.priority AS priority, "
            "s.status AS resolution_status, "
            "LEFT(s.baseline_state, 255) AS current_state_ref, "
            "LEFT(s.target_state, 255) AS target_state_ref, "
            "COALESCE(s.created_at, now()) AS created_at, "
            "COALESCE(s.updated_at, s.created_at, now()) AS updated_at, "
            "'architecture', false, 'capability_shortfall', "
            "am.organization_id, "
            "CAST(:source_table_val AS varchar), s.id "
            'FROM "implementation_gaps" s '
            "LEFT JOIN architecture_models am ON am.id = s.architecture_id "
            "WHERE s.retired_into_id IS NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM gaps g WHERE g.source_table = :source_table AND g.source_id = s.id"
            ") "
            "RETURNING id AS new_id, source_id AS old_id"
            ") "
            'UPDATE "implementation_gaps" SET retired_into_id = inserted.new_id '
            'FROM inserted WHERE "implementation_gaps".id = inserted.old_id'
        ),
    },
    {
        "table": "compliance_gaps",
        "count_sql": (
            'SELECT count(*) FROM "compliance_gaps" WHERE retired_into_id IS NULL'
        ),
        "insert_sql": (
            "WITH inserted AS ("
            "INSERT INTO gaps (name, description, gap_type, severity, priority, "
            "resolution_status, current_state_ref, target_state_ref, created_at, "
            "updated_at, context, auto_generated, gap_kind, organization_id, "
            "source_table, source_id) "
            "SELECT "
            "LEFT(s.title, 255) AS name, "
            "s.description AS description, "
            "LEFT(s.gap_type, 30) AS gap_type, "
            "s.risk_level AS severity, "
            "s.risk_level AS priority, "
            "CASE WHEN s.status = 'open' THEN 'identified' ELSE s.status END AS resolution_status, "
            "NULL AS current_state_ref, "
            "NULL AS target_state_ref, "
            "COALESCE(s.identified_at, now()) AS created_at, "
            "COALESCE(s.resolved_at, s.identified_at, now()) AS updated_at, "
            "'architecture', false, 'capability_shortfall', "
            "COALESCE(ua.organization_id, ui.organization_id), "
            "CAST(:source_table_val AS varchar), s.id "
            'FROM "compliance_gaps" s '
            "LEFT JOIN users ua ON ua.id = s.assigned_to_id "
            "LEFT JOIN users ui ON ui.id = s.identified_by_id "
            "WHERE s.retired_into_id IS NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM gaps g WHERE g.source_table = :source_table AND g.source_id = s.id"
            ") "
            "RETURNING id AS new_id, source_id AS old_id"
            ") "
            'UPDATE "compliance_gaps" SET retired_into_id = inserted.new_id '
            'FROM inserted WHERE "compliance_gaps".id = inserted.old_id'
        ),
    },
)


def _merge_one(conn, spec, dry_run):
    table = spec["table"]
    eligible = _count(conn, spec["count_sql"])
    if not eligible:
        click.echo(f"  {table}: nothing to merge")
        return
    if dry_run:
        click.echo(f"  - {table}: would merge {eligible} row(s) into gaps")
        return

    conn.execute(
        text(spec["insert_sql"]),
        {"source_table": table, "source_table_val": table},
    )
    click.echo(f"  + {table}: merged {eligible} row(s), marked retired_into_id")


@click.command("merge-gap-stores")
@click.option("--dry-run", is_flag=True, help="Report what would change; change nothing.")
@with_appcontext
def merge_gap_stores(dry_run):
    """Merge roadmap_gaps, implementation_gaps and compliance_gaps into gaps,
    recording provenance (source_table/source_id) and marking each merged
    source row with retired_into_id. Never drops a source row or table."""
    conn = db.session.connection()

    before = _count(conn, "SELECT count(*) FROM gaps")
    click.echo(f"gaps: {before} row(s) before merge")

    for spec in _MERGE_SOURCES:
        _merge_one(conn, spec, dry_run)

    if dry_run:
        click.echo("dry-run: no changes committed.")
        db.session.rollback()
        return

    after = _count(conn, "SELECT count(*) FROM gaps")
    quarantined = _count(conn, "SELECT count(*) FROM gaps WHERE organization_id IS NULL")
    db.session.commit()
    click.echo(
        f"merge-gap-stores: done. gaps: {before} -> {after} row(s), "
        f"{quarantined} quarantined (unattributable)."
    )


def init_app(app):
    """Register the gap consolidation CLI command."""
    app.cli.add_command(merge_gap_stores)