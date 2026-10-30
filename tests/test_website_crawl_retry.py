"""A busy day means "later", not "never": the live free list, the backoff, and the queued retries.

QA's sign-up on 26 Oct met a rate-limited OpenRouter primary, five retired free slugs and a 429 from NVIDIA,
and the model pass added nothing. The free list is now read from OpenRouter's own catalogue, a 429 is
waited out once, and when every provider is busy the pass is queued to run again.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from tests.test_website_crawl import (  # noqa: F401  (fixtures and helpers of the crawl's own tests)
    _by_field,
    _crawl,
    _org,
    fake_model,
    internal_hosts_allowed,
    other_site,
    site,
)

pytestmark = pytest.mark.usefixtures("db_session")

CATALOGUE = {"data": [
    {"id": "meta-llama/llama-3.3-70b-instruct:free"},
    {"id": "openai/gpt-4o"},
    {"id": "deepseek/deepseek-chat-v3:free"},
    {"id": "google/gemma-3n:free"},
]}


class _Response:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise OSError("status %s" % self.status_code)

    def json(self):
        return self.body


@pytest.fixture
def catalogue(monkeypatch):
    from app.modules.ai_chat.services import model_defaults

    state = {"reads": 0, "body": CATALOGUE, "fail": False}

    def get(url, **kwargs):
        assert url == model_defaults.OPENROUTER_MODELS_URL
        state["reads"] += 1
        if state["fail"]:
            raise OSError("the catalogue did not answer")
        return _Response(state["body"])

    monkeypatch.setattr(model_defaults.requests, "get", get)
    monkeypatch.setitem(model_defaults._live_free, "ids", [])
    monkeypatch.setitem(model_defaults._live_free, "read_at", 0.0)
    monkeypatch.setitem(model_defaults._live_free, "tried_at", 0.0)
    state["module"] = model_defaults
    return state


# ----------------------------------------------------------------------------- the live free list

def test_the_free_list_is_read_from_the_catalogue_and_retired_slugs_are_never_tried(catalogue):
    from app.modules.ai_chat.services.model_defaults import fallback_models_for, live_free_openrouter_models

    assert live_free_openrouter_models() == [
        "meta-llama/llama-3.3-70b-instruct:free", "deepseek/deepseek-chat-v3:free", "google/gemma-3n:free"]
    chain = fallback_models_for("openrouter", "google/gemma-4-31b-it:free")
    assert "qwen/qwen-2.5-72b-instruct:free" not in chain and "openrouter/free" not in chain
    assert "google/gemma-4-31b-it:free" not in chain, "the primary is retired too: it is not in today's list"
    assert chain[0] == "meta-llama/llama-3.3-70b-instruct:free", "a curated model still listed comes first"
    assert all(model.endswith(":free") for model in chain)


def test_the_list_is_cached_for_24_hours_and_the_last_good_list_survives_a_failed_read(catalogue, monkeypatch):
    import time as real_time

    from app.modules.ai_chat.services import model_defaults

    first = model_defaults.live_free_openrouter_models()
    model_defaults.live_free_openrouter_models()
    assert catalogue["reads"] == 1, "the second call is served from the cache"
    now = real_time.time()
    monkeypatch.setattr(model_defaults.time, "time", lambda: now + 23 * 3600)
    model_defaults.live_free_openrouter_models()
    assert catalogue["reads"] == 1, "still cached after 23 hours"
    catalogue["fail"] = True
    monkeypatch.setattr(model_defaults.time, "time", lambda: now + 25 * 3600)
    assert model_defaults.live_free_openrouter_models() == first, "a failed read keeps the last good list"
    assert catalogue["reads"] == 2
    catalogue["fail"] = False
    catalogue["body"] = {"data": [{"id": "new/model:free"}]}
    monkeypatch.setattr(model_defaults.time, "time", lambda: now + 25 * 3600 + 700)
    assert model_defaults.live_free_openrouter_models() == ["new/model:free"]


# ----------------------------------------------------------------------------- order and backoff

class _Busy(Exception):
    def __init__(self, retry_after=None):
        super().__init__("429 rate limit exceeded")
        self.status_code = 429
        self.response = type("R", (), {"status_code": 429, "headers": {"Retry-After": str(retry_after)} if retry_after else {}})()


@pytest.fixture
def chain(monkeypatch, catalogue):
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    calls, sleeps, script = [], [], {}

    def caller(provider):
        def call(prompt, model, api_key, max_tokens=None, timeout=None, **_ignored):
            calls.append((provider, model))
            outcomes = script.get(provider, ["answer from " + provider])
            seen = sum(1 for p, _ in calls if p == provider) - 1
            outcome = outcomes[min(seen, len(outcomes) - 1)]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome, 1, 1, 0.0
        return call

    for name in ("openrouter", "nvidia", "mistral", "cerebras", "groq", "gemini"):
        monkeypatch.setattr(LLMService, "_call_" + name, staticmethod(caller(name)), raising=False)
    monkeypatch.setattr(LLMService, "_call_nvidia_once", staticmethod(caller("nvidia")), raising=False)
    monkeypatch.setattr(LLMService, "_get_all_api_keys", staticmethod(lambda provider: ["key"]))
    monkeypatch.setattr(LLMService, "_persist_interaction_record", staticmethod(lambda interaction: None))
    monkeypatch.setattr(LLMService, "_sleep", staticmethod(lambda seconds: sleeps.append(seconds)))
    return type("Chain", (), {"calls": calls, "sleeps": sleeps, "script": script, "service": LLMService})


def test_openrouter_is_tried_first_then_nvidia_and_a_429_is_waited_out_once(chain):
    from app.modules.ai_chat.services.model_defaults import live_free_openrouter_models

    chain.script["openrouter"] = [_Busy(retry_after=7)]
    chain.script["nvidia"] = [RuntimeError("down")]
    chain.script["mistral"] = ["mistral answers"]
    answer = chain.service.generate_from_prompt("public page", use_cache=False, timeout=60, public_input=True)

    assert answer == "mistral answers"
    providers = [p for p, _ in chain.calls]
    openrouter_calls = providers.count("openrouter")
    assert providers[0] == "openrouter"
    assert chain.sleeps[0] == 7, "Retry-After is honoured"
    assert chain.sleeps.count(7) == 1, "backed off once only"
    assert openrouter_calls >= 2, "openrouter was tried again once after the wait"
    openrouter = [m for p, m in chain.calls if p == "openrouter"]
    assert set(openrouter) <= set(live_free_openrouter_models()), "only models in today's list"
    assert providers.index("nvidia") > max(i for i, p in enumerate(providers) if p == "openrouter")
    assert providers[-1] == "mistral" and "cerebras" not in providers, "stops at the first answer"


def test_a_429_without_retry_after_backs_off_once_with_the_default_wait(chain):
    chain.script["openrouter"] = [_Busy()] * 5 + ["answer after the wait"]  # every openrouter model is busy, then one answers
    assert chain.service.generate_from_prompt("p", use_cache=False, timeout=60, public_input=True) == "answer after the wait"
    assert chain.sleeps == [chain.service.PUBLIC_BACKOFF_SECONDS]


# ----------------------------------------------------------------------------- the retries

def _retry_jobs(org_id):
    from app.models.job import Job

    return [j for j in Job.query.filter_by(task="website_profile_crawl_more").order_by(Job.id).all()
            if (j.payload or {}).get("organization_id") == org_id]


def _busy(prompt):
    raise RuntimeError("every provider is busy")


def test_when_every_provider_is_busy_a_retry_is_queued_and_the_retry_adds_the_missing_facts(make_org, site, monkeypatch):
    from app.modules.organization import website_crawl

    org = _org(make_org, site.url)
    started = dt.datetime.utcnow()
    summary = _crawl(org, llm=_busy)

    assert summary["model_errors"] == 1 and summary["retry_queued"]
    first = _retry_jobs(org.id)
    assert len(first) == 1 and first[0].payload["retry_attempt"] == 1
    due = dt.datetime.fromisoformat(first[0].payload["not_before"])
    assert dt.timedelta(minutes=14) < due - started < dt.timedelta(minutes=16), "the first retry is in 15 minutes"
    assert "category" not in _by_field(org.id), "nothing from the model yet"
    before = sum(len(rows) for rows in _by_field(org.id).values())

    # later the providers are less busy: the retry runs and adds what the model found
    monkeypatch.setattr(website_crawl, "default_model", lambda *a, **k: fake_model)
    result = website_crawl.run_website_retry_job({"organization_id": org.id, "retry_attempt": 1})
    found = _by_field(org.id)
    assert result["added"] > 0 and found["category"][0]["value"] == "Transportation & Logistics"
    assert sum(len(rows) for rows in found.values()) == before + result["added"]
    assert found["company_name"][0]["value"] == "Acme Robotics Ltd" and len(found["company_name"]) == 1, "nothing doubled"
    assert len(_retry_jobs(org.id)) == 1, "the retry succeeded, so no further retry is queued"


def test_a_retry_that_finds_them_still_busy_queues_one_more_an_hour_later_and_then_stops(make_org, site, monkeypatch):
    from app.modules.organization import website_crawl

    org = _org(make_org, site.url)
    _crawl(org, llm=_busy)
    monkeypatch.setattr(website_crawl, "default_model", lambda *a, **k: _busy)
    started = dt.datetime.utcnow()
    website_crawl.run_website_retry_job({"organization_id": org.id, "retry_attempt": 1})
    jobs = _retry_jobs(org.id)
    assert [j.payload["retry_attempt"] for j in jobs] == [1, 2]
    due = dt.datetime.fromisoformat(jobs[1].payload["not_before"])
    assert dt.timedelta(minutes=59) < due - started < dt.timedelta(minutes=61), "the second retry is in an hour"
    website_crawl.run_website_retry_job({"organization_id": org.id, "retry_attempt": 2})
    assert len(_retry_jobs(org.id)) == 2, "after the second retry there is no third"


def test_the_queue_leaves_a_retry_alone_until_it_is_due(app):
    from app.models.job import Job
    from app.services.job_queue_service import JobQueueService

    later = (dt.datetime.utcnow() + dt.timedelta(minutes=5)).isoformat()
    earlier = (dt.datetime.utcnow() - dt.timedelta(minutes=5)).isoformat()
    assert JobQueueService._is_due(Job(task="t", payload={"not_before": later})) is False
    assert JobQueueService._is_due(Job(task="t", payload={"not_before": earlier})) is True
    assert JobQueueService._is_due(Job(task="t", payload={})) is True


def test_the_person_is_told_more_facts_will_come_when_the_service_is_less_busy(app, make_org, site, monkeypatch):
    from app import db
    from app.models.job import JobStatus
    from app.models.organization import Organization
    from app.modules.account.services import onboarding_service as onboarding
    from app.modules.organization import website_crawl
    from app.services.job_queue_service import get_job_queue_service

    org = _org(make_org, site.url)
    monkeypatch.setattr(website_crawl, "default_model", lambda *a, **k: _busy)
    job_id = website_crawl.queue_website_crawl(org.id).id
    result = website_crawl.run_website_crawl_job({"organization_id": org.id, "job_id": job_id})
    assert result["more_to_come"] is True and result["model_pass_queued"] is True
    get_job_queue_service().update_job_status(job_id, JobStatus.COMPLETED.value, result=json.loads(json.dumps(result)))
    # the model pass is its own job; it finds every provider busy, so a retry is queued for later
    retry = website_crawl.run_website_retry_job({"organization_id": org.id, "retry_attempt": 0})
    assert retry["retry_queued"]

    with app.test_request_context("/welcome"):
        row = next(r for r in onboarding.sources(db.session.get(Organization, org.id)) if r["key"] == "website")
    assert "More facts will be added when our reading service is less busy." in row["text"]
    assert onboarding.more_facts_coming(db.session.get(Organization, org.id)) == onboarding.MORE_FACTS_LATER


def test_step_three_shows_the_sentence_to_a_signed_in_person(app, client, login_as, make_org, site, monkeypatch):
    from app import db
    from app.models.job import JobStatus
    from app.models.user import Role, User
    from app.modules.organization import website_crawl
    from app.services.job_queue_service import get_job_queue_service

    org = _org(make_org, site.url)
    Role.insert_roles()
    user = User(email="step3@example.com", first_name="Sam", last_name="Founder", organization_id=org.id,
                confirmed=True, enterprise_role="enterprise_architect")
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "password"
    db.session.add(user)
    db.session.commit()
    user_id = user.id
    monkeypatch.setattr(website_crawl, "default_model", lambda *a, **k: _busy)
    job_id = website_crawl.queue_website_crawl(org.id).id
    result = website_crawl.run_website_crawl_job({"organization_id": org.id, "job_id": job_id})
    get_job_queue_service().update_job_status(job_id, JobStatus.COMPLETED.value, result=json.loads(json.dumps(result)))
    assert website_crawl.run_website_retry_job({"organization_id": org.id, "retry_attempt": 0})["retry_queued"]  # the model pass found them busy

    login_as(client, user_id)
    page = client.get("/account/welcome?step=3")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "More facts will be added when our reading service is less busy." in html
    assert 'data-testid="more-facts-coming"' in html, "shown beside the facts to confirm"


def test_a_retry_never_proposes_again_a_fact_the_person_has_confirmed_or_left_out(make_org, site, monkeypatch):
    from app import db
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.modules.organization import website_crawl
    from app.modules.organization.website_crawl import confirm

    org = _org(make_org, site.url)
    _crawl(org, llm=_busy)
    proposed = _by_field(org.id)
    assert "company_name" in proposed and "tagline" in proposed
    Role.insert_roles()
    user = User(email="decider@example.com", first_name="Dee", last_name="Cider", organization_id=org.id,
                confirmed=True, enterprise_role="enterprise_architect")
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "password"
    db.session.add(user)
    db.session.commit()
    decisions = [{"id": proposed["company_name"][0]["id"], "accept": True},
                 {"id": proposed["tagline"][0]["id"], "accept": False}]
    confirm.confirm(db.session.get(Organization, org.id), db.session.get(User, user.id), decisions)
    assert "company_name" not in _by_field(org.id) and "tagline" not in _by_field(org.id), "decided: no longer pending"

    monkeypatch.setattr(website_crawl, "default_model", lambda *a, **k: fake_model)
    website_crawl.run_website_retry_job({"organization_id": org.id, "retry_attempt": 1})
    after = _by_field(org.id)
    assert "company_name" not in after, "a confirmed fact is not proposed again"
    assert "tagline" not in after, "a fact the person left out is not proposed again"
    assert after["category"][0]["value"] == "Transportation & Logistics", "what is new is still added"
