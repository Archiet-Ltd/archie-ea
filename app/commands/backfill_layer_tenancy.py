"""flask backfill-layer-tenancy — give every TenantMixin table a hardened tenant column.

ADR-0002 records the structural bind this command resolves: `reconcile-schema`
can only ever ADD nullable columns, while `TenantMixin.organization_id` is
declared NOT NULL. So when a model gains the mixin, an existing database is left
with a nullable, unindexed column that the model says cannot be null — and, per
ADR-0003, the tenant filter compares with `=`, so any row left at NULL silently
vanishes from every organisation's view rather than failing loudly.

`backfill-value-stream-tenancy` solved this for three tables. The ADR-0003
completion put `TenantMixin` on ~28 more models across the business, data,
technology and physical layers, and hard-coding another table list per adoption
wave does not scale. This command derives its worklist from the mapper registry
instead: every mapped `TenantMixin` model's table is a candidate, and a table is
touched only when something is actually wrong with it —

  * the column is missing entirely (model gained the mixin before any
    reconcile-schema ran here), or
  * rows hold NULL (pre-mixin rows never assigned to a tenant), or
  * the column is still nullable or unindexed (reconcile-schema adds plain
    nullable columns with no index).

Healthy tables are read and skipped, so the command is safe to run on every
boot, which is exactly where docker-compose runs it.

The organisation-column-everywhere brief reused this mechanism rather than
adding a second one: its own text names a new
`app/commands/backfill_organisation_columns.py`, but this command already
provides column-discovery, per-row derivation and
orphan/hardening handling for exactly the same problem shape (TenantMixin
gained on an existing, populated table). Motivation, requirements, strategic
and technology-layer tables newly gaining TenantMixin for that brief are
registered in `_DERIVABLE_ORG`/`_PROVENANCE_ONLY` below instead of behind a
duplicate command; the brief's shared-catalogue tables (framework.py,
framework_configuration.py, reference_models.py, industry_apqc.py) use the new
`HybridTenantMixin` instead, which this command's `_tenant_tables()` does not
discover (nullable is their correct, permanent state, not an expand step).

This is the one place in the codebase allowed to write organization_id on an
existing row; a static check fails the build on a second one. The policy per
table, in order:

  1. Purge. A row whose link to its parent is always set at creation, and
     names a parent that no longer exists, is not unresolved provenance -- it
     is a leftover with nowhere to belong, and is deleted before anything
     else runs for that table.
  2. Derive. Where a row can state its own tenant through a link it already
     carries (a joined capability, a work package's creator, an initiative),
     that link fills organization_id first. A derivation statement can only
     fill a NULL; it can never move a row between tenants.
  3. Assign or defer. What is still NULL after derivation is the true orphan
     count. On a database with exactly one active, non-default organisation,
     those rows go to it. On a database with more than one -- or with none --
     no row is assigned, with or without --org-id: an unresolved row is
     another tenant's data, or nobody's yet, never a guess this command is
     allowed to make. Every such table is reported, not silently skipped.
  4. Index and harden. The index is always added. The NOT NULL constraint is
     added only when nothing was left NULL; a deferred table gets the index
     and is reported, not the constraint, which would only fail. A table
     whose model declares organization_id nullable=True as a permanent
     override of TenantMixin's default (UnifiedWorkPackage, ConnectorConfig
     and the rest of that generation -- an unattributable row is their
     deliberate, permanent quarantine) never gets the constraint either,
     however many rows happen to be resolved; it is reported as nullable by
     design, not as a deferred backlog.

Each table's purge, derivation, assignment, index and hardening run in their
own transaction, committed before the next table starts (rolled back, always,
under --dry-run). A table whose statements raise is rolled back on its own,
recorded, and the run continues with every other table.

Exit codes: 0 whenever every statement issued here succeeded, however many
rows were left NULL -- an unresolved row is reported, not a failure. Non-zero
only for a real failure: a SQL error while repairing a table, a mapped table
the database still lacks the column for after the add, or an --org-id that
does not resolve to the one organisation eligible for it.

    flask --app manage backfill-layer-tenancy --dry-run
    flask --app manage backfill-layer-tenancy
    flask --app manage backfill-layer-tenancy --org-id 7
"""

import click
from flask.cli import with_appcontext

from app import db


