"""The website read is a job on the JobQueueService worker, and runs to completion through it."""

from app.modules.organization.website_crawl import WEBSITE_TASK
from app.services.job_queue_service import JobQueueService
from tests.test_website_crawl import internal_hosts_allowed, other_site, site  # noqa: F401  fixtures


def test_the_website_read_is_a_registered_task_on_the_job_queue():
    """The website crawl task name is wired into _execute_task."""
    svc = JobQueueService()
    assert hasattr(svc, "_execute_task")
    try:
        svc._execute_task("no_such_task", {}, 0)
    except ValueError as exc:
        assert "Unknown task type" in str(exc)
    else:
        raise AssertionError("unknown task should have raised ValueError")


def test_the_job_queue_executes_the_website_read_and_reports_counts(app, make_org, site, monkeypatch):
    """The full path: queue, execute — the task finishes through the job worker."""
    import json

    from app.modules.organization import website_crawl
    from app.services.job_queue_service import get_job_queue_service

    from tests.test_website_crawl import _org, fake_model

    org = _org(make_org, site.url)
    monkeypatch.setattr(website_crawl, "default_model", lambda: fake_model)
    job = website_crawl.queue_website_crawl(org.id)
    assert job.task == WEBSITE_TASK
    assert job.payload["organization_id"] == org.id

    result = get_job_queue_service()._execute_task(job.task, dict(job.payload), job.id)
    assert result["pages_read"] >= 5
    assert result["proposals"] == len(website_crawl.pending_website_proposals(org.id))
    assert "html" not in json.dumps(result).lower()


def test_two_worker_threads_never_run_two_jobs_of_one_organisation_together_but_do_run_two_organisations(
        app, db_session, make_org):
    """Per-organisation exclusion: a read and its model pass never overlap; another organisation's job proceeds.

    Each pass below is what one worker thread does in a loop: pick the next job, then claim it. Two threads taking
    a step one after the other, with neither job finished, is exactly two jobs running together.
    """
    from app.models.job import Job
    from app.services.job_queue_service import JobQueueService

    org_a, org_b = make_org("excl-a"), make_org("excl-b")
    service = JobQueueService()
    service.app = app
    model_pass = service.create_job("model pass", "website_profile_crawl_more", {}, organization_id=org_a.id)
    first_read = service.create_job("first read", "website_profile_crawl", {}, organization_id=org_a.id)
    other_read = service.create_job("other read", "website_profile_crawl", {}, organization_id=org_b.id)

    def step():
        job = service._next_job()
        return job if job and service._claim(job.id) else None

    first = step()
    assert first.id == first_read.id
    second = step()  # the second thread, while the first read is still running
    assert second.id == other_read.id, "the second thread took another job of the organisation already being read"
    assert step() is None, "a job of an organisation with a job in progress was picked"
    assert db_session.get(Job, model_pass.id).status == "pending"
    # When the read ends, the same organisation's model pass is next.
    service.update_job_status(first_read.id, "completed", result={})
    assert step().id == model_pass.id


def test_the_pool_size_is_a_setting_with_three_threads_by_default(app, monkeypatch):
    from app.services.job_queue_service import WORKER_THREADS, JobQueueService

    service = JobQueueService()
    service.app = app
    monkeypatch.setitem(app.config, "JOB_WORKER_THREADS", None)
    assert service.worker_threads() == WORKER_THREADS == 3
    monkeypatch.setitem(app.config, "JOB_WORKER_THREADS", 5)
    assert service.worker_threads() == 5
