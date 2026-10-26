"""Who runs the background job worker: every configuration but testing, and a preview that asks for it."""
import pytest

from app._bootstrap.services import job_worker_wanted


@pytest.mark.parametrize(
    "config_name,environ,expected",
    [
        ("production", {}, True),
        ("development", {}, True),
        ("smoke", {}, True),
        ("testing", {}, False),
        ("testing", {"RUN_JOB_WORKER": "0"}, False),
        ("testing", {"RUN_JOB_WORKER": "1"}, True),
        ("production", {"DISABLE_JOB_QUEUE_WORKER": "1"}, False),
        ("testing", {"RUN_JOB_WORKER": "1", "DISABLE_JOB_QUEUE_WORKER": "1"}, False),
    ],
)
def test_the_worker_runs_where_it_should(config_name, environ, expected):
    assert job_worker_wanted(config_name, environ) is expected


def test_the_test_suite_itself_never_starts_a_worker(monkeypatch):
    monkeypatch.delenv("RUN_JOB_WORKER", raising=False)
    import os

    assert job_worker_wanted("testing", os.environ) is False