# Tables whose tenant can be READ from a row they already reference rather than
# guessed. Each statement fills organization_id only where it is NULL, so it is
# idempotent and can never move a row between tenants.
#
# vendor_product_capabilities records how well a vendor product covers a business
# capability. The capability is tenant-owned, so the assessment belongs to that
# capability's organisation — every production row resolves this way, which is
# strictly better than the refuse-to-guess fallback (they would otherwise all be
# assigned to one operator-chosen org).
# tenancy-ok: this backfill is what gives the column its values; it derives the
# tenant from the joined row rather than assuming one.
#
# Ordering is load-bearing: _tenant_tables() sorts alphabetically, and
# "application_ownership" < "organization_units", so application_ownership's
# organization_id is always derived (or left NULL) before organization_units'
# derivation reads it. If that alphabetical relationship ever changes, the
# organization_units entry below must still run after application_ownership's.
#
# A row's creator/rated-by/generated-by user's own organization_id is only a
# safe fallback when that user belongs to exactly their home organisation --
# a user who is also a member of a second one (an org_roles row pointing
# elsewhere) may have created the row while actively working in that other
# organisation, which this cannot see. Guessing the home org then hands one
# tenant's data to another, the exact failure the refuse-to-guess rule exists
# to prevent (refuter finding H4 on PR 317). Embedded in every such subquery
# below rather than filtered at the UPDATE's WHERE clause, so a user who
# fails the check is excluded from COALESCE and the row falls through to the
# next fallback (or stays an unresolved orphan) instead of being skipped
# outright.
# The "AND NOT EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = <alias>.id
# AND r.organization_id != <alias>.organization_id)" clause below, inlined
# per entry rather than built from one shared helper: bandit's B608 flags any
# f-string-constructed SQL-shaped text regardless of whether the
# interpolated value is attacker-reachable (here it never is -- u/u2 are
# this file's own fixed aliases), and the project's own convention is to
# avoid the pattern entirely rather than carry a baseline exception for it.
_DERIVABLE_ORG = {
    "vendor_product_capabilities": """
        UPDATE vendor_product_capabilities v
           SET organization_id = b.organization_id
          FROM business_capability b
         WHERE v.business_capability_id = b.id
           AND v.organization_id IS NULL
           AND b.organization_id IS NOT NULL
    """,
    # An ownership row's tenant is its application's tenant — every production
    # row resolves this way (nothing in app/ writes this table independently
    # of a component). A row whose application itself has no organization_id
    # is per-row provenance this statement cannot resolve; see
    # _PROVENANCE_ONLY below for what happens to it.
    "application_ownership": """
        UPDATE application_ownership o
           SET organization_id = c.organization_id
          FROM application_components c
         WHERE o.application_id = c.id
           AND o.organization_id IS NULL
           AND c.organization_id IS NOT NULL
    """,
    # A unit's tenant is derived from its own ownership rows, never guessed: a
    # unit referenced by exactly one organisation's ownership rows takes that
    # organisation; a unit referenced by more than one, or by none at all,
    # stays NULL here. It is excluded from the residual sweep below
    # (_PROVENANCE_ONLY), so with several organisations in the database it
    # stays NULL and is reported, never assigned, with or without --org-id;
    # with exactly one organisation the ordinary single-organisation rule
    # still applies, same as every other table.
    "organization_units": """
        UPDATE organization_units u
           SET organization_id = s.org_id
          FROM (
                SELECT organization_unit_id, MIN(organization_id) AS org_id
                  FROM application_ownership
                 WHERE organization_id IS NOT NULL
                 GROUP BY organization_unit_id
                HAVING COUNT(DISTINCT organization_id) = 1
               ) s
         WHERE u.id = s.organization_unit_id
           AND u.organization_id IS NULL
    """,
    # A learned correction belongs to the organisation of the user who made it. user_id has no
    # foreign key and can be NULL or name a user with no organisation; such a row stays NULL
    # (hidden from every organisation, reported), never guessed. See _PROVENANCE_ONLY.
    "extraction_feedback": """
        UPDATE extraction_feedback f
           SET organization_id = u.organization_id
          FROM users u
         WHERE f.user_id = u.id
           AND f.organization_id IS NULL
           AND u.organization_id IS NOT NULL
    """,
    # An options analysis belongs to the organisation that owns the capability it
    # analyses: capability_id is NOT NULL and points at business_capability, which
    # is already tenant-fenced. An analysis whose capability itself has no
    # organisation cannot be resolved here; see _PROVENANCE_ONLY.
    "options_analysis": """
        UPDATE options_analysis a
           SET organization_id = b.organization_id
          FROM business_capability b
         WHERE a.capability_id = b.id
           AND a.organization_id IS NULL
           AND b.organization_id IS NOT NULL
    """,
    # A stakeholder input belongs to its analysis. Ordering is load-bearing in the
    # same way as above: "options_analysis" < "stakeholder_inputs", so the analysis
    # is derived (or left NULL) before this reads it.
    "stakeholder_inputs": """
        UPDATE stakeholder_inputs i
           SET organization_id = a.organization_id
          FROM options_analysis a
         WHERE i.analysis_id = a.id
           AND i.organization_id IS NULL
           AND a.organization_id IS NOT NULL
    """,

    # --- Motivation, requirements, strategic and technology
    # layer tables. Every entry below derives from a table that is already
    # tenant-fenced (archimate_elements, business_capability,
    # strategic_initiatives, solutions, solution_analysis_sessions,
    # application_components) or from users -- none of them depend on another
    # entry in THIS dict resolving first, so the alphabetical-ordering
    # constraint documented above is not in play for this group, except
    # "drivers" < "goals" for goals' own driver_id fallback below.
    "archimate_resources": """
        UPDATE archimate_resources r
           SET organization_id = e.organization_id
          FROM archimate_elements e
         WHERE r.archimate_element_id = e.id
           AND r.organization_id IS NULL
           AND e.organization_id IS NOT NULL
    """,
    "assessments": """
        UPDATE assessments a
           SET organization_id = e.organization_id
          FROM archimate_elements e
         WHERE a.archimate_element_id = e.id
           AND a.organization_id IS NULL
           AND e.organization_id IS NOT NULL
    """,
    "capability_health_overrides": """
        UPDATE capability_health_overrides c
           SET organization_id = b.organization_id
          FROM business_capability b
         WHERE c.capability_id = b.id
           AND c.organization_id IS NULL
           AND b.organization_id IS NOT NULL
    """,
    "drivers": """
        UPDATE drivers d
           SET organization_id = COALESCE(
                 (SELECT e.organization_id FROM archimate_elements e WHERE e.id = d.archimate_element_id),
                 (SELECT u.organization_id FROM users u WHERE u.id = d.created_by_id
                    AND NOT EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = u.id AND r.organization_id != u.organization_id))
               )
         WHERE d.organization_id IS NULL
           AND COALESCE(
                 (SELECT e.organization_id FROM archimate_elements e WHERE e.id = d.archimate_element_id),
                 (SELECT u.organization_id FROM users u WHERE u.id = d.created_by_id
                    AND NOT EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = u.id AND r.organization_id != u.organization_id))
               ) IS NOT NULL
    """,
    # A briefing's generated_by_id is a plain integer, not an FK constraint
    # (see app/models/strategic.py), but it is a user id in every writer of
    # this table -- resolved the same way, just without a declared FK to lean on.
    "enterprise_briefings": """
        UPDATE enterprise_briefings eb
           SET organization_id = u.organization_id
          FROM users u
         WHERE eb.generated_by_id = u.id
           AND eb.organization_id IS NULL
           AND u.organization_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = u.id AND r.organization_id != u.organization_id)
    """,
    # "drivers" < "goals": the driver_id fallback below reads drivers'
    # organization_id after this dict has already derived it, not before.
    "goals": """
        UPDATE goals g
           SET organization_id = COALESCE(
                 (SELECT e.organization_id FROM archimate_elements e WHERE e.id = g.archimate_element_id),
                 (SELECT d.organization_id FROM drivers d WHERE d.id = g.driver_id),
                 (SELECT u.organization_id FROM users u WHERE u.id = g.created_by_id
                    AND NOT EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = u.id AND r.organization_id != u.organization_id))
               )
         WHERE g.organization_id IS NULL
           AND COALESCE(
                 (SELECT e.organization_id FROM archimate_elements e WHERE e.id = g.archimate_element_id),
                 (SELECT d.organization_id FROM drivers d WHERE d.id = g.driver_id),
                 (SELECT u.organization_id FROM users u WHERE u.id = g.created_by_id
                    AND NOT EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = u.id AND r.organization_id != u.organization_id))
               ) IS NOT NULL
    """,
    "meanings": """
        UPDATE meanings m
           SET organization_id = e.organization_id
          FROM archimate_elements e
         WHERE m.archimate_element_id = e.id
           AND m.organization_id IS NULL
           AND e.organization_id IS NOT NULL
    """,
    "motivation_bridge_links": """
        UPDATE motivation_bridge_links k
           SET organization_id = s.organization_id
          FROM solutions s
         WHERE k.solution_id = s.id
           AND k.organization_id IS NULL
           AND s.organization_id IS NOT NULL
    """,
    "programme_snapshots": """
        UPDATE programme_snapshots p
           SET organization_id = i.organization_id
          FROM strategic_initiatives i
         WHERE p.initiative_id = i.id
           AND p.organization_id IS NULL
           AND i.organization_id IS NOT NULL
    """,
    # Requirement's four ArchiMate-element-pointing columns (Basecoat pattern)
    # all resolve through the same table; the first one set on a given row is
    # tried, since a row rarely has more than one populated. Falling further
    # back to application_component_id covers rows with none of the four.
    "requirements": """
        UPDATE requirements r
           SET organization_id = COALESCE(
                 (SELECT e.organization_id FROM archimate_elements e
                   WHERE e.id = COALESCE(r.archimate_element_id, r.stakeholder_id, r.driver_id, r.goal_id)),
                 (SELECT c.organization_id FROM application_components c WHERE c.id = r.application_component_id)
               )
         WHERE r.organization_id IS NULL
           AND COALESCE(
                 (SELECT e.organization_id FROM archimate_elements e
                   WHERE e.id = COALESCE(r.archimate_element_id, r.stakeholder_id, r.driver_id, r.goal_id)),
                 (SELECT c.organization_id FROM application_components c WHERE c.id = r.application_component_id)
               ) IS NOT NULL
    """,
    "solution_adr_links": """
        UPDATE solution_adr_links l
           SET organization_id = s.organization_id
          FROM solution_analysis_sessions s
         WHERE l.session_id = s.id
           AND l.organization_id IS NULL
           AND s.organization_id IS NOT NULL
    """,
    "solution_migration_roadmaps": """
        UPDATE solution_migration_roadmaps m
           SET organization_id = COALESCE(
                 (SELECT s.organization_id FROM solutions s WHERE s.id = m.solution_id),
                 (SELECT u.organization_id FROM users u WHERE u.id = m.generated_by_id
                    AND NOT EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = u.id AND r.organization_id != u.organization_id))
               )
         WHERE m.organization_id IS NULL
           AND COALESCE(
                 (SELECT s.organization_id FROM solutions s WHERE s.id = m.solution_id),
                 (SELECT u.organization_id FROM users u WHERE u.id = m.generated_by_id
                    AND NOT EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = u.id AND r.organization_id != u.organization_id))
               ) IS NOT NULL
    """,
    "stakeholders": """
        UPDATE stakeholders h
           SET organization_id = COALESCE(
                 (SELECT e.organization_id FROM archimate_elements e WHERE e.id = h.archimate_element_id),
                 (SELECT u.organization_id FROM users u WHERE u.id = h.created_by_id
                    AND NOT EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = u.id AND r.organization_id != u.organization_id))
               )
         WHERE h.organization_id IS NULL
           AND COALESCE(
                 (SELECT e.organization_id FROM archimate_elements e WHERE e.id = h.archimate_element_id),
                 (SELECT u.organization_id FROM users u WHERE u.id = h.created_by_id
                    AND NOT EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = u.id AND r.organization_id != u.organization_id))
               ) IS NOT NULL
    """,
    "strategic_milestones": """
        UPDATE strategic_milestones m
           SET organization_id = i.organization_id
          FROM strategic_initiatives i
         WHERE m.initiative_id = i.id
           AND m.organization_id IS NULL
           AND i.organization_id IS NOT NULL
    """,
    "strategic_recommendations": """
        UPDATE strategic_recommendations s
           SET organization_id = COALESCE(
                 (SELECT b.organization_id FROM business_capability b WHERE b.id = s.capability_id),
                 (SELECT u.organization_id FROM users u WHERE u.id = s.created_by_id
                    AND NOT EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = u.id AND r.organization_id != u.organization_id)),
                 (SELECT u2.organization_id FROM users u2 WHERE u2.id = s.rated_by_id
                    AND NOT EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = u2.id AND r.organization_id != u2.organization_id))
               )
         WHERE s.organization_id IS NULL
           AND COALESCE(
                 (SELECT b.organization_id FROM business_capability b WHERE b.id = s.capability_id),
                 (SELECT u.organization_id FROM users u WHERE u.id = s.created_by_id
                    AND NOT EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = u.id AND r.organization_id != u.organization_id)),
                 (SELECT u2.organization_id FROM users u2 WHERE u2.id = s.rated_by_id
                    AND NOT EXISTS (SELECT 1 FROM org_roles r WHERE r.user_id = u2.id AND r.organization_id != u2.organization_id))
               ) IS NOT NULL
    """,
    # "values" is a reserved SQL keyword -- the table name must stay quoted.
    "values": """
        UPDATE "values" v
           SET organization_id = e.organization_id
          FROM archimate_elements e
         WHERE v.archimate_element_id = e.id
           AND v.organization_id IS NULL
           AND e.organization_id IS NOT NULL
    """,

    # roadmap_tasks rows predate the tenant column and carry no single
    # provenance link; each statement fills only NULLs, in precedence order.
    # The per-object links (the work package's creator, the consolidation
    # entry's application) are checked before the task's own creating user:
    # a user can be moved to a different organisation after the task was
    # created (an admin route reassigns a removed user to another
    # organisation), which would misattribute the task if the creating-user
    # statement ran first. The work package's creator can move too, but it
    # is ordinarily a different user than the task's own creator, and the
    # consolidation entry's application is not read off a user at all, so
    # checking both first is strictly safer than checking the task's own
    # creator first.
    "roadmap_tasks": [
        # 1. the creator of the work package the task belongs to
        """
        UPDATE roadmap_tasks t
           SET organization_id = u.organization_id
          FROM unified_work_packages w
          JOIN users u ON u.id = w.created_by
         WHERE w.id = t.unified_work_package_id
           AND t.organization_id IS NULL
        """,
        # 2. the application whose consolidation entry created the task
        """
        UPDATE roadmap_tasks t
           SET organization_id = a.organization_id
          FROM consolidation_list_entries e
          JOIN application_components a ON a.id = e.application_id
         WHERE e.roadmap_item_id = t.id
           AND t.organization_id IS NULL
           AND a.organization_id IS NOT NULL
        """,
        # 3. the user who created the task (set by the roadmap UI route);
        # checked last because this user's own organization_id can change
        # after the task was created
        """
        UPDATE roadmap_tasks t
           SET organization_id = u.organization_id
          FROM users u
         WHERE u.id = t.created_by
           AND t.organization_id IS NULL
        """,
    ],
    # strategic_roadmap_items predates the tenant column; its one trustworthy
    # provenance is the initiative it belongs to. Moved here from
    # reconcile_schema.py, which now adds columns and constraints only, never
    # organization_id values -- this is the one place that does.
    "strategic_roadmap_items": """
        UPDATE strategic_roadmap_items AS r
           SET organization_id = p.organization_id
          FROM strategic_initiatives AS p
         WHERE r.initiative_id = p.id
           AND r.organization_id IS NULL
           AND p.organization_id IS NOT NULL
    """,
}

