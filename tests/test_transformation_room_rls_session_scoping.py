"""Independent Transformation Room sessions must see their own tenant's rows.

PR 763 fixed one case of this: ``CommandService`` opens its own
``Session(db.engine)`` objects (deliberately apart from Flask-SQLAlchemy's own
``db.session``, so a long command's fence recheck never autoflushes the
caller's own pending writes), and ``app/middleware/tenant_isolation.py``'s
row-level-security tenant-context listeners are wired onto ``db.session``
specifically. Any independent session reading an RLS-fenced table through a
connection with no tenant context sees nothing -- not an error, just an empty
or missing result, indistinguishable from the row genuinely not existing.

Auditing the eight sibling files in the same package that also open
independent sessions directly against ``db.engine`` found the same gap, for
real, in six of them: ``programme_service.py``, ``decision_service.py``,
``evidence_service.py``, ``execution_service.py``, ``gate_service.py``,
``http.py`` and ``read_models.py`` each had at least one independent session
reading a row-level-security-fenced table
(``migrations/versions/20261008_row_level_security.py`` onward) with no
tenant context set on it. Each is fixed the same way PR 763 fixed
``command_service.py``: call
``app.middleware.tenant_isolation.set_database_tenant_context(connection,
organization_id)`` once, immediately after opening the session, before any
query.

``arb_waiver_expiry_batch_service.py`` (the eighth file) is NOT part of this
fix: its two independent sessions are already covered by a different,
pre-existing, already-documented mechanism -- the global SQLAlchemy ``Engine``
"begin" hook ``app.middleware.tenant_isolation._install_platform_scope_engine_hook``,
whose own docstring names this exact file ("the typed-ARB waiver expiry") as
one of the paths it exists for. Its one production caller
(``app/_bootstrap/extensions.py``'s APScheduler job) already wraps the call in
``with platform_scope(...)``, which is what makes that hook fire. (A related,
separate gap was found in that audit and is called out in the accompanying
report rather than fixed here: the CLI entry point
``app/commands/process_arb_waiver_expiries.py`` does not wrap its call in
``platform_scope(...)`` the way the scheduler does, so running it directly
would never see any due waiver -- but that fix belongs in a file outside this
change's scope.)

This module drives the real, restricted ``archie_runtime`` role against a
fully row-level-security-enabled database (schema deployed and grants applied
the way a real deploy does), reusing
``tests/test_runtime_role_domain_writes.py``'s ``deployed_roles_database``
fixture and the same subprocess-driver pattern as
``tests/test_command_claim_runtime_role.py`` /
``tests/_command_claim_probe_driver.py``: every other test of these services
connects as the database owner or a superuser, which bypasses row-level
security entirely and could not have caught this.
"""

from __future__ import annotations

import json
import sys
from urllib.parse import urlsplit, urlunsplit

from tests.test_runtime_role_domain_writes import (  # noqa: F401
    _maintenance_url,
    _run,
    deployed_roles_database,
)


# One call site per fixed function (see the module docstring for which file
# each belongs to), plus one direct check that the underlying primitive this
# whole fix relies on actually fences a second organisation's rows out.
EXPECTED_OK_KEYS = (
    "programme_service.list_programmes",
    "programme_service.get_programme",
    "gate_service.load_policy_snapshot",
    "http.resolve_enterprise_roles",
    "read_models.room",
    "read_models.stage",
    "read_models.load_programmes",
    "evidence_service.load_application_for_tenant",
    "evidence_service.load_assigned_request",
    "evidence_service.active_evidence",
    "read_models.evidence_debt",
    "decision_service.load_option_for_tenant",
    "decision_service.authorise_draft",
    "decision_service.load_versions_for_tenant",
    "decision_service.load_brief_for_tenant",
    "decision_service.current_option_version_ids",
    "decision_service.current_evidence_ids",
    "decision_service.require_latest_frozen_version",
    "decision_service.require_version_for_tenant",
    "execution_service.await_or_recover_export_attempt",
    "cross_tenant_rows_stay_hidden",
    "archimate_cleanup_other_org_leaves_rows",
    "archimate_cleanup_deletes_own_org_rows",
)


def test_every_fixed_independent_session_sees_its_own_tenant_rows(
    deployed_roles_database, monkeypatch
):
    """Every independent session this change fixed must see its own org's rows.

    Run against the pre-fix code, every key below whose function opens a
    ``Session(db.engine)`` / ``db.engine.connect()`` against a row-level-
    security-fenced table would report ``NotFound`` (or an equivalent empty
    result), not because the row is missing, but because row-level security
    silently hides every row from a session with no tenant context -- exactly
    the production symptom PR 763 fixed for ``command_service.py``.
    """
    # The runtime role cannot insert decision briefs directly; the driver seeds
    # those two fixtures through the maintenance (owner) login on the same database.
    owner_url = urlunsplit(
        urlsplit(_maintenance_url())._replace(path=f"/{deployed_roles_database['name']}")
    )
    monkeypatch.setenv("PROBE_OWNER_URL", owner_url)
    done = _run(
        [sys.executable, "-m", "tests._rls_session_scoping_probe_driver"],
        deployed_roles_database["runtime_url"],
    )
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, f"driver produced no result:\n{done.stdout[-4000:]}\n{done.stderr[-4000:]}"
    result = json.loads(lines[-1][len("RESULT "):])

    missing_keys = set(EXPECTED_OK_KEYS) - set(result)
    assert not missing_keys, f"driver did not report on: {missing_keys}\nfull result: {result}"

    failures = {key: result[key] for key in EXPECTED_OK_KEYS if result[key] is not True}
    assert not failures, (
        "one or more fixed independent sessions could not see their own "
        f"tenant's rows under the restricted runtime role: {failures}"
    )
