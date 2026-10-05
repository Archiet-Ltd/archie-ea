#!/bin/sh
# One-shot schema-owner container. Runtime web/worker containers never receive
# DATABASE_ADMIN_URL or DATABASE_DEPLOY_PASSWORD.
set -eu

flask --app manage init-db
# reconcile-schema exits 1 when it reports quarantined rows (conflicts/orphans
# that cannot be auto-resolved) or other schema drift it has logged.  That is a
# recoverable, expected condition on a long-lived database and must not abort the
# deploy chain — the four backfill commands below are what actually resolve most
# of those rows.  Any other non-zero exit (Python crash, connection failure,
# permission denied on ALTER TABLE, etc.) produces exit code 2 or a signal and
# is NOT suppressed here, so a genuinely fatal schema failure still aborts the
# deploy.
if flask --app manage reconcile-schema; then _rc=0; else _rc=$?; fi
if [ $_rc -ne 0 ] && [ $_rc -ne 1 ]; then exit $_rc; fi
[ $_rc -eq 0 ] || echo 'WARN reconcile-schema reported quarantined rows (conflicts/orphans) - see logs; subsequent backfills still run'
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
