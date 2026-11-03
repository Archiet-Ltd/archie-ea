"""A script cannot create an account or a workspace; a person still gets theirs on confirming.

Each refusal creates nothing. A workspace (an organisation and its agent charters) is created when the
address is confirmed, never at sign-up. Messages are captured by Flask-Mail's test backend and the link
is followed as a person would follow it.
"""
import time
import uuid

import pytest


PASSWORD = "Correct-horse-9"
SENDER = "no-reply@example.com"
PERSON_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


@pytest.fixture
def outbox(app):
    from app.extensions import mail

    with mail.record_messages() as messages:
        yield messages


@pytest.fixture
def mail_on(app, monkeypatch):
    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", SENDER)


@pytest.fixture
def checks_on(app, monkeypatch):
    monkeypatch.setitem(app.config, "SIGNUP_FORM_TOKEN_REQUIRED", True)
    monkeypatch.setitem(app.config, "SIGNUP_MIN_SECONDS", 1)


def _client(app):
    from flask import g, has_app_context

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)
    return app.test_client()


def _form_token(client):
    import re

    page = client.get("/account/register", headers={"User-Agent": PERSON_AGENT}).get_data(as_text=True)
    found = re.search(r'name="form_issued"[^>]*value="([^"]+)"', page) or re.search(
        r'value="([^"]+)"[^>]*name="form_issued"', page
    )
    return found.group(1) if found else ""


def _data(email, **extra):
    data = {"first_name": "Sam", "last_name": "Walker", "email": email, "password": PASSWORD, "password2": PASSWORD}
    data.update(extra)
    return data


def _address(tag="a"):
    return "bot-check-{}-{}@example.com".format(tag, uuid.uuid4().hex[:8])


def _post(client, data, agent=PERSON_AGENT):
    return client.post("/account/register", data=data, headers={"User-Agent": agent})


def _account(email):
    from app import db
    from app.models.user import User

    db.session.expire_all()
    return User.find_by_email(email)


def _sign_up_properly(client, email):
    token = _form_token(client)
    time.sleep(1.2)
    return _post(client, _data(email, form_issued=token, nickname=""))


def _own_workspaces(user):
    """Organisations created for this person: the shared holding organisation is nobody's workspace."""
    from app.models import Organization
    from app.models.org_role import OrgRole

    org = Organization.query.filter_by(id=user.organization_id).first()
    holding = org is not None and org.slug == "unconfirmed-sign-ups"
    roles = OrgRole.query.filter_by(user_id=user.id).count()
    return (0 if holding else 1), roles


def _organisation_count():
    from app.models import Organization

    # the shared holding organisation is made on first use, and is nobody's workspace
    return Organization.query.filter(Organization.slug != "unconfirmed-sign-ups").count()


def test_a_filled_hidden_field_is_refused_and_creates_nothing(app, db_session, mail_on, outbox, checks_on):
    client = _client(app)
    token = _form_token(client)
    time.sleep(1.2)
    email = _address("honeypot")
    before = _organisation_count()

    resp = _post(client, _data(email, form_issued=token, nickname="http://spam.example"))

    assert resp.status_code == 400
    assert "could not accept this sign-up" in resp.get_data(as_text=True)
    assert _account(email) is None and _organisation_count() == before and not outbox


def test_a_submission_faster_than_a_person_is_refused_and_creates_nothing(app, db_session, mail_on, outbox, monkeypatch):
    monkeypatch.setitem(app.config, "SIGNUP_FORM_TOKEN_REQUIRED", True)
    monkeypatch.setitem(app.config, "SIGNUP_MIN_SECONDS", 30)
    client = _client(app)
    token = _form_token(client)
    email = _address("fast")
    before = _organisation_count()

    resp = _post(client, _data(email, form_issued=token, nickname=""))

    assert resp.status_code == 400
    assert "quicker than a person" in resp.get_data(as_text=True)
    assert _account(email) is None and _organisation_count() == before and not outbox