# Tables whose link column to a parent is set at every creation site (a NULL
# link is "no provenance", never "gone" -- it is not purged), keyed to
# (link_column, parent_table). A row whose link names a parent that no
# longer exists is deleted, idempotently, before that table's derivation or
# count runs.
#
# Empty on this base. document_chunk_embeddings is the qualifying case --
# document_id is set at its one creation site (document_processing_service.py's
# chunk_and_embed) and joins ai_chat_document_uploads, and it now carries
# TenantMixin, so it is among _tenant_tables() and reaches this loop. Its
# entry is not added here yet: the document-delete route still leaves its
# chunks behind with no cascade, so a purge entry alone would just delete
# what that route keeps creating rather than closing the leak at its source.
# A derivation for this table also still lives in reconcile_schema.py rather
# than here; both are one change together, not made in this one.
_PURGE_ORPHANS = {}

# Tables whose remaining NULL rows carry per-row provenance rather than a
# single owning entity this command can always resolve: an ownership row
# whose own application has no organization_id, or a unit referenced by more
# than one organisation's ownership rows, or by none. Handing either to an
# operator-chosen --org-id would move another tenant's row into view, so with
# several organisations in the database the row stays NULL here and is only
# reported, never a candidate for the single-organisation or --org-id orphan
# assignment below. With exactly one organisation there is no other one it
# could belong to, so the ordinary single-organisation rule still applies.
_PROVENANCE_ONLY = {
    "application_ownership", "extraction_feedback", "options_analysis", "organization_units",
    "stakeholder_inputs",
    # Every table above whose _DERIVABLE_ORG entry can leave
    # a genuine remainder (a row whose own attribution columns are all NULL,
    # or all point at rows that are themselves unattributed) -- the fenced,
    # 100%-resolvable-by-a-NOT-NULL-FK tables (motivation_bridge_links,
    # strategic_milestones, capability_health_overrides, programme_snapshots,
    # solution_migration_roadmaps, solution_adr_links) are deliberately left
    # out: an orphan there cannot occur under normal FK integrity, so there is
    # nothing for this set to protect.
    "drivers", "goals", "meanings", "values", "assessments", "stakeholders", "requirements",
    "strategic_recommendations", "enterprise_briefings",
    # No _DERIVABLE_ORG entry at all -- every existing row is an orphan
    # candidate, for two different reasons:
    # monitoring_alerts/monitoring_baselines have no FK at all to any
    # tenant-fenced table -- affected_element_id is a bare, unconstrained
    # integer whose target table varies by affected_element_type
    # ("architecture", "capability", "vendor", ...), so there is no single
    # join that can resolve it without risking a wrong-tenant guess.
    "monitoring_alerts", "monitoring_baselines",
    # framework_instances/reference_model_import/industry_process_recommendation
    # DO have an FK (configuration_id/reference_model_id/industry_framework_id
    # and industry_process_id), but every one of those targets is a
    # HybridTenantMixin shared-catalogue table with organization_id always
    # NULL by design, so following it resolves to nothing either way.
    "framework_instances", "reference_model_import", "industry_process_recommendation",
}


