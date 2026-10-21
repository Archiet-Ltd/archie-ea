"""A command's own claim must actually be visible to its own live-fence check.

Production report: the Transformation Programme wizard at
/solutions/new-programme answered every submission with
"stale_or_expired_claim", and every retry after that with "active_lease" --
the claim was made (command_idempotency_records moved to
status='in_progress') but the very next check the same request ran against
its own, just-committed row answered "not found".

Root cause confirmed by direct reproduction, not a database privilege gap: a
missing grant on command_idempotency_records or on
archie_claim_transformation_command was tried first (CommandService.execute
run against a deliberately under-granted copy of this restricted role) and
produces a raw psycopg ``InsufficientPrivilege``/generic 500, never the
"stale_or_expired_claim" text production actually reported.

The real cause is row-level security, not the claim/lease state machine
itself. ``command_idempotency_records`` (and ``operation_results``, which
CommandService also reads directly) are RLS-fenced tenant tables
(migrations/versions/20261008_row_level_security.py onward); every policy
checks the ``archie.organization_id`` session GUC. That GUC is set only by
``app.middleware.tenant_isolation``'s listeners, which are wired onto
Flask-SQLAlchemy's own ``db.session`` (``after_begin`` / ``do_orm_execute`` /
``before_flush``). CommandService deliberately opens its own, separate
``Session(db.engine)`` / ``_FencedSession`` objects instead of using
``db.session`` (so a long-running command's fence check never autoflushes
the caller's own pending domain writes) -- but that means none of those
listeners ever fire for it. Confirmed directly against a freshly migrated,
fully RLS-enabled restricted-role database: ``CommandService.assert_fence``'s
plain ORM SELECT on the row ``claim_or_reconcile`` had just committed, one
statement earlier, in the very same process, came back empty -- not because
the row was not there, but because RLS silently hides every row from a
session with no tenant context, indistinguishable from "receipt not found".
That answers exactly "stale_or_expired_claim" on the very first claim ever
made, every time, which matches a report of *every* submission failing, not
an occasional one.

``app/middleware/tenant_isolation.py`` already names this exact gap and
already exports the fix for it: ``set_database_tenant_context(connection,
organization_id)`` is the same helper
``app.jobs.tenant_safe_job.platform_scope`` uses for its own
``db.engine.connect()`` callers, documented there as being for "a few
runtime paths [that] open db.engine.connect() or Session(db.engine)
directly ... so the session after_begin listener never sees them." The fix
reuses it rather than reimplementing it, calling it once at the start of
every transaction CommandService opens (``CommandService._scope_session``).

This module drives CommandService.execute itself (not just the raw SQL
claim function tests/test_transformation_runtime_role.py exercises) through
the real, restricted ``archie_runtime`` role, reusing the full deploy/
runtime database built by test_runtime_role_domain_writes.py's
``deployed_roles_database`` fixture (roles via configure_database_roles,
schema via init-db/schema-upgrade/reconcile-schema -- which is also where
row-level security is installed -- grants re-applied after) rather than
hand-rolling a second copy of that bootstrap. Every other CommandService
test connects as the database owner or a superuser, which bypasses RLS
entirely and could not have caught this.
"""

from __future__ import annotations

import json
import sys

from tests.test_runtime_role_domain_writes import _run, deployed_roles_database  # noqa: F401


def test_first_claim_goes_live_as_the_runtime_role(deployed_roles_database):
    """Catches a claim that commits but is invisible to its own live-fence check.

    A claim that is made but never goes live is invisible to any test that
    only checks the claim RPC's immediate return value (it returns
    ``claimed`` correctly, since archie_claim_transformation_command is a
    SECURITY DEFINER function unaffected by the caller's own missing tenant
    context); the defect only shows up one step later, in the plain ORM
    live-fence check every real command handler runs immediately
    afterwards, under the same restricted role connection -- and the same
    row-level security -- production uses.
    """
    done = _run(
        [sys.executable, "-m", "tests._command_claim_probe_driver"],
        deployed_roles_database["runtime_url"],
    )
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, f"driver produced no result:\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}"
    result = json.loads(lines[-1][len("RESULT "):])

    assert result.get("outcome") == "succeeded", (
        f"first claim did not go live as {deployed_roles_database['runtime_role']}: {result}"
    )
    assert result["created"] is True

    # The exact production symptom: a second attempt with the same
    # idempotency key must be answered idempotently, never "active_lease".
    assert result.get("replay_outcome") == "succeeded", (
        f"replay of a succeeded claim should be idempotent, not a conflict: {result}"
    )
    assert result["replay_idempotent"] is True
