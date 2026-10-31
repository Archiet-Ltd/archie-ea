"""The reading of a company's website is visible while it runs, ends in a next step, and says plainly when it cannot read.

What a person waits on at sign-up is a sentence: how many pages of the limit are read, which page is
being read, and then either "confirm what we read" (also posted to the bell) or why nothing was read
and what to do instead. Each organisation sees only its own reading.
"""
import json
from types import SimpleNamespace

import pytest

from tests.test_website_crawl import (
    ABOUT, APP_SHELL, CONTACT, HOME, LINKEDIN, PRODUCTS, ROBOTS, _html, _org, _Site, fake_model,
)  # noqa: F401  (fixtures: site, other_site and the loopback allowance come with the module)
from tests.test_website_crawl import internal_hosts_allowed, other_site, site  # noqa: F401

pytestmark = pytest.mark.usefixtures("internal_hosts_allowed")


def _user(org, email):
    from app import db
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(email=email, first_name="Sam", last_name="Founder", organization_id=org.id,
                enterprise_role="enterprise_architect", confirmed=True)
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "password"
    db.session.add(user)
    db.session.commit()
    return user


def _signed_up(make_org, website, email):
    """An organisation whose owner gave ``website`` with consent, and the person who gave it."""
    from app import db
    from app.modules.account.services import onboarding_service as onboarding

    org = make_org("reading")
    db.session.flush()
    user = _user(org, email)
    onboarding.save_website(org, website, True, user.id)
    # a reading runs in a tenant scope that detaches loaded rows, so the tests hold ids and a copy of the settings
    return SimpleNamespace(id=org.id, settings=json.loads(json.dumps(org.settings or {}))), user.id


def _person(user_id):
    from app import db
    from app.models.user import User

    return db.session.get(User, user_id)


def _job(org_id, status, result=None):
    from app import db
    from app.models.job import Job
    from app.modules.organization.website_crawl import WEBSITE_TASK

    for old in Job.query.filter(Job.task == WEBSITE_TASK, Job.payload["organization_id"].as_string() == str(org_id)).all():
        db.session.delete(old)
    job = Job(name="Read the company website", task=WEBSITE_TASK, payload={"organization_id": org_id},
              status=status, result=result)
    db.session.add(job)
    db.session.commit()
    return job


def _state(org):
    from app.modules.account.services import onboarding_service as onboarding

    return onboarding.website_state(org)


# ---------------------------------------------------------------- progress states

def test_a_reading_in_progress_says_pages_read_of_the_limit_and_the_page_being_read(app, make_org):
    org, _ = _signed_up(make_org, "https://acme.example", "prog-a@example.com")
    with app.test_request_context():
        _job(org.id, "in_progress", {"progress": {"stage": "reading", "pages_read": 3, "limit": 30,
                                                  "current": "https://acme.example/about"}})
        state = _state(org)
    assert state["state"] == "reading"
    assert state["text"] == "Read 3 of up to 30 pages. Reading now: acme.example/about."
    assert state["progress"] == {"read": 3, "limit": 30, "percent": 10}


def test_the_first_moments_name_the_site_and_the_limit_rather_than_showing_a_bare_spinner(app, make_org):
    org, _ = _signed_up(make_org, "https://acme.example", "prog-b@example.com")
    with app.test_request_context():
        _job(org.id, "in_progress", {"progress": {"stage": "reading", "pages_read": 0, "limit": 30,
                                                  "current": "https://acme.example"}})
        assert _state(org)["text"] == "Opening acme.example. Up to 30 pages will be read."
        _job(org.id, "in_progress", None)
        assert _state(org)["text"] == "Opening acme.example. Up to 30 pages will be read."


def test_after_the_pages_the_line_says_what_is_happening_now(app, make_org):
    org, _ = _signed_up(make_org, "https://acme.example", "prog-c@example.com")
    with app.test_request_context():
        _job(org.id, "in_progress", {"progress": {"stage": "working_out", "pages_read": 5, "limit": 30, "current": None}})
        state = _state(org)
    assert state["text"] == "Read 5 of up to 30 pages. Working out what they say; this takes a minute."


def test_a_waiting_job_is_not_reported_as_reading(app, make_org):
    org, _ = _signed_up(make_org, "https://acme.example", "prog-d@example.com")
    with app.test_request_context():
        _job(org.id, "pending")
        state = _state(org)
    assert state["state"] == "waiting" and "Reading starts in a moment" in state["text"]


# ---------------------------------------------------------------- a real reading reports as it goes