def _resolve_org_id(conn, explicit):
    """The one organisation orphaned rows may be assigned to, or None.

    Counts active, non-default organisations only: an inactive organisation
    and the "default" organisation an install starts with are never a home
    for another tenant's rows. Exactly one such organisation is the only
    case this command assigns automatically or accepts --org-id for; zero or
    several means no assignment happens at all, with or without --org-id --
    an unresolved row is another tenant's data, or nobody's yet, never a
    guess this command is allowed to make.
    """
    from sqlalchemy import text

    eligible = conn.execute(
        text(
            "SELECT id, name FROM organizations "
            "WHERE COALESCE(is_active, TRUE) AND slug <> 'default' "
            "ORDER BY id"
        )
    ).fetchall()

    if explicit is not None:
        if len(eligible) > 1:
            listing = ", ".join(f"{r[0]}={r[1]}" for r in eligible)
            raise click.ClickException(
                f"{len(eligible)} active, non-default organizations exist ({listing}); "
                "--org-id cannot choose among them. An unresolved row is another "
                "tenant's data, not a guess this command is allowed to make."
            )
        if len(eligible) == 1 and eligible[0][0] != explicit:
            raise click.ClickException(
                f"--org-id={explicit} does not match the one active, non-default "
                f"organization (id={eligible[0][0]}, {eligible[0][1]})."
            )
        row = conn.execute(text("SELECT id FROM organizations WHERE id = :i"), {"i": explicit}).first()
        if not row:
            raise click.ClickException(f"No organization with id={explicit}.")
        return explicit

    if len(eligible) == 1:
        click.echo(
            f"  single active organization found: id={eligible[0][0]} ({eligible[0][1]}) "
            "— assigning orphans to it"
        )
        return eligible[0][0]

    # Zero, or more than one, eligible organisation: refuse to guess.
    return None


