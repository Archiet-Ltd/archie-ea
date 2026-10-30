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

    assert onboarding.choices(two["one"]) == {"kind": None, "industry": "banking", "frameworks": ["iso27001", "soc2"]}
    assert onboarding.choices(two["two"]) == {"kind": None, "industry": None, "frameworks": []}


def test_the_company_kind_is_kept_for_the_choosing_organisation_only_and_unknown_kinds_are_refused(client, login_as, two):
    ok = _post(client, login_as, two["one_owner"], "choices", {"kind": "enterprise", "industry": "", "frameworks": []})
    assert ok.status_code == 200, ok.get_data(as_text=True)
    bad = _post(client, login_as, two["one_owner"], "choices", {"kind": "giant", "industry": "", "frameworks": []})
    assert bad.status_code == 400
    from app.modules.account.services import onboarding_service as onboarding

    assert onboarding.choices(two["one"])["kind"] == "enterprise"
    assert onboarding.choices(two["two"])["kind"] is None
    # Saving again without a kind leaves the answer as it was.
    _post(client, login_as, two["one_owner"], "choices", {"industry": "banking", "frameworks": []})
    assert onboarding.choices(two["one"])["kind"] == "enterprise"


def test_a_choice_not_on_the_list_is_refused_and_nothing_is_kept(client, login_as, two):
    bad_industry = _post(client, login_as, two["one_owner"], "choices", {"industry": "piracy", "frameworks": []})
    bad_framework = _post(client, login_as, two["one_owner"], "choices", {"industry": "banking", "frameworks": ["made-up"]})
    assert bad_industry.status_code == 400 and bad_framework.status_code == 400
    from app.modules.account.services import onboarding_service as onboarding

    assert onboarding.choices(two["one"]) == {"kind": None, "industry": None, "frameworks": []}


def test_skipping_both_choices_clears_them(client, login_as, two):
    _post(client, login_as, two["one_owner"], "choices", {"industry": "banking", "frameworks": ["gdpr"]})
    reply = _post(client, login_as, two["one_owner"], "choices", {"industry": "", "frameworks": []})
    assert reply.status_code == 200
    from app.modules.account.services import onboarding_service as onboarding

    assert onboarding.choices(two["one"]) == {"kind": None, "industry": None, "frameworks": []}


def test_the_website_needs_consent_and_is_kept_for_this_organisation_only(client, login_as, two):
    refused = _post(client, login_as, two["one_owner"], "website", {"url": "acme.com", "consent": False})
    assert refused.status_code == 400 and "read this site" in refused.get_json()["message"]
    ok = _post(client, login_as, two["one_owner"], "website", {"url": "acme.com", "consent": True})
    assert ok.status_code == 200
    rows = {row["key"]: row for row in ok.get_json()["sources"]}
    assert rows["website"]["state"] == "waiting"
    assert "Company website is connected" in rows["website"]["text"]
    from app.modules.account.services import onboarding_service as onboarding
    from app.services.job_queue_service import get_job_queue_service

    assert len(get_job_queue_service().list_jobs(two["one"].id, limit=5, task=onboarding.WEBSITE_TASK)) == 1
    assert get_job_queue_service().list_jobs(two["two"].id, limit=5, task=onboarding.WEBSITE_TASK) == []

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


def test_register_user_retries_when_slug_already_exists(app, db_session):
    """When the slug an organisation would take is already in use, the sign-up
    succeeds with a suffixed slug and no IntegrityError reaches the caller."""
    import uuid
    from app.models.organization import Organization
    from app.modules.account.services.account_service import AccountService

    _slug_base = "caseyfounder"
    # Create an organisation whose slug is exactly what "Casey" "Founder" would claim.
    first = Organization(name="Casey's Workspace", slug=_slug_base)
    db_session.add(first)
    db_session.commit()

    with app.test_request_context("/"):
        user = AccountService.register_user("Casey", "Founder", "casey.existing.%s@example.test" % uuid.uuid4().hex[:8], "Sup3r-Secret!pass")

    assert user is not None
    org = Organization.query.filter_by(id=user.organization_id).first()
    assert org is not None
    assert org.slug != _slug_base
    assert org.slug.startswith(_slug_base + "-")


def test_zzz_concurrent_signups_both_succeed(app, db_session):
    """Two people signing up at the same moment with the same name both get an
    organisation and neither sees an error."""
    import multiprocessing
    import uuid
    from app.models.organization import Organization
    from app.modules.account.services.account_service import AccountService

    email1 = "casey.one.%s@example.test" % uuid.uuid4().hex[:8]
    email2 = "casey.two.%s@example.test" % uuid.uuid4().hex[:8]

    def sign_up_one(email, queue):
        try:
            from app import create_app
            import os
            os.environ.setdefault("FLASK_CONFIG", "testing")
            proc_app = create_app("testing")
            proc_app.config["TESTING"] = True
            proc_app.config["WTF_CSRF_ENABLED"] = False
            with proc_app.app_context(), proc_app.test_request_context("/"):
                user = AccountService.register_user("Casey", "Founder", email, "Sup3r-Secret!pass")
                org = Organization.query.filter_by(id=user.organization_id).first()
                queue.put(("ok", email, org.slug))
        except Exception as exc:
            queue.put(("err", email, type(exc).__name__ + ": " + str(exc)[:300]))

    queue = multiprocessing.Queue()
    p1 = multiprocessing.Process(target=sign_up_one, args=(email1, queue))
    p2 = multiprocessing.Process(target=sign_up_one, args=(email2, queue))
    p1.start()
    p2.start()
    p1.join()
    p2.join()

    results = []
    errors = []
    while not queue.empty():
        kind, email, slug = queue.get()
        if kind == "ok":
            results.append((email, slug))
        else:
            errors.append((email, slug))

    assert not errors, "one of the concurrent sign-ups raised: %s" % errors
    assert len(results) == 2, "expected 2 results, got %d: %s" % (len(results), results)
    slugs = [s for _, s in results]
    assert len(set(slugs)) == 2, "both got the same slug: %s" % slugs
    for slug in slugs:
        assert slug.startswith("caseyfounder"), slug

    # Multiprocessing commits outside the db_session rollback; clean up
    # using a real connection so the DELETE is visible to other tests.
    from sqlalchemy import text
    from app import db as app_db
    app_db.session.execute(text(
        "DELETE FROM organizations WHERE slug LIKE 'caseyfounder%'"
        " AND id IN (SELECT organization_id FROM users WHERE email LIKE '%concurrent@example.test')"
    ))
    app_db.session.execute(text(
        "DELETE FROM users WHERE email LIKE '%concurrent@example.test'"
    ))
    app_db.session.commit()


def test_slug_collision_inside_transaction_retries(app, db_session):
    """Inside a single transaction that already holds the slug, the sign-up retries
    with no automatic collision detection (the old while-loop is gone)."""
    import uuid
    from app.models.organization import Organization
    from app.modules.account.services.account_service import AccountService

    _slug_base = "caseyfounder-%s" % uuid.uuid4().hex[:6]
    first = Organization(name="Casey's Workspace", slug=_slug_base)
    db_session.add(first)
    db_session.flush()

    with app.test_request_context("/"):
        user = AccountService.register_user("Casey", "Founder", "casey.same.tx.%s@example.test" % uuid.uuid4().hex[:8], "Sup3r-Secret!pass")

    assert user is not None
    org = Organization.query.filter_by(id=user.organization_id).first()
    assert org is not None
    assert org.slug != _slug_base
    assert org.slug != "caseyfounder"
    assert org.slug.startswith("casey")
