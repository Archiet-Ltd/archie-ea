"""Tests stop switching triggers off to reach an append-only row.

Deleting an organisation admits its own append-only rows through a
transaction-local marker the guard functions check (``archie_org_deletion_admits``), never by
disabling a trigger. ``session_replication_role`` disables every trigger in the session,
including the ones that keep the audit chain true, so a test that uses it can no longer be
trusted to catch a trigger defect. This is a regression barrier, not a one-time sweep: a test
written tomorrow that reaches for ``session_replication_role`` fails here, in a targeted run,
before it reaches CI.

Every teardown-only use has been removed: the arb_canonical_conditions /
arb_condition_evidence_records cycle those sites were working around is now resolved by a
DEFERRABLE INITIALLY DEFERRED foreign key (``fk_arb_condition_evidence_condition``,
migration ``20261030_arb_cond_evidence_fk``), so the deletion planner orders around it like
it already does for the arb_submission_evidence_snapshots ring, and no test needs the switch
to reach it any more.

A handful of sites construct a state no real caller can reach through any admitted path at all
(a receipt "damaged" after it already succeeded, a lease fence bumped out of band) precisely
because the production concurrency guard they sit under is doing its job: refusing a real
caller the same write. Resolving one of these for good means adding a new, named admission
branch to that guard — a decision for whoever owns that guard, not a test change.

That round also removed the old per-line ``# session-replication-role-ok: <reason>`` escape
hatch: it let a future test self-grant the exception by typing a comment, which is exactly how
one more teardown bypass (``tests/test_schema_migrations.py``) was added in the same branch
this file was meant to stop. The sites below are the ones reviewed and ruled
genuinely not teardown, each with its reason recorded for that ruling; each file's count here
can only fall, and a file or a count not already in this registry fails the test outright,
comment or no comment.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
THIS_FILE = Path(__file__).resolve()

# relative path -> the exact number of session_replication_role lines that have been ruled on.
# Lower this when a use is removed; raise it only with a new ruling recorded in the report
# above. Never add a bare comment to clear a new one.
_RULED_COUNTS = {
    "test_alert_rules.py": 2,
    "test_api_token_migration.py": 2,
    "test_arb_condition_lifecycle_integration.py": 2,
    "test_decision_brief_service.py": 3,
    "test_schema_reconciliation.py": 2,
    "test_transformation_command_service.py": 4,
    "test_transformation_db_guards.py": 1,
    "test_transformation_execution_service.py": 2,
    "test_transformation_gate_service.py": 1,
    "test_transformation_option_service.py": 1,
    "test_transformation_room_public_journey.py": 1,
    "test_transformation_runtime_role.py": 1,
    "test_typed_arb_read_models.py": 8,
    "smoke/test_arb_status_chart_renders.py": 1,
    "smoke/test_typed_arb_governance_journey.py": 5,
}


def test_no_test_sets_session_replication_role():
    counts = {}
    for path in TESTS_DIR.rglob("*.py"):
        if path.resolve() == THIS_FILE:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = text.count("session_replication_role")
        if hits:
            counts[str(path.relative_to(TESTS_DIR))] = hits

    unexpected = []
    for relative_path, hits in counts.items():
        allowed = _RULED_COUNTS.get(relative_path, 0)
        if hits > allowed:
            unexpected.append(f"{relative_path}: {hits} found, {allowed} ruled on")
    stale = [
        relative_path for relative_path in _RULED_COUNTS
        if counts.get(relative_path, 0) < _RULED_COUNTS[relative_path]
    ]
    assert not unexpected, (
        "session_replication_role disables every trigger in the session, append-only guards "
        "included; use the organisation-delete marker (archie_org_deletion_admits) or the "
        "owning writer instead. Every use must be named in _RULED_COUNTS above with its "
        "ruling recorded in the report -- a comment on the line no longer clears one. Found:\n  "
        + "\n  ".join(unexpected)
    )
    assert not stale, (
        "_RULED_COUNTS above claims more session_replication_role uses than exist; lower the "
        "count for:\n  " + "\n  ".join(stale)
    )