def _tenant_tables():
    """Every table mapped by a TenantMixin model, deduplicated and sorted.

    Derived from the mapper registry rather than a hand-kept list so the next
    model to gain the mixin is covered without editing this file. Dual-mapped
    tables (`extend_existing`) appear once.

    Names are re-checked against a strict identifier pattern before being
    returned. They come from mapped classes rather than from any request, so
    this cannot fail in practice — it exists so the f-string interpolation
    below (bandit B608, which cannot know the source is trusted) is guarded by
    something executable rather than by a comment.
    """
    import re

    from app.models.mixins import TenantMixin

    safe = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    tables = set()
    for mapper in db.Model.registry.mappers:
        if issubclass(mapper.class_, TenantMixin):
            name = mapper.local_table.name
            if not safe.match(name):
                raise RuntimeError(
                    f"refusing to interpolate unexpected table name {name!r}"
                )
            tables.add(name)
    return sorted(tables)


def _process_table(conn, insp, t, resolved_org, dry_run):
    """Purge, derive, assign, index and harden one table.

    Returns (status, extra): status is "healthy", "repaired" or
    "nullable_by_design"; extra is the unresolved-orphan count when the
    table was deferred, else None.

    "nullable_by_design" is distinct from a deferred table: several models
    override TenantMixin's default (nullable=False) back to nullable=True as
    a permanent choice -- UnifiedWorkPackage, ConnectorConfig and others --
    because an unattributable row is their deliberate, permanent quarantine,
    not a transient state this command's own backfilling is meant to
    resolve. Hardening must never fight that model declaration, however many
    orphans happen to be resolved right now; the check is against the
    model's own declared nullability (db.metadata), not the live column,
    since the live column is exactly what this function is deciding whether
    to change.

    Raises on an SQL error the caller could not have anticipated -- nothing
    here commits or rolls back on its own; the caller does that once, around
    the whole call, so a failure here undoes only this table's work. An
    *expected* DDL failure (SET NOT NULL skipped for a deferred table) is
    reported and swallowed here instead, because it is not a reason to
    discard everything else this table did.
    """
    from sqlalchemy import text

    if t in _PURGE_ORPHANS:
        link, parent = _PURGE_ORPHANS[t]
        # _PURGE_ORPHANS is a hardcoded dict; link and parent are trusted
        # identifiers.  Validate them anyway so bandit B608 can see the
        # guard is executable rather than a comment.
        import re
        _safe = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        if not (_safe.match(link) and _safe.match(parent)):
            raise RuntimeError(
                f"refusing to interpolate unexpected identifier {link!r} / {parent!r}"
            )
        from sqlalchemy import delete as sa_delete, select as sa_select, and_ as sa_and, not_ as sa_not
        from sqlalchemy.sql.expression import table as sa_table, column as sa_column
        tbl = sa_table(t)
        parent_tbl = sa_table(parent)
        link_col = sa_column(link)
        stmt = sa_delete(tbl).where(
            sa_and(
                link_col.isnot(None),
                sa_not(link_col.in_(sa_select(sa_column("id")).select_from(parent_tbl)))
            )
        )
        purged = conn.execute(stmt).rowcount
        if purged:
            click.echo(f"  - {t}: purged {purged} row(s) whose parent is gone")

    cols = {c["name"]: c for c in insp.get_columns(t)}
    col = cols.get("organization_id")
    indexes = {i["name"] for i in insp.get_indexes(t)}
    wanted_index = f"ix_{t}_organization_id"
    has_index = wanted_index in indexes or any(
        i["column_names"] == ["organization_id"] for i in insp.get_indexes(t)
    )

    if col is None:
        if dry_run:
            click.echo(f"  - {t}: would ADD COLUMN organization_id")
            return "repaired", None
        conn.execute(text(f'ALTER TABLE "{t}" ADD COLUMN IF NOT EXISTS organization_id INTEGER'))
        click.echo(f"  + {t}: added organization_id")
        col = {"nullable": True}

    # The model's own declared nullability -- not the live column's current
    # nullability, which is exactly what the hardening step below is
    # deciding whether to change -- is the one source of truth for whether
    # organization_id may ever be hardened to NOT NULL on this table. A
    # model that overrides TenantMixin's default back to nullable=True (see
    # the class docstrings/comments on UnifiedWorkPackage, ConnectorConfig,
    # EnterpriseInitiative, KanbanCard and the rest of this generation) has
    # made a permanent design choice: an unattributable row is left NULL as
    # its quarantine, not a defect for this command to fix once enough
    # other rows happen to get resolved.
    model_nullable = db.metadata.tables[t].columns["organization_id"].nullable

    # A table that can state its own tenant does so first, so those rows
    # never reach the guess-based orphan pass below. Run this in dry-run
    # too (it is rolled back with everything else this table did): the
    # orphan count taken right after must reflect rows with no provenance at
    # all, not rows a real run would derive a moment later, or the "would
    # leave NULL" line below overstates how many rows actually have none.
    if t in _DERIVABLE_ORG:
        stmts = _DERIVABLE_ORG[t]
        stmts = [stmts] if isinstance(stmts, str) else stmts
        derived = sum(conn.execute(text(s)).rowcount for s in stmts)
        if derived:
            verb, prefix = ("would derive", "-") if dry_run else ("derived", "+")
            click.echo(f"  {prefix} {t}: {verb} org for {derived} row(s) from the linked entity")

    orphans = conn.execute(
        text(f'SELECT count(*) FROM "{t}" WHERE organization_id IS NULL')
    ).scalar()

    if not orphans and has_index and (model_nullable or col.get("nullable") is False):
        return ("nullable_by_design" if model_nullable else "healthy"), None

    # Every table takes the branch #112 wrote only for roadmap_tasks: with no
    # single active, non-default organisation to assign to, residual rows
    # stay NULL and are reported, never guessed -- with or without --org-id,
    # which _resolve_org_id already refused up front on such a database.
    deferred = False
    if orphans:
        if resolved_org is not None:
            if dry_run:
                click.echo(f"  - {t}: would assign {orphans} orphaned row(s) to org {resolved_org}")
            else:
                conn.execute(
                    text(f'UPDATE "{t}" SET organization_id = :o WHERE organization_id IS NULL'),
                    {"o": resolved_org},
                )
                click.echo(f"  + {t}: assigned {orphans} orphaned row(s) to org {resolved_org}")
        else:
            deferred = True
            if dry_run:
                click.echo(
                    f"  - {t}: {orphans} row(s) have no tenant provenance; "
                    "would leave NULL and report, not assigned"
                )
            else:
                click.echo(
                    f"  ! {t}: {orphans} row(s) have no tenant provenance; "
                    "left NULL and reported, not assigned"
                )

    if dry_run:
        if model_nullable:
            if not has_index:
                click.echo(f"  - {t}: would add index; organization_id stays nullable (model declares it by design)")
        elif col.get("nullable") is not False or not has_index:
            click.echo(f"  - {t}: would add index / SET NOT NULL as needed")
        return ("nullable_by_design" if model_nullable else "repaired"), (orphans if deferred else None)

    # Index before NOT NULL, both idempotent; reconcile-schema adds neither.
    # A deferred table (unresolved provenance rows still NULL) gets the
    # index but not the NOT NULL constraint, which would only fail; that is
    # reported explicitly instead of via the try/except below. A table whose
    # model declares organization_id permanently nullable never gets the
    # NOT NULL DDL at all, deferred or not -- that is a design choice, not
    # an unresolved backlog, and is reported as such rather than lumped in
    # with the deferred-orphan case.
    ddls = [(f'CREATE INDEX IF NOT EXISTS {wanted_index} ON "{t}" (organization_id)', "index")]
    if model_nullable:
        click.echo(f"  = {t}: organization_id is nullable by design (model declares it); not-null skipped")
    elif deferred:
        click.echo(f"  ! {t}: not-null deferred: {orphans} unresolved row(s)")
    else:
        ddls.append((f'ALTER TABLE "{t}" ALTER COLUMN organization_id SET NOT NULL', "not-null"))
    for ddl, label in ddls:
        try:
            conn.execute(text(ddl))
        except Exception as exc:  # noqa: BLE001 — report, keep repairing other tables
            click.echo(f"  ! {t}: {label} skipped ({str(exc)[:100]})")
    click.echo(f"  + {t}: hardened (index{'' if (deferred or model_nullable) else ', NOT NULL'})")
    return ("nullable_by_design" if model_nullable else "repaired"), (orphans if deferred else None)


