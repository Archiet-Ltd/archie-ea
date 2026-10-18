"""The worker pool has four named queues, and a job keeps its organisation on whichever it is on."""
import pathlib

import pytest

fakeredis = pytest.importorskip("fakeredis")
rq = pytest.importorskip("rq")

REPO = pathlib.Path(__file__).resolve().parent.parent
FOUR = ("interactive", "agent", "ingestion", "compute")


@pytest.fixture
def redis_conn(app):
    from app.services.job_queue_service import get_job_queue_service

    service = get_job_queue_service()
    previous = service._redis
    conn = fakeredis.FakeStrictRedis()
    service.use_rq_connection(conn)
    yield conn
    service.use_rq_connection(previous)


def count_applications_visible_to(organization_id):
    """A tenant-scoped job body: count the applications its own organisation can see."""
    from app.jobs.tenant_safe_job import tenant_scope
    from app.models.application_portfolio import ApplicationComponent

    with tenant_scope(organization_id):
        return ApplicationComponent.query.count()


def test_there_are_exactly_four_queues_and_interactive_drains_first():
    from app.services.job_queue_service import QUEUE_NAMES

    assert set(QUEUE_NAMES) == set(FOUR)
    assert QUEUE_NAMES[0] == "interactive"


def test_a_job_lands_on_the_queue_it_names_and_on_no_other(app, redis_conn):
    from app.flask_email import send_email
    from app.services.job_queue_service import get_job_queue_service

    service = get_job_queue_service()
    for name in FOUR:
        service.enqueue(name, send_email, "a@example.test", "subject", "tpl")
        assert rq.Queue(name, connection=redis_conn).count == 1
    assert rq.Queue("default", connection=redis_conn).count == 0


def test_a_queue_outside_the_four_is_refused(app, redis_conn):
    from app.flask_email import send_email
    from app.services.job_queue_service import get_job_queue_service

    with pytest.raises(ValueError):
        get_job_queue_service().enqueue("default", send_email, "a@example.test", "s", "t")


def test_the_worker_listens_on_all_four_unless_narrowed():
    from app.services.job_queue_service import resolve_worker_queues

    assert resolve_worker_queues() == ["interactive", "agent", "ingestion", "compute"]
    assert resolve_worker_queues(["compute", "interactive"]) == ["interactive", "compute"]
    with pytest.raises(ValueError):
        resolve_worker_queues(["default"])


def test_one_worker_drains_all_four_queues(app, redis_conn, db_session, make_org):
    from app.services.job_queue_service import get_job_queue_service, resolve_worker_queues

    org = make_org("drain")
    service = get_job_queue_service()
    jobs = [service.enqueue(name, count_applications_visible_to, org.id) for name in FOUR]

    worker = rq.SimpleWorker(
        [service.get_queue(n) for n in resolve_worker_queues()], connection=redis_conn)
    worker.work(burst=True)

    assert all(job.get_status(refresh=True) == "finished" for job in jobs)


def test_a_compute_job_acts_on_its_own_organisation_and_not_another(
        app, redis_conn, db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent
    from app.services.job_queue_service import QUEUE_COMPUTE, get_job_queue_service

    org_a, org_b = make_org("a"), make_org("b")
    for n in range(3):
        db_session.add(ApplicationComponent(name=f"A{n}", organization_id=org_a.id))
    db_session.add(ApplicationComponent(name="B0", organization_id=org_b.id))
    # tenant_scope starts from a clean session, so the rows must be committed first.
    db_session.commit()

    service = get_job_queue_service()
    job_a = service.enqueue(QUEUE_COMPUTE, count_applications_visible_to, org_a.id)
    job_b = service.enqueue(QUEUE_COMPUTE, count_applications_visible_to, org_b.id)
    rq.SimpleWorker([service.get_queue(QUEUE_COMPUTE)], connection=redis_conn).work(burst=True)

    assert job_a.get_status(refresh=True) == "finished"
    assert job_a.return_value() == 3
    assert job_b.return_value() == 1


def test_outgoing_e_mail_goes_on_the_interactive_queue(app, redis_conn, monkeypatch):
    from app.modules.admin.services.admin_user_service import AdminUserService
    from app.modules.admin.v2.services.admin_user_service_v2 import AdminUserService as AdminUserServiceV2
    from app.modules.account.services import account_service

    AdminUserService.queue_email("a@example.test", "subject", "tpl")
    AdminUserServiceV2.queue_email("a@example.test", "subject", "tpl")
    account_service._queue_email("a@example.test", "subject", "tpl")

    assert rq.Queue("interactive", connection=redis_conn).count == 3
    assert rq.Queue("default", connection=redis_conn).count == 0


def test_e_mail_is_sent_in_the_request_when_rq_is_not_installed(app, monkeypatch):
    from app.services import job_queue_service as jq

    def refuse(self, name):
        raise jq.RqUnavailable("rq is not installed")

    monkeypatch.setattr(jq.JobQueueService, "get_queue", refuse)
    sent = []
    assert jq.enqueue_or_run("interactive", lambda *a: sent.append(a) or "done", 1, 2) == "done"
    assert sent == [(1, 2)]


def test_compliance_generation_is_queued_on_the_agent_queue():
    source = (REPO / "app/services/compliance/compliance_job_orchestrator.py").read_text()
    assert "QUEUE_AGENT" in source and "flask_rq" not in source


def test_nothing_enqueues_on_the_single_default_queue_any_more():
    offenders = [
        str(p.relative_to(REPO)) for p in (REPO / "app").rglob("*.py")
        if "flask_rq" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []
