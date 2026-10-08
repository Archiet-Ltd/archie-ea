#!/bin/sh
# One-shot schema-owner container. Runtime web/worker containers never receive
# DATABASE_ADMIN_URL or DATABASE_DEPLOY_PASSWORD.
set -eu

# Schema order: create missing tables, apply versioned revisions, then detect
# drift. schema-upgrade is not suppressed: a revision that fails rolls back on
# its own (PostgreSQL DDL is transactional) and the deploy must stop there
# rather than start new code on a schema it does not expect. On an existing
# database the baseline revision changes nothing; a database recorded at a
# pre-baseline revision is re-stamped (record only) and the old id is printed.
flask --app manage init-db
flask --app manage schema-upgrade
flask --app manage reconcile-schema
flask --app manage migrate-connector-credentials || echo 'WARN connector credential migration skipped - ServiceNow/Lucidchart secrets saved before this fix remain only in the retired encrypted columns; ServiceNowConnectorService and LucidchartConnectorService read the new OrgCredentialVault store and will treat those organisations as unconfigured until it runs'
flask --app manage reconcile-admin-flags || echo 'WARN reconcile-admin-flags skipped - stale is_org_admin flags may disagree with is_admin() until it runs'
flask --app manage backfill-ai-chat-approval-org || echo 'WARN AI chat approval tenancy backfill skipped - legacy approvals remain unavailable for review until requester organization ownership is restored'
flask --app manage backfill-archimate-layer-casing || echo 'WARN archimate layer casing backfill skipped - ArchiMate elements stored with a capitalised layer will not match any query until it runs'
flask --app manage backfill-layer-tenancy || echo 'WARN layer tenancy backfill skipped - newly tenant-scoped tables keep nullable organization_id until it runs; rows left NULL are invisible to every org'
flask --app manage backfill-value-stream-tenancy || echo 'WARN value-stream tenancy backfill skipped - run manually with --org-id'
flask --app manage backfill-principle-org || echo 'WARN principle tenancy backfill skipped - run manually with --org-id'
flask --app manage backfill-initiative-org || echo 'WARN initiative tenancy backfill skipped - run manually with --org-id'
flask --app manage backfill-kanban-card-org || echo 'WARN kanban card tenancy backfill skipped'
flask --app manage backfill-saved-diagram-tenancy || echo 'WARN saved-diagram tenancy backfill skipped - composer diagrams keep nullable organization_id until it runs; rows left NULL are invisible to every org (CMP-01)'
flask --app manage drop-audit-log-viewpoint-fk || echo 'WARN audit-log viewpoint-FK drop skipped - composer audit writes keep failing with a FK violation until it runs (CMP-03)'
flask --app manage backfill-architect-role
flask --app manage backfill-decision-register-consolidation || echo 'WARN decision register consolidation backfill skipped - decision_ledger rows remain untenanted and architecture_decision_records rows stay unpaired with the canonical register until it runs' >&2
flask --app manage backfill-solution-risk-merge || echo 'WARN solution risk merge backfill skipped - solution risks stay unlinked from the one risk register (no risk_entity_links row, no shared score history) until it runs'
# RUN-01: copy ARB, ArchiMate composer and rationalisation audit history into
# soc2_audit_log (the system of record per ADR 0008). Runs here because
# CREATE INDEX IF NOT EXISTS ix_soc2_audit_org_id requires table ownership
# (the schema-deploy service connects as the deploy role that owns the tables).
# Idempotent: a row whose retired_into_id is set is skipped.
flask --app manage backfill-audit-trail || echo 'WARN audit trail backfill skipped - older audit entries from ARB, ArchiMate composer and rationalisation stores remain uncopied until it runs (RUN-01)' >&2
flask --app manage backfill-review-queue-approvals || echo 'WARN approval-queue consolidation backfill skipped - pending rows from review_queue_items, relationship_suggestions and solution_blueprint_proposals remain uncopied until it runs' >&2