def repair_layer_tenancy(org_id=None, dry_run=False):
    """Repair organization_id on every TenantMixin table that needs it.

    --org-id is resolved once, before any table's statements run, so a
    database with more than one active, non-default organisation rejects it
    up front rather than partway through the run. Each table then runs its
    purge, derivation, assignment, index and hardening in its own
    transaction, committed before the next table starts (rolled back, always,
    under dry_run). A table whose statements raise is rolled back on its
    own, recorded in "failed", and every other table still runs.

    Returns {"repaired": [...], "skipped_healthy": n, "absent": [...],
    "unresolved": {table: count}, "failed": {table: reason},
    "nullable_by_design": [...]}. A table in "nullable_by_design" never
    appears in "unresolved": organization_id there is a permanent model
    choice, not a deferred backlog this command failed to resolve.
    """
    from sqlalchemy import inspect

    # Introspect on db.session's own connection/transaction, not a second,
    # separately-pooled one from db.engine: an engine-level inspector opens
    # its own connection, so its catalog reads (get_table_names/get_columns/
    # get_indexes) contend for the same relation locks db.session's
    # transaction already holds from its own purge/derive/DDL statements on
    # this table -- including, in a test fixture that holds an open,
    # uncommitted transaction across the whole call, the relation lock a
    # column-level ALTER on a still-open test transaction holds, which a
    # second connection's schema reflection then waits on forever. Same
    # connection, same transaction: nothing here can lock against itself.
    live = set(inspect(db.session.connection()).get_table_names())

    repaired, absent, nullable_by_design = [], [], []
    healthy = 0
    unresolved = {}
    failed = {}

    resolved_org = _resolve_org_id(db.session.connection(), org_id)

    for t in _tenant_tables():
        if t not in live:
            absent.append(t)
            continue

        # Both re-fetched every iteration, not once before the loop: a real
        # (non-dry-run) run commits after every table (and a dry-run, or a
        # failed table, rolls back every table), and each of those ends the
        # SQLAlchemy Connection object the previous iteration was bound to --
        # db.session.connection() afterwards returns a new one. An Inspector
        # built on the old one would raise ResourceClosedError on the very
        # next table it has not already cached (confirmed empirically: a
        # single Inspector instance reused past a commit/rollback fails this
        # way the moment it is asked about a table it has not seen before).
        # The per-table cache this throws away was never serving a repeat
        # read anyway -- _tenant_tables() is deduplicated, so a fresh
        # Inspector per iteration costs nothing a stale one was saving.
        conn = db.session.connection()
        insp = inspect(conn)
        try:
            status, deferred_count = _process_table(conn, insp, t, resolved_org, dry_run)
        except Exception as exc:  # noqa: BLE001 — isolate this table, keep going
            db.session.rollback()
            failed[t] = str(exc)[:200]
            click.echo(f"  ! {t}: failed and rolled back ({str(exc)[:150]})")
            continue

        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()

        if status == "healthy":
            healthy += 1
        elif status == "nullable_by_design":
            nullable_by_design.append(t)
        else:
            repaired.append(t)
            if deferred_count is not None:
                unresolved[t] = deferred_count

    return {
        "repaired": repaired,
        "skipped_healthy": healthy,
        "absent": absent,
        "unresolved": unresolved,
        "failed": failed,
        "nullable_by_design": nullable_by_design,
    }


