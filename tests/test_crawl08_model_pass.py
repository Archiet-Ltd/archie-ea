"""The website model pass answers, a second sign-up is not kept waiting, and no read is lost to a restart (crawl08).

QA-032: the first model took about four minutes and spent the whole deadline, so no other provider was tried.
QA-064: a read cut off by a restart stayed "in progress" for ever. QA-053: one worker thread, so a second
organisation's read waited for the first one's model pass. QA-052: an address inside a code block was taken as the
company's contact.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta

import pytest

from tests.test_website_crawl import _crawl, _org, fake_model, internal_hosts_allowed, other_site, site  # noqa: F401
from tests.test_website_crawl_retry import catalogue  # noqa: F401  (fixture: the live free list, offline)

pytestmark = pytest.mark.usefixtures("db_session")


# ----------------------------------------------------------------------------- QA-032: real caps, a chain that moves on

@pytest.fixture
def providers(monkeypatch, catalogue):  # noqa: F811
    """NVIDIA through the real client call with a stand-in OpenAI class, OpenRouter through a stand-in post."""
    import openai
    import requests

    from app.modules.ai_chat.services import llm_service_impl as impl

    state = {"nvidia_calls": [], "nvidia_clients": [], "openrouter_calls": [], "nvidia_sleep": 0.0, "openrouter": [], "slept": []}

    class Completions:
        def __init__(self, client):
            self.client = client

        def create(self, **kwargs):
            state["nvidia_calls"].append(kwargs["model"])
            time.sleep(state["nvidia_sleep"])
            raise openai.APITimeoutError(request=requests.Request("POST", "https://integrate.api.nvidia.com/v1").prepare())

    class FakeOpenAI:
        def __init__(self, **kwargs):
            state["nvidia_clients"].append(kwargs)
            self.chat = type("Chat", (), {"completions": Completions(self)})()

    class Answer:
        def __init__(self, content):
            self.content = content
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": self.content}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    def post(url, **kwargs):
        state["openrouter_calls"].append(kwargs["json"]["model"])
        outcome = state["openrouter"][min(len(state["openrouter_calls"]) - 1, len(state["openrouter"]) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return Answer(outcome)

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(impl.LLMService, "_get_all_api_keys", staticmethod(lambda provider: ["key"] if provider in ("nvidia", "openrouter") else []))
    monkeypatch.setattr(impl.LLMService, "_persist_interaction_record", staticmethod(lambda interaction: None))
    monkeypatch.setattr(impl.LLMService, "_sleep", staticmethod(lambda seconds: state["slept"].append(seconds)))
    state["impl"] = impl
    return state


def test_a_slow_nvidia_model_falls_through_to_openrouter_inside_the_deadline(providers, monkeypatch):
    impl = providers["impl"]
    monkeypatch.setattr(impl, "PUBLIC_INPUT_CHAIN", ("nvidia", "openrouter"))
    monkeypatch.setattr(impl.LLMService, "PUBLIC_ATTEMPT_SECONDS", 1.0)
    providers["nvidia_sleep"] = 1.0  # every NVIDIA model runs out its cap
    providers["openrouter"] = ["openrouter answers"]
    started = time.monotonic()
    answer = impl.LLMService.generate_from_prompt("a public page", use_cache=False, timeout=20, public_input=True)
    assert answer == "openrouter answers"
    assert time.monotonic() - started < 12, "the first provider spent the deadline"
    assert providers["openrouter_calls"], "OpenRouter was never reached"


def test_an_nvidia_call_is_one_attempt_with_a_client_that_does_not_retry(providers, monkeypatch):
    impl = providers["impl"]
    monkeypatch.setattr(impl, "PUBLIC_INPUT_CHAIN", ("nvidia",))
    monkeypatch.setattr(impl.LLMService, "PUBLIC_ATTEMPT_SECONDS", 1.0)
    with pytest.raises(Exception):
        impl.LLMService.generate_from_prompt("a public page", use_cache=False, timeout=6, public_input=True)
    models = providers["nvidia_calls"]
    assert len(models) == len(set(models)), "a model was called twice (the retry decorator): %s" % models
    assert providers["nvidia_clients"] and all(c.get("max_retries") == 0 for c in providers["nvidia_clients"])
    assert all(c.get("timeout") <= 1.0 for c in providers["nvidia_clients"])


def test_a_model_that_answers_with_null_content_hands_over_to_the_next_model(providers, monkeypatch):
    impl = providers["impl"]
    monkeypatch.setattr(impl, "PUBLIC_INPUT_CHAIN", ("openrouter",))
    providers["openrouter"] = [None, "the second model answers"]
    answer = impl.LLMService.generate_from_prompt("a public page", use_cache=False, timeout=20, public_input=True)
    assert answer == "the second model answers"
    assert len(providers["openrouter_calls"]) == 2


def test_openrouter_comes_before_nvidia_for_public_pages():
    from app.modules.ai_chat.services.model_defaults import PUBLIC_INPUT_CHAIN

    assert PUBLIC_INPUT_CHAIN.index("openrouter") < PUBLIC_INPUT_CHAIN.index("nvidia")


def test_the_prompt_for_a_large_site_stays_near_forty_thousand_characters():
    from app.modules.organization.website_crawl.service import _batched_prompt

    page = "<html><head><title>Big</title></head><body>%s</body></html>" % ("<p>Some words about the company and its product.</p>" * 6000)
    pages = [{"url": "https://big.example/p%d" % i, "html": page, "static": {"title": "Big"}} for i in range(30)]
    assert len(_batched_prompt(pages)) <= 42000


# ----------------------------------------------------------------------------- the failure is visible on the job

def test_a_failed_model_pass_puts_its_error_on_the_job_result(make_org, site, monkeypatch):
    from app.modules.organization import website_crawl
    from app.modules.organization.website_crawl import service

    def broken(prompt):
        raise RuntimeError("the providers sent nothing usable")

    org = _org(make_org, site.url)
    summary = _crawl(org, llm=broken)
    assert summary["model_error"] == {"type": "RuntimeError", "message": "the providers sent nothing usable"}

    monkeypatch.setattr(website_crawl, "default_model", lambda *a, **k: broken)
    result = service.run_website_retry_job({"organization_id": org.id, "retry_attempt": 1})
    assert result["model_error"]["type"] == "RuntimeError" and "nothing usable" in result["model_error"]["message"]


# ----------------------------------------------------------------------------- QA-053: the model pass is its own job

def test_the_first_read_proposes_the_fast_facts_and_leaves_the_model_pass_to_its_own_job(make_org, site, monkeypatch):
    from app.models.job import Job
    from app.modules.organization import website_crawl
    from app.modules.organization.website_crawl import pending_website_proposals, service

    calls = []

    def model(prompt):
        calls.append(prompt)
        return "{}"

    monkeypatch.setattr(website_crawl, "default_model", lambda *a, **k: model)
    org = _org(make_org, site.url)
    result = service.run_website_crawl_job({"organization_id": org.id})
    assert calls == [], "the first read waited for the model"
    assert pending_website_proposals(org.id), "the fast facts were not proposed"
    assert result["more_to_come"] is True and result["model_pass_queued"] is True
    queued = [j for j in Job.query.filter_by(task="website_profile_crawl_more").all() if (j.payload or {}).get("organization_id") == org.id]
    assert len(queued) == 1 and queued[0].status == "pending"
    service.run_website_crawl_job({"organization_id": org.id})  # reading again does not queue a second model pass
    queued = [j for j in Job.query.filter_by(task="website_profile_crawl_more").all() if (j.payload or {}).get("organization_id") == org.id]
    assert len(queued) == 1


def test_the_job_worker_runs_more_than_one_thread(app, monkeypatch):
    from app.services import job_queue_service
    from app.services.job_queue_service import JobQueueService

    assert job_queue_service.WORKER_THREADS >= 2
    service = JobQueueService()
    service.app = app
    release = threading.Event()
    started = []
    monkeypatch.setattr(service, "_worker_loop", lambda: (started.append(threading.current_thread().name), release.wait(5)))
    monkeypatch.setattr(service, "recover_stale_jobs", lambda *a, **k: 0)
    service.start_worker()
    try:
        time.sleep(0.5)
        assert len(started) == job_queue_service.WORKER_THREADS
    finally:
        release.set()
        service.stop_worker()


def test_two_threads_never_take_the_same_job_and_a_first_read_comes_before_a_model_pass(app, db_session, make_org):
    from app.models.job import Job
    from app.services.job_queue_service import JobQueueService

    org = make_org("c8-claim")
    service = JobQueueService()
    service.app = app
    long_job = service.create_job("model pass", "website_profile_crawl_more", {}, organization_id=org.id)
    first_read = service.create_job("first read", "website_profile_crawl", {}, organization_id=org.id)
    assert service._next_job().id == first_read.id, "the long job was picked ahead of a first read that waits"
    assert service._claim(first_read.id) is True
    assert service._claim(first_read.id) is False, "a second thread took the same job"
    assert db_session.get(Job, first_read.id).status == "in_progress"
    # Sharpened (websiteread02): one organisation runs one job at a time, so the model pass of the organisation whose
    # read is in progress waits for it; it is next once the read has finished.
    assert service._next_job() is None, "a second job of an organisation with a job in progress was picked"
    service.update_job_status(first_read.id, "completed", result={})
    assert service._next_job().id == long_job.id


# ----------------------------------------------------------------------------- QA-064: a read cut off by a restart

def _running(db_session, org, task, minutes):
    from app.models.job import Job

    job = Job(name="x", task=task, payload={"organization_id": org.id}, status="in_progress",
              started_at=datetime.utcnow() - timedelta(minutes=minutes))
    db_session.add(job)
    db_session.commit()
    return job


def test_a_website_read_running_for_twenty_minutes_is_failed_and_read_again(app, db_session, make_org):
    from app.models.job import Job
    from app.services.job_queue_service import JobQueueService

    org = make_org("c8-stale")
    stale = _running(db_session, org, "website_profile_crawl", 20)
    fresh = _running(db_session, org, "website_profile_crawl", 1)
    service = JobQueueService()
    service.app = app
    assert service.recover_stale_jobs() == 1
    db_session.expire_all()
    stale, fresh = db_session.get(Job, stale.id), db_session.get(Job, fresh.id)
    assert stale.status == "failed" and stale.result["reason"] == "interrupted" and stale.finished_at is not None
    assert fresh.status == "in_progress", "a read that started a minute ago was touched"
    pending = [j for j in Job.query.filter_by(task="website_profile_crawl", status="pending").all() if (j.payload or {}).get("organization_id") == org.id]
    assert len(pending) == 1, "the read was not queued again"
    assert service.recover_stale_jobs() == 0 and len([j for j in Job.query.filter_by(task="website_profile_crawl", status="pending").all()
                                                       if (j.payload or {}).get("organization_id") == org.id]) == 1


def test_another_kind_of_job_gets_thirty_minutes_and_is_not_queued_again(app, db_session, make_org):
    from app.models.job import Job
    from app.services.job_queue_service import JobQueueService

    org = make_org("c8-other")
    twenty = _running(db_session, org, "github_first_sync", 20)
    forty = _running(db_session, org, "github_first_sync", 40)
    service = JobQueueService()
    service.app = app
    assert service.recover_stale_jobs() == 1
    db_session.expire_all()
    assert db_session.get(Job, twenty.id).status == "in_progress" and db_session.get(Job, forty.id).status == "failed"
    assert Job.query.filter_by(task="github_first_sync", status="pending").count() == 0


def test_starting_the_worker_recovers_what_the_last_run_left_behind(app, db_session, make_org, monkeypatch):
    from app.services.job_queue_service import JobQueueService

    service = JobQueueService()
    service.app = app
    recovered = []
    monkeypatch.setattr(service, "recover_stale_jobs", lambda *a, **k: recovered.append(1) or 0)
    monkeypatch.setattr(service, "_worker_loop", lambda: None)
    service.start_worker()
    service.stop_worker()
    assert recovered, "nothing looked for interrupted jobs at worker start"


def test_an_organisation_with_a_cut_off_read_is_not_left_reading_for_ever(app, db_session, make_org, site):
    from app.models.job import Job
    from app.models.organization import Organization
    from app.modules.account.services import onboarding_service
    from app.services.job_queue_service import JobQueueService

    org = _org(make_org, site.url)
    Job.query.filter_by(status="pending").update({"status": "cancelled"})  # the read that saving the site queued
    db_session.commit()
    stale = _running(db_session, org, "website_profile_crawl", 25)
    with app.test_request_context("/"):
        assert onboarding_service.website_state(db_session.get(Organization, org.id))["state"] == "reading"
    service = JobQueueService()
    service.app = app
    service.recover_stale_jobs()
    db_session.expire_all()
    with app.test_request_context("/"):
        state = onboarding_service.website_state(db_session.get(Organization, org.id))
    assert state["state"] == "waiting" and "starts in a moment" in state["text"], state  # the read was queued again
    assert db_session.get(Job, stale.id).status == "failed"


def test_a_read_cut_off_with_nothing_queued_says_so_in_plain_words_and_offers_the_next_step(app, db_session, make_org, site):
    from app.models.job import Job
    from app.models.organization import Organization
    from app.modules.account.services import onboarding_service

    org = _org(make_org, site.url)
    db_session.add(Job(name="x", task="website_profile_crawl", payload={"organization_id": org.id}, status="failed",
                       result={"success": False, "reason": "interrupted"}))
    db_session.commit()
    with app.test_request_context("/"):
        state = onboarding_service.website_state(db_session.get(Organization, org.id))
    assert state["state"] == "failed" and "cut off by a restart" in state["text"]
    assert state["instead"], "no next step is offered"


# ----------------------------------------------------------------------------- QA-052: an example is not the contact

def test_an_address_shown_in_a_code_block_is_not_the_companys_contact():
    from app.modules.organization.website_crawl.extraction import extract_static

    html = ("<html><head><title>Resend</title></head><body><h1>Email for developers</h1>"
            "<pre><code>from: onboarding@resend.dev</code></pre><p>Send your first message today.</p>"
            "<p>Talk to us: <a href=\"mailto:hello@resend.example\">hello@resend.example</a></p></body></html>")
    found = extract_static("https://resend.example/", html, "https://resend.example")
    assert found["contact_email"]["value"] == "hello@resend.example"
    only_example = ("<html><head><title>Docs</title></head><body><pre>onboarding@resend.dev</pre>"
                    "<code>a@b.dev</code><a href=\"mailto:x@y.dev\"><code>x@y.dev</code></a></body></html>")
    assert "contact_email" not in extract_static("https://docs.example/", only_example, "https://docs.example")


# ----------------------------------------------------------------------------- the pass reads a few pages per call

def test_the_model_reads_nine_pages_three_at_a_time_and_only_the_first_call_gives_the_business_reading(make_org, site, monkeypatch):
    from app.modules.organization.website_crawl import service

    prompts = []

    def model(prompt):
        prompts.append(prompt)
        return fake_model(prompt)

    org = _org(make_org, site.url)
    summary = _crawl(org, llm=model)
    assert summary["pages_read"] >= 5
    assert 2 <= len(prompts) <= 3 and all(len(p) <= 32000 for p in prompts), [len(p) for p in prompts]
    marker = 'The "inference" object'
    assert marker in prompts[0] and all(marker not in p for p in prompts[1:]), "only the first call asks for it"
    assert service.MODEL_PAGES_PER_CALL == 3


def test_when_the_first_call_fails_the_rest_are_not_asked(make_org, site):
    asked = []

    def broken(prompt):
        asked.append(prompt)
        raise RuntimeError("no provider answered")

    org = _org(make_org, site.url)
    summary = _crawl(org, llm=broken)
    assert len(asked) == 1 and summary["model_errors"] == 1 and summary["model_error"]["type"] == "RuntimeError"


def test_a_public_page_may_go_to_the_free_models_that_keep_what_they_are_sent(providers, monkeypatch):
    """OpenRouter refused every free model with "no endpoints match your data policy" while the policy said deny."""
    import requests

    impl = providers["impl"]
    monkeypatch.setattr(impl, "PUBLIC_INPUT_CHAIN", ("openrouter",))
    seen = []
    real_post = requests.post

    def post(url, **kwargs):
        seen.append(kwargs["json"]["provider"])
        return real_post(url, **kwargs)

    monkeypatch.setattr(requests, "post", post)
    providers["openrouter"] = ["answer"]
    impl.LLMService.generate_from_prompt("a public page", use_cache=False, timeout=20, public_input=True)
    assert seen and all(policy == {"data_collection": "allow"} for policy in seen)
    seen.clear()
    impl.LLMService._call_openrouter("organisation data", "some/model:free", "key", public_input=False)
    assert seen == [{"data_collection": "deny"}]


def test_only_a_busy_or_slow_provider_is_tried_again_later_not_an_answer_that_is_not_json(make_org, site):
    from app.models.job import Job

    def jobs_for(org_id):
        return [j for j in Job.query.filter_by(task="website_profile_crawl_more").all() if (j.payload or {}).get("organization_id") == org_id]

    org = _org(make_org, site.url)
    summary = _crawl(org, llm=lambda prompt: "this is not JSON at all")
    assert summary["model_errors"] >= 1 and summary["retry_queued"] is None
    assert summary["model_error"]["type"] in ("ValueError", "JSONDecodeError")
    assert jobs_for(org.id) == []

    other = _org(make_org, site.url)

    def busy(prompt):
        raise RuntimeError("every provider is busy")

    again = _crawl(other, llm=busy)
    assert again["retry_queued"] is not None and len(jobs_for(other.id)) == 1


def test_the_website_reading_asks_for_room_to_answer(monkeypatch):
    from app.modules.ai_chat.services import llm_service_impl
    from app.modules.organization.website_crawl import service

    seen = {}
    monkeypatch.setattr(llm_service_impl.LLMService, "_call_public_chain",
                        staticmethod(lambda prompt, timeout=None, max_tokens=None: seen.update(max_tokens=max_tokens) or "{}"))
    service.default_model(timeout=30)("a page")
    assert seen["max_tokens"] == 8192