def test_a_submission_with_no_form_token_is_refused(app, db_session, mail_on, outbox, checks_on):
    email = _address("notoken")
    resp = _post(_client(app), _data(email))
    assert resp.status_code == 400
    assert _account(email) is None and not outbox


def test_a_tampered_form_token_is_refused(app, db_session, mail_on, outbox, checks_on):
    email = _address("forged")
    resp = _post(_client(app), _data(email, form_issued="1700000000.forged", nickname=""))
    assert resp.status_code == 400
    assert _account(email) is None


@pytest.mark.parametrize("domain", ["mailinator.com", "guerrillamail.com", "10minutemail.com", "Yopmail.com"])
def test_a_disposable_domain_is_refused_with_a_plain_message(app, db_session, mail_on, outbox, domain):
    email = "someone-{}@{}".format(uuid.uuid4().hex[:6], domain)
    before = _organisation_count()

    resp = _post(_client(app), _data(email))

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "work or personal email address" in body
    assert _account(email) is None and _organisation_count() == before and not outbox


@pytest.mark.parametrize("agent", ["WebslockBot/1.0", "Mozilla/5.0 (compatible; SomeCrawler/2.1)", "Scrapy/2.11 (+https://scrapy.org)"])
def test_a_user_agent_that_names_itself_a_bot_is_refused(app, db_session, mail_on, outbox, agent):
    email = _address("agent")
    before = _organisation_count()

    resp = _post(_client(app), _data(email), agent=agent)

    assert resp.status_code == 400
    assert "Automated sign-ups are not accepted" in resp.get_data(as_text=True)
    assert _account(email) is None and _organisation_count() == before


def test_an_ordinary_browser_agent_is_not_refused_on_its_agent_alone(app, db_session, mail_on, outbox):
    email = _address("browser")
    resp = _post(_client(app), _data(email))
    assert resp.status_code == 302
    assert _account(email) is not None


def test_an_unconfirmed_account_has_no_organisation_of_its_own(app, db_session, mail_on, outbox, checks_on):
    from app.models.agent_charter import AgentCharter

    email = _address("unconfirmed")
    before = _organisation_count()
    client = _client(app)

    resp = _sign_up_properly(client, email)

    assert resp.status_code == 302 and resp.headers["Location"].endswith("/account/unconfirmed")
    user = _account(email)
    assert user.confirmed is False
    assert _own_workspaces(user) == (0, 0)
    assert _organisation_count() == before
    assert AgentCharter.query.filter_by(organization_id=user.organization_id).count() == 0
    assert len(outbox) == 1


def test_confirming_creates_exactly_one_organisation_and_the_link_works_once(app, db_session, mail_on, outbox, checks_on):
    from app.models.agent_charter import AgentCharter

    email = _address("confirm")
    client = _client(app)
    _sign_up_properly(client, email)
    user = _account(email)
    before = _organisation_count()
    path = _link_path(outbox[0])

    confirmed = client.get(path)

    assert confirmed.status_code == 302 and confirmed.headers["Location"].endswith("/account/welcome")
    user = _account(email)
    assert user.confirmed is True
    assert _organisation_count() == before + 1
    assert _own_workspaces(user) == (1, 1)
    assert user.is_admin()
    assert AgentCharter.query.filter_by(organization_id=user.organization_id).count() > 0

    again = _client(app).get(path)
    assert again.status_code == 410
    assert _organisation_count() == before + 1


def test_two_sign_ups_never_share_a_workspace(app, db_session, mail_on, outbox, checks_on):
    first_email, second_email = _address("one"), _address("two")
    # a client is made just before its sign-up: signing in caches the user on the shared test context
    first_client = _client(app)
    r1 = _sign_up_properly(first_client, first_email)
    second_client = _client(app)
    r2 = _sign_up_properly(second_client, second_email)
    assert (r1.status_code, r2.status_code) == (302, 302), r2.get_data(as_text=True)[-800:]
    assert len(outbox) == 2
    first_path, second_path = _link_path(outbox[0]), _link_path(outbox[1])

    second_client.get(second_path)
    assert _own_workspaces(_account(first_email)) == (0, 0)
    first_client.get(first_path)

    first, second = _account(first_email), _account(second_email)
    assert first.organization_id != second.organization_id
    assert _own_workspaces(first) == (1, 1) and _own_workspaces(second) == (1, 1)
    # the first person's link cannot open the second person's workspace
    from app.models.org_role import OrgRole

    assert OrgRole.query.filter_by(user_id=first.id, organization_id=second.organization_id).count() == 0
    assert OrgRole.query.filter_by(user_id=second.id, organization_id=first.organization_id).count() == 0


