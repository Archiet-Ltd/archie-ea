"""The background job worker, started the way the application starts it, runs a queued job.

The person: a founder who has just given their company website. The reading is a queued job; if the worker
that runs queued jobs cannot start, nothing is ever read and the sign-up page waits for ever.

The worker is a thread. Begun while the application is still being assembled, its look at the queue can land
in the middle of a model module's import (the first class defined, the class it names not yet); the mapper
error that follows stays for the life of the process and no job runs again. A busy machine makes that happen
by chance, so the test checks the cause: a fresh process starts the application as production does, the
number of models mapped is noted when the worker begins and when the application is up, the two must agree,
and a website reading queued then must finish.
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest
from sqlalchemy import text

from tests.test_website_crawl import ABOUT, CONTACT, HOME, PRODUCTS, ROBOTS, _html, _Site

CHILD = textwrap.dedent(
    r"""
    import sys, time
    # Note how many models are mapped at the moment the worker begins.
    import app.services.job_queue_service as queue
    begin = queue.JobQueueService.start_worker
    seen = {}
    def start_worker(self):
        from app import db
        seen["at_start"] = len(list(db.Model.registry.mappers))
        return begin(self)
    queue.JobQueueService.start_worker = start_worker

    import manage  # the production entry point: gunicorn manage:app
    from app import db
    from sqlalchemy import text
    from app.modules.organization.website_crawl import queue_website_crawl
    org_id = int(sys.argv[1])
    # The application is up and the worker has been idling since it began; now the person's reading is queued.
    with manage.app.app_context():
        job_id = queue_website_crawl(org_id).id
    deadline = time.time() + 150
    status = None
    with manage.app.app_context():
        while time.time() < deadline:
            status = db.session.execute(text("SELECT status FROM jobs WHERE id = :i"), {"i": job_id}).scalar()
            db.session.rollback()
            if status in ("completed", "failed"):
                break
            time.sleep(1)
    from app import db as _db
    print("MODELS_AT_WORKER_START=%s" % seen.get("at_start"))
    print("MODELS_WHEN_UP=%s" % len(list(_db.Model.registry.mappers)))
    print("JOB_ID=%s" % job_id)
    print("JOB_STATUS=%s" % status)
    sys.stdout.flush()
    import os
    os._exit(0)
    """
)


@pytest.fixture
def consenting_company(app):
    """A committed organisation that consented to its site being read, with its site up."""
    from app import db
    from app.models.organization import Organization
    from app.modules.account.services import onboarding_service as onboarding
    import uuid

    from tests.smoke.test_connect_github_to_cited_answer_journey import remove_organisations

    site = _Site({"/": _html(HOME % {"linkedin": "https://www.linkedin.com/company/acme-robotics", "other": "http://127.0.0.1:9"}),
                  "/about": _html(ABOUT), "/products": _html(PRODUCTS), "/contact": _html(CONTACT),
                  "/robots.txt": (200, "text/plain", ROBOTS)})
    previous = app.config.get("ALLOW_INTERNAL_WEBHOOK_TARGETS")
    app.config["ALLOW_INTERNAL_WEBHOOK_TARGETS"] = True  # the company site is on loopback
    with app.app_context():
        suffix = uuid.uuid4().hex[:10]
        org = Organization(name="Worker %s" % suffix, slug="worker-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        org_id = org.id
        onboarding.save_website(org, site.url, True, None)
        # The process under test queues the reading itself once it is up; remove the one sign-up queued here.
        db.session.execute(text("DELETE FROM jobs WHERE (payload->>'organization_id')::int = :o"), {"o": org_id})
        db.session.commit()
    app.config["ALLOW_INTERNAL_WEBHOOK_TARGETS"] = previous
    yield site, org_id
    site.stop()
    with app.app_context():
        db.session.execute(text("DELETE FROM jobs WHERE (payload->>'organization_id')::int = :o"), {"o": org_id})
        db.session.commit()
    remove_organisations(app, [org_id])


def test_a_worker_started_as_production_starts_it_reads_the_queued_website(app, consenting_company):
    site, org_id = consenting_company
    env = dict(os.environ)
    env.update({
        "FLASK_CONFIG": "testing", "RUN_JOB_WORKER": "1", "ALLOW_INTERNAL_WEBHOOK_TARGETS": "1",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    })
    env.pop("DISABLE_JOB_QUEUE_WORKER", None)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    done = subprocess.run(
        [sys.executable, "-c", CHILD, str(org_id)], cwd=root, env=env, capture_output=True, text=True, timeout=420,
    )
    out = done.stdout + done.stderr
    assert "JOB_STATUS=completed" in done.stdout, "the queued reading did not finish:\n" + out[-4000:]
    assert "failed to locate a name" not in out, out[-4000:]
    at_start = int(done.stdout.split("MODELS_AT_WORKER_START=")[1].split()[0])
    when_up = int(done.stdout.split("MODELS_WHEN_UP=")[1].split()[0])
    assert at_start == when_up, (
        "the worker began with %d models mapped and the application ended with %d: it ran while models were "
        "still being loaded" % (at_start, when_up))
    job_id = int(done.stdout.split("JOB_ID=")[1].split()[0])
    with app.app_context():
        from app import db

        result = db.session.execute(text("SELECT result FROM jobs WHERE id = :i"), {"i": job_id}).scalar()
        db.session.rollback()
    assert (result or {}).get("pages_read", 0) >= 1, "the job finished but read no page: %s" % json.dumps(result)