# ADR 0008 / unified_work_packages tenancy and consolidation. The unified
# work packages table predates TenantMixin; reconcile-schema above adds
# organization_id as NULL. The first backfill attributes every existing row
# to its owning organisation (linked programme or element, else creator,
# else quarantine). The merge copies rows from the four legacy stores
# (work_packages, roadmap_work_packages, technology_roadmap_initiatives,
# implementation_work_packages) into unified_work_packages with provenance.
# The second backfill picks up rows that the merge resolved through a FK
# its own attribution chain did not try. Each command is idempotent.
flask --app manage backfill-work-package-org || echo 'WARN work package tenancy backfill skipped - unified_work_packages rows without an organisation are invisible to every org until this runs'
flask --app manage merge-work-package-stores || echo 'WARN work package store merge skipped - legacy work package stores will not appear in the unified view until this runs'
flask --app manage backfill-work-package-org || echo 'WARN work package tenancy second pass skipped - merged rows that resolved through a new FK remain unattributed until this runs'
# The generic history trigger reconcile-schema cannot create, then
# one seeded version per pre-existing element/relationship. Trigger first so
# the backfill's "no entity_history row at all" check is not racing a
# concurrent write that the trigger would otherwise have versioned.
flask --app manage apply-entity-history-trigger || echo 'WARN entity-history trigger skipped - no version is recorded for a changed element/relationship until it runs' >&2
flask --app manage backfill-entity-history || echo 'WARN entity-history backfill skipped - pre-existing elements/relationships have no history version until it runs' >&2

# ADR 0008 -- give unified_capabilities (the canonical capability store, per
# app/models/unified_capability.py and docs/adr/0008-one-system-of-record.md) a
# producer. Must run after reconcile-schema (provenance columns) and after every
# backfill-*-tenancy step above (an ownerless business_capability row is
# blocker 1 in app/commands/project_capabilities.py and would abort the
# projection). The migration step is NOT suppressed: it only creates an index
# (CREATE UNIQUE INDEX IF NOT EXISTS), is a no-op on a database where it already
# ran, and project-capabilities cannot run without it -- silencing a failure
# here would silently leave the projection permanently blocked with no signal.
flask --app manage apply-unified-capability-provenance-migration

# ADR 0008 -- classify every pre-existing unified_capabilities row (the seven
# direct writers named in app/commands/project_capabilities.py's own docstring
# can leave organization_id/scope NULL) before project-capabilities or the
# catalog backfill below add any more rows to classify. Must run first: both
# of those commands read scope/organization_id on rows that may already be
# sitting there unclassified, and `apply`'s own ordering guard
# (app/commands/cutover_capability_tenancy.py) otherwise blocks a projection
# run from leaving every freshly-projected row permanently 'ambiguous'.
#
# `--apply` refuses outright without a recorded backup manifest
# (CutoverBlocked) -- it is not a mechanism this script invents, it already
# existed in cutover_capability_tenancy.py before this change. Bridges to
# whichever verified backup this box actually takes: a marker in
# deploy/archie-backup.sh's own format (`$DIR/LAST_SUCCESS`, `file=<path>` on
# its last line), written either by that script (archie-backup.timer) or, in
# production, by deploy/write-backup-marker.sh called from the production
# backup step after its own verified dump -- same format, same reader, no
# second marker mechanism. scripts/database/backup_marker_to_manifest.sh
# turns that marker into the JSON manifest the cutover command requires.
# A box that has not completed a verified backup yet has no marker, so this
# step correctly WARNs and skips rather than cutting over unprotected -- same
# non-fatal convention as every backfill-* line above, so one missing or
# stale backup does not 503 the whole platform.
#
# ARCHIE_BACKUP_MARKER overrides the marker path (default: where
# deploy/archie-backup.sh writes it) so a test, or a deploy whose backup
# folder is mounted somewhere else, can point this at a different marker
# instead of the default host path under /var/backups.
BACKUP_MARKER=${ARCHIE_BACKUP_MARKER:-/var/backups/archie/LAST_SUCCESS}
CUTOVER_MANIFEST=/tmp/cutover-capability-tenancy-manifest.json
sh "$(dirname "$0")/backup_marker_to_manifest.sh" "$BACKUP_MARKER" "$CUTOVER_MANIFEST"
if [ -f "$CUTOVER_MANIFEST" ]; then
    flask --app manage cutover-capability-tenancy --apply \
        --backup-manifest "$CUTOVER_MANIFEST" \
        --report /tmp/cutover-capability-tenancy-report.json \
        && echo 'cutover-capability-tenancy --apply succeeded' \
        || echo 'WARN capability tenancy cutover blocked or failed (see logs / /tmp/cutover-capability-tenancy-report.json) - unified_capabilities rows left organization_id IS NULL AND scope IS NULL until an ambiguous classification is resolved and cutover-capability-tenancy --apply is re-run' >&2
