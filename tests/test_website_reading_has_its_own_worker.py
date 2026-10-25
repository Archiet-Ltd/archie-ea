"""Reading a website takes minutes, so it never holds up the import a person is waiting for."""

from app.modules.organization.website_crawl import WEBSITE_TASK
from app.services.job_queue_service import JobQueueService


def test_the_website_reading_is_a_slow_task_the_main_worker_skips():
    assert WEBSITE_TASK in JobQueueService.SLOW_TASKS
    assert "onboarding_github_import" not in JobQueueService.SLOW_TASKS
    assert "onboarding_jira_import" not in JobQueueService.SLOW_TASKS
