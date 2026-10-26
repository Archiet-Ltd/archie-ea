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
