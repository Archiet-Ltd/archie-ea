"""Tests stop switching triggers off to reach an append-only row.

ADR-org-deletion-01: deleting an organisation admits its own append-only rows through a
transaction-local marker the guard functions check (``archie_org_deletion_admits``), never by
disabling a trigger. ``session_replication_role`` disables every trigger in the session,
including the ones that keep the audit chain true, so a test that uses it can no longer be
trusted to catch a trigger defect. This is a regression barrier, not a one-time sweep: a test
written tomorrow that reaches for ``session_replication_role`` fails here, in a targeted run,
before it reaches CI.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
THIS_FILE = Path(__file__).resolve()


def test_no_test_sets_session_replication_role():
    offenders = []
    for path in TESTS_DIR.rglob("*.py"):
        if path.resolve() == THIS_FILE:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "session_replication_role" in text:
            for lineno, line in enumerate(text.splitlines(), start=1):
                if "session_replication_role" in line:
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert not offenders, (
        "session_replication_role disables every trigger in the session, append-only guards "
        "included; use the organisation-delete marker (archie_org_deletion_admits) or the "
        "owning writer instead. Found in:\n  " + "\n  ".join(offenders)
    )