def test_a_real_reading_reports_each_page_and_ends_with_a_next_step_and_a_bell(app, make_org, site, monkeypatch):
    from app.models.models import Notification
    from app.modules.organization import website_crawl
    from app.modules.organization.website_crawl import confirm
    from app.services.job_queue_service import get_job_queue_service

    org, user_id = _signed_up(make_org, site.url, "prog-e@example.com")
    seen = []
    real = website_crawl.service._job_progress

    def spy(job_id):
        write = real(job_id)

        def both(state):
            seen.append(dict(state))
            write(state)
        return both

    monkeypatch.setattr(website_crawl.service, "_job_progress", spy)
    monkeypatch.setattr(website_crawl, "default_model", lambda: fake_model)
    job = website_crawl.queue_website_crawl(org.id)
    job_id, task, payload = job.id, job.task, dict(job.payload)
    result = get_job_queue_service()._execute_task(task, payload, job_id)
    get_job_queue_service().update_job_status(job_id, "completed", result=result)

    reading = [s for s in seen if s["stage"] == "reading"]
    assert [s["pages_read"] for s in reading][0] == 0 and max(s["pages_read"] for s in reading) >= 5
    assert {s["limit"] for s in seen} == {30}
    assert any(s["stage"] == "facts_ready" for s in seen)
    assert any(s["current"] and s["current"].endswith("/about") for s in reading)

    with app.test_request_context():
        state = _state(org)
        facts, _ = confirm.proposals(org.id)
    assert state["state"] == "done"
    # the figure on the sources step is the number of facts on the confirm screen
    assert state["text"] == ("Read %d pages; %d facts wait for you to confirm in the next step. "
                             "More facts follow in a few minutes, as we finish working out what your pages say.") % (result["pages_read"], len(facts))
    assert state["next"] == {"label": "Confirm what we read", "url": "/account/welcome?step=3"}

    bell = Notification.query.filter_by(user_id=user_id, event_type="website_read").all()
    assert len(bell) == 1 and bell[0].organization_id == org.id
    assert bell[0].url == "/account/welcome?step=3" and bell[0].read is False
    assert bell[0].message == "We read your website and %d facts are ready for you to confirm." % len(facts)
    assert "html" not in json.dumps(result).lower()


# ---------------------------------------------------------------- a site that cannot be read

def _run(org, monkeypatch, model=fake_model):
    from app.modules.organization import website_crawl
    from app.services.job_queue_service import get_job_queue_service

    monkeypatch.setattr(website_crawl, "default_model", lambda: model)
    job = website_crawl.queue_website_crawl(org.id)
    job_id, task, payload = job.id, job.task, dict(job.payload)
    result = get_job_queue_service()._execute_task(task, payload, job_id)
    get_job_queue_service().update_job_status(job_id, "completed", result=result)
    return result


def _plain(state):
    assert state["state"] == "failed"
    for word in ("Traceback", "CrawlRefused", "Error:", "Exception", "None", "null"):
        assert word not in state["text"], state["text"]
    assert state["text"].endswith("You can connect GitHub or Jira instead, or look around the sample company.")
    assert [i["label"] for i in state["instead"]] == ["Connect GitHub", "Connect Jira", "See the sample company"]
    return state["text"]


def test_a_site_whose_robots_file_excludes_everyone_is_said_so_with_what_to_do_instead(app, make_org, monkeypatch):
    blocked = _Site({"/": _html("<html><body>Hello</body></html>"), "/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /\n")})
    try:
        org, _ = _signed_up(make_org, blocked.url, "fail-a@example.com")
        result = _run(org, monkeypatch)
        assert result == {"success": False, "reason": "robots", "status": None}
        assert "/" not in blocked.hits  # it was never asked for a page
        with app.test_request_context():
            text = _plain(_state(org))
        assert "robots.txt" in text and "stay out" in text
    finally:
        blocked.stop()


def test_a_site_that_does_not_answer_is_said_so(app, make_org, monkeypatch):
    gone = _Site({})
    url = gone.url
    gone.stop()
    org, _ = _signed_up(make_org, url, "fail-b@example.com")
    result = _run(org, monkeypatch)
    assert result["success"] is False and result["reason"] == "no_answer"
    with app.test_request_context():
        assert "did not answer when we tried to read it" in _plain(_state(org))


