"""A job cut off by a restart is queued again at worker start-up, and never blocks its organisation.

Journey: a person's website read is running when the server restarts; on the next start the read is queued again
before the first claim, and their sign-up step reads the sentence about the restart instead of waiting for ever.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.test_website_crawl import _org, internal_hosts_allowed, other_site, site  # noqa: F401

pytestmark = pytest.mark.usefixtures("db_session")


def _service(app):
    from app.services.job_queue_service import JobQueueService

    service = JobQueueService()
    service.app = app
    return service


def _claimed_by(db_session, org, task, worker, minutes=1):
    from app.models.job import Job

    job = Job(name="x", task=task, payload={"organization_id": org.id, "worker": worker}, status="in_progress",
              started_at=datetime.utcnow() - timedelta(minutes=minutes))
    db_session.add(job)
    db_session.commit()
    return job


def test_a_job_of_a_worker_that_no_longer_exists_is_queued_again_at_start_up_and_the_person_is_told(
        app, db_session, make_org, site, monkeypatch):
    from app.models.job import Job
    from app.models.organization import Organization
    from app.modules.account.services import onboarding_service

    org = _org(make_org, site.url)
    Job.query.filter_by(status="pending").update({"status": "cancelled"})  # the read that saving the site queued
    db_session.commit()
    dead = _claimed_by(db_session, org, "website_profile_crawl", "a-process-that-has-ended")
    service = _service(app)
    monkeypatch.setattr(service, "_worker_loop", lambda: None)
    service.start_worker()  # the sweep runs here, before any thread can claim
    service.stop_worker()
    db_session.expire_all()
    dead = db_session.get(Job, dead.id)
    assert dead.status == "failed" and dead.result["reason"] == "interrupted"
    pending = [j for j in Job.query.filter_by(task="website_profile_crawl", status="pending").all()
               if (j.payload or {}).get("organization_id") == org.id]
    assert len(pending) == 1, "the read was not queued again"
    assert "worker" not in pending[0].payload, "the new job carries the dead worker's token"
    with app.test_request_context("/"):
        state = onboarding_service.website_state(db_session.get(Organization, org.id))
    assert state["text"].startswith("The reading of ") and "cut off by a restart" in state["text"], state
    assert "queued to run again" in state["text"]


def test_a_claim_records_which_process_holds_the_job(app, db_session, make_org):
    from app.models.job import Job
    from app.services.job_queue_service import PROCESS_TOKEN

    org = make_org("sweep-claim")
    service = _service(app)
    job = service.create_job("read", "website_profile_crawl", {}, organization_id=org.id)
    assert service._claim(job.id) is True
    db_session.expire_all()
    assert db_session.get(Job, job.id).payload["worker"] == PROCESS_TOKEN


def test_a_job_this_process_holds_is_not_taken_back_at_start_up(app, db_session, make_org):
    from app.models.job import Job
    from app.services.job_queue_service import PROCESS_TOKEN

    org = make_org("sweep-own")
    service = _service(app)
    mine = _claimed_by(db_session, org, "website_profile_crawl", PROCESS_TOKEN)
    service.requeue_dead_worker_jobs()
    db_session.expire_all()
    assert db_session.get(Job, mine.id).status == "in_progress"


def test_a_stale_job_in_progress_does_not_block_the_organisations_next_job(app, db_session, make_org):
    org = make_org("sweep-block")
    service = _service(app)
    dead = _claimed_by(db_session, org, "website_profile_crawl", "gone")  # dead worker, started a minute ago
    nxt = service.create_job("model pass", "website_profile_crawl_more", {}, organization_id=org.id)
    assert service._next_job().id == nxt.id, "a job of a worker that no longer exists blocked its organisation"
    dead.status = "failed"
    db_session.commit()
    _claimed_by(db_session, org, "website_profile_crawl", None, minutes=20)  # past its 15 minutes, no worker recorded
    assert service._next_job().id == nxt.id, "a job running past its limit blocked its organisation"


def test_a_live_job_in_progress_still_holds_its_organisation(app, db_session, make_org):
    from app.services.job_queue_service import PROCESS_TOKEN

    org = make_org("sweep-live")
    service = _service(app)
    _claimed_by(db_session, org, "website_profile_crawl", PROCESS_TOKEN)
    service.create_job("model pass", "website_profile_crawl_more", {}, organization_id=org.id)
    assert service._next_job() is None


def test_the_stale_sweep_still_fails_a_hung_job_after_its_threshold(app, db_session, make_org):
    from app.models.job import Job
    org = make_org("sweep-hung")
    hung = _claimed_by(db_session, org, "github_first_sync", None, minutes=40)
    fresh = _claimed_by(db_session, org, "github_first_sync", None, minutes=5)
    assert _service(app).recover_stale_jobs() == 1
    db_session.expire_all()
    assert db_session.get(Job, hung.id).status == "failed" and db_session.get(Job, fresh.id).status == "in_progress"