else
    echo "WARN capability tenancy cutover skipped - no backup marker at $BACKUP_MARKER yet (no verified backup has written one); unified_capabilities rows left organization_id IS NULL AND scope IS NULL until one runs with a real backup available" >&2
fi

# Corrected 17 Sep 2026 -- this comment previously said project-capabilities
# was deliberately NOT suppressed. That was wrong: run_projection() can raise
# ProjectionBlocked for any one of five reasons (app/commands/project_capabilities.py),
# several of which are a SINGLE tenant's bad data (a tenant_code_collision or
# archimate_id_collision belonging to one org), not a global schema problem. With
# `set -eu` and this line unsuppressed, that one tenant's bad row aborted schema
# deploy for EVERY tenant on the box -- the exact blast-radius mistake the other
# backfill-* lines above already avoid with `|| echo WARN`. Matching that
# convention here: a blocked projection is loud (stderr WARN + whatever
# project-capabilities itself already logs) but non-fatal, so unrelated tenants
# still get a working boot while the blocked tenant's projection is fixed
# separately. `unified_capabilities` staying stale for one tenant is a `store-agreement`/
# `canonical-store` finding to chase down, not a reason to 503 the whole platform.
# Corrected again 17 Sep 2026 (round 3) -- `owner_or_code_changed_since_projection`
# is no longer one of the reasons this command aborts its whole run: it is now a
# per-row skip (report["skipped"]), not a hard blocker, so THIS WARN firing means
# one of the four remaining hard blockers (ownerless_source_rows,
# tenant_code_collision, archimate_id_collision, source_hierarchy_cycle) or a lock
# conflict / missing index, not a moved row.
flask --app manage project-capabilities --apply --report /tmp/project-capabilities-report.json \
    && echo 'project-capabilities --apply succeeded' \
    || echo 'WARN capability projection blocked (see logs / /tmp/project-capabilities-report.json) - unified_capabilities may be stale for one or more tenants until the blocker (ownerless_source_rows, tenant_code_collision, archimate_id_collision, source_hierarchy_cycle, missing provenance index, or a competing cutover/projection lock) is resolved and project-capabilities --apply is re-run. A moved/re-parented row (owner_or_code_changed_since_projection) is no longer a reason this fires -- it is skipped per-row and reported in the report JSON, not blocked.' >&2
if [ -f /tmp/project-capabilities-report.json ]; then
    echo '--- project-capabilities report ---'
    cat /tmp/project-capabilities-report.json
    echo '--- end report ---'
fi

# ADR 0008 -- retire the remaining four superseded capability stores
# (capabilities, enterprise_capabilities, archimate_capabilities,
# technical_capabilities) into unified_capabilities. Must run after
# project-capabilities: an enterprise_capabilities/archimate_capabilities row
# linked to a business_capability is retired straight into that capability's
# own projection, so a business_capability that has not been projected yet
# leaves its linked rows quarantined (visible at /admin/errors) rather than
# guessed at. Same WARN-on-failure convention as the backfill-* lines above:
# one organisation's unresolved row must not 503 the whole platform.
flask --app manage backfill-capability-catalogs --apply \
    || echo 'WARN capability catalog backfill incomplete - capabilities/enterprise_capabilities/archimate_capabilities/technical_capabilities may still hold rows neither merged nor quarantined into unified_capabilities; see /admin/errors and re-run flask backfill-capability-catalogs --apply' >&2
