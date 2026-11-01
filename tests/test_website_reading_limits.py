"""A company website is read once, not on every save: one open reading, one reading per address a day, three a day."""
from datetime import datetime, timedelta

import pytest

from app import db
from app.models.job import Job
from app.modules.account.services import onboarding_service as onboarding
from app.modules.organization.website_crawl import WEBSITE_RETRY_TASK, WEBSITE_TASK
from app.services.job_queue_service import get_job_queue_service

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture(autouse=True)
def internal_hosts_allowed(app):
    """The addresses are on loopback; the outbound guard allows that only where configured."""
    previous = app.config.get("ALLOW_INTERNAL_WEBHOOK_TARGETS")
    app.config["ALLOW_INTERNAL_WEBHOOK_TARGETS"] = True
    yield
    app.config["ALLOW_INTERNAL_WEBHOOK_TARGETS"] = previous

LIMIT_SENTENCE = "You can read a website three times a day. Try again tomorrow, or confirm what we found."


def _org(make_org, name):
    org = make_org(name)
    db.session.flush()
    return org


def _jobs(org, task=WEBSITE_TASK):
    return get_job_queue_service().list_jobs(org.id, limit=100, task=task)


def _finish(job):
    job.status = "completed"
    db.session.commit()


def test_saving_the_same_address_again_while_a_reading_is_open_starts_no_second_reading(make_org):
    org = _org(make_org, "open-reading")
    onboarding.save_website(org, "127.0.0.1:11", True, None)
    notes = [onboarding.save_website(org, "127.0.0.1:11", True, None) for _ in range(4)]
    assert len(_jobs(org)) == 1
    assert all("already under way" in note for note in notes)


def test_a_changed_address_while_a_reading_is_open_returns_that_reading(make_org):
    org = _org(make_org, "open-other")
    onboarding.save_website(org, "127.0.0.1:11", True, None)
    note = onboarding.save_website(org, "127.0.0.1:12", True, None)
    assert len(_jobs(org)) == 1
    assert "already under way" in note


def test_the_same_address_read_within_a_day_is_not_read_again(make_org):
    org = _org(make_org, "same-day")
    onboarding.save_website(org, "127.0.0.1:11", True, None)
    _finish(_jobs(org)[0])
    note = onboarding.save_website(org, "127.0.0.1:11", True, None)
    assert len(_jobs(org)) == 1
    assert "within the last day" in note


def test_the_same_address_is_read_again_after_a_day(make_org):
    org = _org(make_org, "next-day")
    onboarding.save_website(org, "127.0.0.1:11", True, None)
    job = _jobs(org)[0]
    job.created_at = datetime.utcnow() - timedelta(hours=25)
    _finish(job)
    assert onboarding.save_website(org, "127.0.0.1:11", True, None) is None
    assert len(_jobs(org)) == 2


def test_a_different_address_after_the_first_finished_starts_a_second_reading(make_org):
    org = _org(make_org, "changed")
    onboarding.save_website(org, "127.0.0.1:11", True, None)
    _finish(_jobs(org)[0])
    assert onboarding.save_website(org, "127.0.0.1:12", True, None) is None
    assert len(_jobs(org)) == 2


def test_the_fourth_reading_of_the_day_is_refused_with_a_plain_sentence_and_keeps_the_last_address(make_org):
    org = _org(make_org, "daily")
    for host in ("127.0.0.1:13", "127.0.0.1:14", "127.0.0.1:15"):
        onboarding.save_website(org, host, True, None)
        _finish(_jobs(org)[0])
    with pytest.raises(onboarding.ReadingLimited) as refused:
        onboarding.save_website(org, "127.0.0.1:16", True, None)
    assert str(refused.value) == LIMIT_SENTENCE
    assert len(_jobs(org)) == 3
    assert onboarding._onboarding(org)["website"]["url"] == "https://127.0.0.1:15"


def test_the_limit_of_one_organisation_does_not_stop_another(make_org):
    busy, other = _org(make_org, "busy"), _org(make_org, "other")
    for host in ("127.0.0.1:13", "127.0.0.1:14", "127.0.0.1:15"):
        onboarding.save_website(busy, host, True, None)
        _finish(_jobs(busy)[0])
    assert onboarding.save_website(other, "127.0.0.1:13", True, None) is None
    assert len(_jobs(other)) == 1 and len(_jobs(busy)) == 3


def test_a_retry_counts_toward_the_day_and_never_stacks(make_org):
    from app.modules.organization.website_crawl.service import _queue_retry

    org = _org(make_org, "retry")
    onboarding.save_website(org, "127.0.0.1:11", True, None)
    _finish(_jobs(org)[0])
    first = _queue_retry(org.id, 1)
    assert _queue_retry(org.id, 1) == first
    assert len(_jobs(org, WEBSITE_RETRY_TASK)) == 1
    for job in _jobs(org, WEBSITE_RETRY_TASK):
        job.status = "completed"
    db.session.commit()
    _queue_retry(org.id, 2)
    # one reading and two retries are the day's three
    with pytest.raises(onboarding.ReadingLimited):
        onboarding.save_website(org, "127.0.0.1:12", True, None)