def test_without_a_mail_server_the_workspace_is_made_at_once(app, db_session, monkeypatch, outbox):
    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", None)
    monkeypatch.setitem(app.config, "MAIL_USERNAME", None)
    email = _address("nomail")
    resp = _post(_client(app), _data(email))
    assert resp.status_code == 302
    user = _account(email)
    assert user.confirmed is True and _own_workspaces(user) == (1, 1)


def test_the_form_carries_a_signed_time_and_an_empty_hidden_field(app):
    page = _client(app).get("/account/register").get_data(as_text=True)
    assert 'name="form_issued"' in page and 'name="nickname"' in page
    assert _form_token(_client(app))


def _link_path(message):
    import re

    match = re.search(r"https?://[^\s\"'<>]+(/account/confirm-account/[^\s\"'<>]+)", message.body)
    assert match, message.body
    return match.group(1)


def _alerts(resp):
    import re

    return re.findall(r'role="alert"[^>]*>\s*([^<]+)', resp.get_data(as_text=True))


# -- the clean-up commands for accounts already left behind ----------------------------------------


def _left_behind(db_session, make_org, age_days, confirmed=False, tag="left"):
    from datetime import datetime, timedelta

    from app.models.user import User

    org = make_org()
    user = User(
        first_name="Zq", last_name="Xv", email="{}-{}@example.org".format(tag, uuid.uuid4().hex[:8]),
        password=PASSWORD, confirmed=confirmed, organization_id=org.id,
    )
    user.signup_at = datetime.utcnow() - timedelta(days=age_days)
    db_session.add(user)
    db_session.flush()
    return user


def _run(app, command, *args):
    from click.testing import CliRunner

    return CliRunner().invoke(command, list(args), obj=None, catch_exceptions=False)


def test_the_list_names_the_email_domain_and_never_the_person(app, db_session, make_org):
    from app.commands.unconfirmed_signups import list_unconfirmed_signups

    user = _left_behind(db_session, make_org, 3)
    local_part = user.email.split("@")[0]

    out = _run(app, list_unconfirmed_signups).output

    assert "example.org" in out and local_part not in out
    assert out.splitlines()[0] == "domain\tcreated\tconfirmed\tsessions\tactivity"
    assert "no\t0\tno" in out


def test_disabling_marks_old_unconfirmed_accounts_inactive_and_deletes_nothing(app, db_session, make_org):
    from app.commands.unconfirmed_signups import disable_unconfirmed_signups
    from app.models.user import User

    old = _left_behind(db_session, make_org, 5, tag="old")
    recent = _left_behind(db_session, make_org, 0, tag="recent")
    confirmed = _left_behind(db_session, make_org, 9, confirmed=True, tag="confirmed")
    users = User.query.count()

    dry = _run(app, disable_unconfirmed_signups, "--older-than", "2d", "--dry-run").output
    assert "dry run, 0 disabled" in dry and old.disabled_at is None

    out = _run(app, disable_unconfirmed_signups, "--older-than", "2d").output

    assert "disabled" in out
    db_session.expire_all()
    assert old.disabled_at is not None and old.is_active is False
    assert recent.disabled_at is None and confirmed.disabled_at is None
    assert User.query.count() == users
    from app.modules.account.services.account_service import AccountService

    assert AccountService.authenticate(old.email, PASSWORD) is None
    assert AccountService.authenticate(recent.email, PASSWORD) is not None