def test_a_site_that_answers_with_an_error_says_which(app, make_org, monkeypatch):
    refusing = _Site({"/": (403, "text/html", "<p>no</p>"), "/robots.txt": (404, "text/plain", "")})
    try:
        org, _ = _signed_up(make_org, refusing.url, "fail-c@example.com")
        result = _run(org, monkeypatch)
        assert result["reason"] == "no_answer" and result["status"] == 403
        with app.test_request_context():
            assert "answered with an error (status 403) when we tried to read it" in _plain(_state(org))
    finally:
        refusing.stop()


def test_a_script_only_page_the_browser_could_not_render_says_so(app, make_org, monkeypatch):
    from app.modules.organization.website_crawl import service

    def no_browser(self, url, timeout):
        raise RuntimeError("no browser here")

    monkeypatch.setattr(service.Browser, "render", no_browser)
    shell = _Site({"/": _html("<html><head><title>Portal</title></head><body><div id='root'></div><script>x()</script></body></html>"),
                   "/robots.txt": (404, "text/plain", "")})
    try:
        org, _ = _signed_up(make_org, shell.url, "fail-d@example.com")
        result = _run(org, monkeypatch, model=lambda prompt: "{}")
        assert result["reason"] == "script_only"
        with app.test_request_context():
            assert "builds its pages with scripts, and the browser we use could not show them" in _plain(_state(org))
    finally:
        shell.stop()


def test_a_job_that_crashed_is_said_plainly_without_the_error(app, make_org):
    org, _ = _signed_up(make_org, "https://acme.example", "fail-e@example.com")
    with app.test_request_context():
        job = _job(org.id, "failed")
        job.error = "ValueError: secret internals"
        from app import db
        db.session.commit()
        text = _plain(_state(org))
    assert text.startswith("Something went wrong while reading acme.example, and nothing was added to your model.")
    assert "secret internals" not in text


# ---------------------------------------------------------------- two organisations

def test_one_organisation_never_sees_or_is_told_about_anothers_reading(app, make_org, site, client, login_as, monkeypatch):
    from app.models.models import Notification

    own, own_user_id = _signed_up(make_org, site.url, "iso-a@example.com")
    other, other_user_id = _signed_up(make_org, "https://other.example", "iso-b@example.com")
    _run(own, monkeypatch)
    _job(other.id, "in_progress", {"progress": {"stage": "reading", "pages_read": 7, "limit": 30,
                                                "current": "https://other.example/team"}})

    login_as(client, _person(own_user_id))
    mine = client.get("/account/welcome/status").get_json()
    site_row = next(r for r in mine["sources"] if r["key"] == "website")
    assert site_row["state"] == "done" and "other.example" not in json.dumps(mine)

    login_as(client, _person(other_user_id))
    theirs = client.get("/account/welcome/status").get_json()
    their_row = next(r for r in theirs["sources"] if r["key"] == "website")
    assert their_row["state"] == "reading"
    assert their_row["text"] == "Read 7 of up to 30 pages. Reading now: other.example/team."
    assert site.url.split("//")[1] not in json.dumps(theirs)

    assert Notification.query.filter_by(user_id=own_user_id, event_type="website_read").count() == 1
    assert Notification.query.filter_by(user_id=other_user_id, event_type="website_read").count() == 0
    # the sources the organisation did not give stay "not given" for it
    assert all(r["state"] == "not_given" for r in theirs["sources"] if r["key"] != "website")


def test_the_status_page_carries_the_progress_and_the_next_step_for_the_script(app, make_org, client, login_as):
    org, user_id = _signed_up(make_org, "https://acme.example", "iso-c@example.com")
    _job(org.id, "in_progress", {"progress": {"stage": "reading", "pages_read": 15, "limit": 30,
                                              "current": "https://acme.example/products"}})
    login_as(client, _person(user_id))
    row = next(r for r in client.get("/account/welcome/status").get_json()["sources"] if r["key"] == "website")
    assert row["progress"] == {"read": 15, "limit": 30, "percent": 50}


def test_a_waiting_read_says_other_readings_come_first_only_when_every_worker_is_busy(app, make_org, monkeypatch):
    from app.services.job_queue_service import get_job_queue_service

    org, _ = _signed_up(make_org, "https://acme.example", "prog-busy@example.com")
    service = get_job_queue_service()
    with app.test_request_context():
        _job(org.id, "pending")
        monkeypatch.setattr(service, "all_workers_busy", lambda: True)
        busy = _state(org)["text"]
        monkeypatch.setattr(service, "all_workers_busy", lambda: False)
        free = _state(org)["text"]
    assert "read first" in busy and "starts in a moment" not in busy, busy
    assert "Reading starts in a moment" in free and "read first" not in free, free
