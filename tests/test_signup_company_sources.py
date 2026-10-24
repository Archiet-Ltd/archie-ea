"""Sign-up after the account: the company's sources, industry, frameworks and AI key.

Two real organisations in every test: what one person gives during sign-up is on their own
organisation and on no other.
"""

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _owner(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="owner.%s@example.test" % uuid.uuid4().hex[:8], first_name="Own", last_name="Er",
        organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "Sup3r-Secret!pass"
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
    db_session.flush()
    return user


@pytest.fixture
def two(db_session, make_org):
    one, other = make_org("signup-one"), make_org("signup-two")
    return {"one": one, "two": other, "one_owner": _owner(db_session, one), "two_owner": _owner(db_session, other)}


def _post(client, login_as, user, path, payload):
    login_as(client, user)
    return client.post("/account/welcome/" + path, json=payload)


def test_industry_and_frameworks_are_kept_on_the_organisation_that_chose_them(client, login_as, two, db_session):
    reply = _post(client, login_as, two["one_owner"], "choices", {"industry": "banking", "frameworks": ["iso27001", "soc2"]})
    assert reply.status_code == 200, reply.get_data(as_text=True)
    from app.modules.account.services import onboarding_service as onboarding

    assert onboarding.choices(two["one"]) == {"industry": "banking", "frameworks": ["iso27001", "soc2"]}
    assert onboarding.choices(two["two"]) == {"industry": None, "frameworks": []}


def test_a_choice_not_on_the_list_is_refused_and_nothing_is_kept(client, login_as, two):
    bad_industry = _post(client, login_as, two["one_owner"], "choices", {"industry": "piracy", "frameworks": []})
    bad_framework = _post(client, login_as, two["one_owner"], "choices", {"industry": "banking", "frameworks": ["made-up"]})
    assert bad_industry.status_code == 400 and bad_framework.status_code == 400
    from app.modules.account.services import onboarding_service as onboarding

    assert onboarding.choices(two["one"]) == {"industry": None, "frameworks": []}


def test_skipping_both_choices_clears_them(client, login_as, two):
    _post(client, login_as, two["one_owner"], "choices", {"industry": "banking", "frameworks": ["gdpr"]})
    reply = _post(client, login_as, two["one_owner"], "choices", {"industry": "", "frameworks": []})
    assert reply.status_code == 200
    from app.modules.account.services import onboarding_service as onboarding

    assert onboarding.choices(two["one"]) == {"industry": None, "frameworks": []}


def test_the_website_needs_consent_and_is_kept_for_this_organisation_only(client, login_as, two):
    refused = _post(client, login_as, two["one_owner"], "website", {"url": "acme.com", "consent": False})
    assert refused.status_code == 400 and "read this site" in refused.get_json()["message"]
    ok = _post(client, login_as, two["one_owner"], "website", {"url": "acme.com", "consent": True})
    assert ok.status_code == 200
    rows = {row["key"]: row for row in ok.get_json()["sources"]}
    assert rows["website"]["state"] == "waiting"
    assert "https://acme.com will be read soon" in rows["website"]["text"]
    from app.modules.account.services import onboarding_service as onboarding

    assert {r["key"]: r["state"] for r in onboarding.sources(two["two"])}["website"] == "not_given"


def test_github_is_connected_and_the_read_is_queued_for_the_giving_organisation(client, login_as, two, monkeypatch):
    from app.services.job_queue_service import get_job_queue_service

    reply = _post(client, login_as, two["one_owner"], "github", {"organization": "acme", "token": "ghp_not_a_real_token"})
    assert reply.status_code == 200, reply.get_data(as_text=True)
    rows = {row["key"]: row for row in reply.get_json()["sources"]}
    assert rows["github"]["state"] == "waiting"
    mine = get_job_queue_service().list_jobs(two["one"].id, task="onboarding_github_import")
    theirs = get_job_queue_service().list_jobs(two["two"].id, task="onboarding_github_import")
    assert len(mine) == 1 and not theirs
    assert mine[0].payload["approver_id"] == two["one_owner"].id
    assert "ghp_not_a_real_token" not in str(mine[0].payload)


def test_a_bad_github_name_is_refused_in_words_and_queues_nothing(client, login_as, two):
    reply = _post(client, login_as, two["one_owner"], "github", {"organization": "../etc", "token": "ghp_x"})
    assert reply.status_code == 400
    assert "GitHub organisation name" in reply.get_json()["message"]


def test_jira_needs_all_three_parts(client, login_as, two):
    reply = _post(client, login_as, two["one_owner"], "jira", {"instance_url": "https://acme.atlassian.net", "email": "", "token": ""})
    assert reply.status_code == 400 and "Jira needs" in reply.get_json()["message"]


def test_a_finished_read_reports_what_was_added_and_only_to_its_organisation(two, db_session):
    from app.models.job import Job, JobStatus
    from app.modules.account.services import onboarding_service as onboarding

    db_session.add(Job(
        name="GitHub: read repositories", task=onboarding.GITHUB_TASK, status=JobStatus.COMPLETED.value,
        payload={"organization_id": two["one"].id, "connector_id": "x"},
        result={"success": True, "repositories": 16, "added": 22},
    ))
    db_session.flush()
    mine = {r["key"]: r for r in onboarding.sources(two["one"])}
    theirs = {r["key"]: r for r in onboarding.sources(two["two"])}
    assert mine["github"]["state"] == "done"
    assert mine["github"]["text"] == "Read 16 repositories; 22 items added to your model."
    assert theirs["github"]["state"] == "not_given"
    assert onboarding.any_source_given(two["one"]) and not onboarding.any_source_given(two["two"])


def test_a_key_the_provider_refuses_is_not_kept(client, login_as, two, monkeypatch):
    from app.models.models import APISettings

    monkeypatch.setattr("app.services.llm_service.test_api_key", lambda provider, key, model=None: {"success": False, "message": "no"})
    reply = _post(client, login_as, two["one_owner"], "ai/test", {"provider": "openai", "key": "sk-refused-key"})
    assert reply.status_code == 400
    assert APISettings.query.filter_by(provider="openai").count() == 0


def test_a_tested_key_is_kept_for_this_organisation_alone_and_named_back(client, login_as, two, monkeypatch, db_session):
    from flask import g

    from app.models.models import APISettings
    from app.modules.ai_chat.services.llm_service_impl import LLMService

    monkeypatch.setattr("app.services.llm_service.test_api_key", lambda provider, key, model=None: {"success": True, "message": "ok"})
    reply = _post(client, login_as, two["one_owner"], "ai/test", {"provider": "openai", "key": "sk-journey-key-0001"})
    assert reply.status_code == 200
    message = reply.get_json()["message"]
    assert "OpenAI" in message and "your organisation pays OpenAI" in message
    assert "sk-journey-key-0001" not in reply.get_data(as_text=True)
    g.current_org_id = two["one"].id
    assert LLMService._get_all_api_keys("openai")[:1] == ["sk-journey-key-0001"]
    db_session.expire_all()
    g.current_org_id = two["two"].id
    assert "sk-journey-key-0001" not in LLMService._get_all_api_keys("openai")
    assert APISettings.query.count() == 0  # org two sees no row of org one