@click.command("backfill-layer-tenancy")
@click.option("--dry-run", is_flag=True, help="Report what would change; change nothing.")
@click.option("--org-id", type=int, default=None, help="Organization to assign orphaned rows to.")
@with_appcontext
def backfill_layer_tenancy(dry_run, org_id):
    """Backfill and harden organization_id on every TenantMixin table."""
    stats = repair_layer_tenancy(org_id=org_id, dry_run=dry_run)
    if stats["absent"]:
        click.echo(f"  {len(stats['absent'])} mapped table(s) absent (created by init-db later): "
                   + ", ".join(stats["absent"][:6]) + ("…" if len(stats["absent"]) > 6 else ""))
    click.echo(
        f"  {'would repair' if dry_run else 'repaired'} {len(stats['repaired'])} table(s); "
        f"{stats['skipped_healthy']} already healthy."
    )
    for table, count in stats.get("unresolved", {}).items():
        click.echo(f"  {table}: {count} row(s) left without a tenant; no provenance found")
    if stats.get("nullable_by_design"):
        click.echo(
            f"  {len(stats['nullable_by_design'])} table(s) left nullable by permanent model design "
            "(not hardened, not a backlog): " + ", ".join(stats["nullable_by_design"][:6])
            + ("…" if len(stats["nullable_by_design"]) > 6 else "")
        )
    if stats["failed"]:
        click.echo(f"\n{len(stats['failed'])} table(s) FAILED:")
        for table, reason in stats["failed"].items():
            click.echo(f"  ! {table}: {reason}")
        raise SystemExit(1)


def init_app(app):
    app.cli.add_command(backfill_layer_tenancy)
