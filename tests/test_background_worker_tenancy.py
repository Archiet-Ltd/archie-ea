"""Background work handed off by a request runs in the owning organisation.

A spawned solution worker and the batch-import task start with no request and
therefore no ``g.current_org_id``, so the isolation listeners apply no filter
at all. Each now resolves the organisation that owns its record and does its
work inside ``tenant_scope`` of that organisation.

The identity-map test reproduces the example in CLAUDE.md on a real call path:
two organisations' solutions handled one after the other in one app context,
where organisation B asks ``Query.get()`` for the row organisation A just
loaded. Without a session reset between them the identity map hands B the
cached row with no SQL and no tenant predicate.

Two real organisations through the shared ``db_session`` / ``make_org``
fixtures. Setup rows are COMMITTED (a savepoint release under ``db_session``)
because ``tenant_scope`` removes the session, which rolls back anything only
flushed.
"""

from __future__ import annotations

import os
import uuid

import pytest
from flask import g

pytestmark = pytest.mark.usefixtures("db_session")


def _solution(db_session, org_id, label):
    from app.models.solution_models import Solution

    row = Solution(name=f"{label}-{uuid.uuid4().hex[:8]}", organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def two_orgs_with_solutions(db_session, make_org):
    org_a, org_b = make_org("wa"), make_org("wb")
    sol_a = _solution(db_session, org_a.id, "A")
    sol_b = _solution(db_session, org_b.id, "B")
    ids = (org_a.id, org_b.id, sol_a.id, sol_b.id)
    db_session.commit()
    return ids


class _RecordingOrchestrator:
    """Stands in for JourneyOrchestrator; records the tenant it ran under."""

    seen: list = []

    def __init__(self, solution_id):
        self.solution_id = solution_id

    def _record(self, what):
        from app.models.solution_models import Solution

        visible = sorted(s.id for s in Solution.query.all())
        type(self).seen.append(
            {
                "what": what,
                "solution_id": self.solution_id,
                "org": getattr(g, "current_org_id", None),
                "visible": visible,
            }
        )

    def rebuild_relationships(self):
        self._record("rebuild")

    def confirm_domain(self, code):
        self._record(f"confirm:{code}")

    def generate_decision_rationale(self):
        self._record("rationale")


@pytest.fixture
def recording_orchestrator(app, monkeypatch):
    import app as app_package
    from app.modules.architecture_assistant import journey_orchestrator

    _RecordingOrchestrator.seen = []
    monkeypatch.setattr(journey_orchestrator, "JourneyOrchestrator", _RecordingOrchestrator)
    # The workers boot their own app in a fresh process; here they reuse the
    # test app so they share the test's rolled-back connection.
    monkeypatch.setattr(app_package, "create_app", lambda *a, **k: app)
    return _RecordingOrchestrator


@pytest.mark.parametrize(
    "worker_name, extra_args",
    [
        ("rebuild_relationships_worker", ()),
        ("promote_domains_worker", (["B", "C"],)),
        ("generate_decision_rationale_worker", ()),
    ],
)
def test_spawned_solution_worker_runs_in_the_solutions_organisation(
    two_orgs_with_solutions, recording_orchestrator, worker_name, extra_args
):
    from app.tasks import rebuild_tasks

    org_a, org_b, sol_a, sol_b = two_orgs_with_solutions
    worker = getattr(rebuild_tasks, worker_name)

    worker(os.getcwd(), sol_b, *extra_args)
    worker(os.getcwd(), sol_a, *extra_args)

    seen = recording_orchestrator.seen
    assert seen, "the worker never reached the orchestrator"
    for entry in seen:
        expected_org = org_b if entry["solution_id"] == sol_b else org_a
        assert entry["org"] == expected_org, entry
        own = sol_b if expected_org == org_b else sol_a
        other = sol_a if own == sol_b else sol_b
        assert own in entry["visible"], entry
        assert other not in entry["visible"], (
            "a worker for one organisation read another organisation's solution: %s" % entry
        )


def test_worker_for_a_missing_solution_does_nothing(
    two_orgs_with_solutions, recording_orchestrator
):
    from app.tasks.rebuild_tasks import rebuild_relationships_worker

    rebuild_relationships_worker(os.getcwd(), 2_000_000_000)
    assert recording_orchestrator.seen == []


def test_identity_map_does_not_carry_org_a_row_into_org_b(
    app, two_orgs_with_solutions, recording_orchestrator
):
    """Two organisations' workers in one app context: B's ``get()`` of A's row is None."""
    from app.models.solution_models import Solution
    from app.tasks.rebuild_tasks import _in_solution_tenant

    org_a, org_b, sol_a, sol_b = two_orgs_with_solutions
    got = {}

    with app.app_context():
        # A loads its own row: it is now in the identity map.
        got["a_own"] = _in_solution_tenant(
            sol_a, lambda orch: Solution.query.get(sol_a) is not None
        )
        # B, straight after, asks for A's row by primary key.
        got["b_reads_a"] = _in_solution_tenant(
            sol_b, lambda orch: Solution.query.get(sol_a)
        )
        got["b_own"] = _in_solution_tenant(
            sol_b, lambda orch: getattr(Solution.query.get(sol_b), "id", None)
        )

    assert got["a_own"] is True
    assert got["b_reads_a"] is None, "organisation B was handed organisation A's cached solution"
    assert got["b_own"] == sol_b


# --------------------------------------------------------------- batch import task


class _RecordingProcessor:
    seen: list = []

    def process_batch(self, batch_id):
        # The task loop retries a batch that stays queued; stop a broken
        # stand-in from spinning forever instead of failing.
        if len(type(self).seen) > 4:
            raise KeyboardInterrupt("batch stand-in called repeatedly")
        from app.models.application_portfolio import ApplicationComponent
        from app.models.batch_import import BatchImportBatch, BatchStatus

        type(self).seen.append(
            {
                "org": getattr(g, "current_org_id", None),
                "components": sorted(c.name for c in ApplicationComponent.query.all()),
            }
        )
        from app import db

        batch = db.session.get(BatchImportBatch, batch_id)
        batch.status = BatchStatus.SKIPPED  # an allowed move out of "queued"
        db.session.commit()


def _job_for(db_session, org_id, label):
    from app.models import User
    from app.models.batch_import import (
        BatchImportBatch,
        BatchImportJob,
        BatchJobStatus,
        BatchStatus,
    )

    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Import",
        last_name=label,
        organization_id=org_id,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    job = BatchImportJob(
        user_id=user.id,
        filename=f"{label}.csv",
        total_batches=1,
        status=BatchJobStatus.PROCESSING,
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        BatchImportBatch(job_id=job.id, batch_number=1, status=BatchStatus.QUEUED)
    )
    db_session.flush()
    return job.id


def test_batch_import_task_runs_in_the_job_owners_organisation(
    app, db_session, make_org, monkeypatch
):
    from app.models.application_portfolio import ApplicationComponent
    from app.services import batch_processor_service
    from app.tasks.import_tasks import _process_job_batches

    org_a, org_b = make_org("ia"), make_org("ib")
    tag = uuid.uuid4().hex[:8]
    db_session.add(ApplicationComponent(name=f"A-{tag}", organization_id=org_a.id))
    db_session.add(ApplicationComponent(name=f"B-{tag}", organization_id=org_b.id))
    job_b = _job_for(db_session, org_b.id, "b")
    job_a = _job_for(db_session, org_a.id, "a")
    db_session.commit()

    _RecordingProcessor.seen = []
    monkeypatch.setattr(batch_processor_service, "BatchProcessorService", _RecordingProcessor)

    # As a Celery worker runs it: an app context and no tenant.
    with app.app_context():
        assert getattr(g, "current_org_id", None) is None
        result_b = _process_job_batches(job_b)
        result_a = _process_job_batches(job_a)

    assert result_b["success"] and result_a["success"], (result_b, result_a)
    first, second = _RecordingProcessor.seen
    assert first["org"] == org_b.id
    assert f"B-{tag}" in first["components"] and f"A-{tag}" not in first["components"]
    assert second["org"] == org_a.id
    assert f"A-{tag}" in second["components"] and f"B-{tag}" not in second["components"]

    # A job that does not exist is refused before anything runs unscoped.
    with app.app_context():
        missing = _process_job_batches(2_000_000_000)
    assert missing["success"] is False
    assert len(_RecordingProcessor.seen) == 2
