"""Unconfirmed sign-ups wait in an organisation of their own, and every confirmation creates the workspace.

The platform administrator lives in the ``default`` organisation. Strangers who have not confirmed their
address must never be rows in that organisation, and the people who really are in it must still be counted.
Confirmation and workspace creation are one step: a failure leaves the account unconfirmed and the link usable.
"""
import re
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


def _address(tag):
    return "hold-org-{}-{}@example.com".format(tag, uuid.uuid4().hex[:8])


def _form_token(client):
    page = client.get("/account/register", headers={"User-Agent": PERSON_AGENT}).get_data(as_text=True)
    found = re.search(r'name="form_issued"[^>]*value="([^"]+)"', page) or re.search(
        r'value="([^"]+)"[^>]*name="form_issued"', page
    )
    return found.group(1) if found else ""


def _data(email, **extra):
    data = {"first_name": "Sam", "last_name": "Walker", "email": email, "password": PASSWORD, "password2": PASSWORD}
    data.update(extra)
    return data


def _sign_up(client, email):
    token = _form_token(client)
    time.sleep(1.2)
    return client.post(
        "/account/register", data=_data(email, form_issued=token, nickname=""), headers={"User-Agent": PERSON_AGENT}
    )


def _user(email):
    from app import db
    from app.models.user import User

    db.session.expire_all()
    return User.find_by_email(email)


def _link_path(message):
    found = re.search(r"https?://[^\s\"'<>]+(/account/confirm-account/[^\s\"'<>]+)", message.body)
    assert found, message.body
    return found.group(1)


def _administrators_organisation(db_session):
    """The organisation ``create_admin.py`` puts the platform administrator in, with that administrator."""
    from app.models.organization import Organization
    from app.models.user import User

    org = Organization.query.filter_by(slug="default").first()
    if org is None:
        org = Organization(name="Default Organization", slug="default")
        db_session.add(org)
        db_session.flush()
    admin = User(first_name="Plat", last_name="Admin", email=_address("admin"), organization_id=org.id,
                 confirmed=True, is_platform_admin=True, is_org_admin=True)
    admin.password = PASSWORD
    db_session.add(admin)
    db_session.flush()
    return org, admin


def test_an_unconfirmed_sign_up_waits_in_an_organisation_that_is_not_the_administrators(app, db_session, mail_on, outbox):
    from app.models.organization import Organization
    from app.services.billing_plans import HOLDING_ORGANISATION_SLUG

    admin_org, _admin = _administrators_organisation(db_session)
    email = _address("wait")
    _sign_up(_client(app), email)

    org = Organization.query.get(_user(email).organization_id)
    assert org.slug == HOLDING_ORGANISATION_SLUG
    assert org.slug not in ("default", admin_org.slug)
    assert org.id != admin_org.id
    assert Organization.query.filter_by(slug=HOLDING_ORGANISATION_SLUG).count() == 1


def test_the_administrators_organisation_still_counts_its_members_with_strangers_waiting(app, db_session, mail_on, outbox):
    from app import db
    from app.services.billing_plans import _count_members
    from app.services.org_service import OrgService

    admin_org, _admin = _administrators_organisation(db_session)
    _sign_up(_client(app), _address("stranger"))

    assert _count_members(db.session.connection(), admin_org.id, "all") >= 1
    assert OrgService.get_org_usage(admin_org.id)["users"] >= 1


def test_only_the_holding_organisation_counts_as_zero_members(app, db_session, mail_on, outbox):
    from app import db
    from app.models.organization import Organization
    from app.services.billing_plans import HOLDING_ORGANISATION_SLUG, _count_members

    _sign_up(_client(app), _address("zero"))
    holding = Organization.query.filter_by(slug=HOLDING_ORGANISATION_SLUG).one()
    assert _count_members(db.session.connection(), holding.id, "all") == 0


def test_the_admin_dashboard_and_the_organisations_list_agree(app, db_session, mail_on, outbox, login_as):
    from flask import template_rendered

    from app.services.billing_plans import HOLDING_ORGANISATION_SLUG

    admin_org, admin = _administrators_organisation(db_session)
    _sign_up(_client(app), _address("agree"))

    seen = []

    def _record(sender, template, context, **extra):
        seen.append((template.name, {k: context[k] for k in ("total_users", "unconfirmed_users", "organizations") if k in context}))

    template_rendered.connect(_record, app)
    try:
        client = _client(app)
        login_as(client, admin)
        assert client.get("/admin/").status_code == 200
        login_as(client, admin)
        assert client.get("/admin/organizations").status_code == 200
    finally:
        template_rendered.disconnect(_record, app)

    dashboard = next(ctx for name, ctx in seen if name == "admin/index.html")
    listing = next(ctx for name, ctx in seen if name == "admin/organizations/list.html")["organizations"]
    own = next(row for row in listing if row["org"].id == admin_org.id)
    holding = next(row for row in listing if row["org"].slug == HOLDING_ORGANISATION_SLUG)
    assert own["holding"] is False
    assert own["user_count"] == dashboard["total_users"]
    assert dashboard["unconfirmed_users"] == 0
    assert holding["holding"] is True and holding["user_count"] == 0


def test_the_move_takes_held_strangers_out_of_the_administrators_organisation_and_no_one_else(db_session):
    import importlib.util
    from pathlib import Path

    from app import db
    from app.models.organization import Organization
    from app.models.user import User
    from app.services.billing_plans import HOLDING_ORGANISATION_SLUG

    path = Path(__file__).resolve().parent.parent / "migrations" / "versions" / "20261103_holding_organisation.py"
    spec = importlib.util.spec_from_file_location("holding_organisation_revision", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    admin_org, admin = _administrators_organisation(db_session)
    stranger = User(first_name="S", last_name="T", email=_address("held"), organization_id=admin_org.id, confirmed=False)
    stranger.password = PASSWORD
    member = User(first_name="M", last_name="B", email=_address("member"), organization_id=admin_org.id, confirmed=True)
    member.password = PASSWORD
    db_session.add_all([stranger, member])
    db_session.flush()

    migration.ensure_holding_organisation(db.session.connection())
    migration.ensure_holding_organisation(db.session.connection())  # a second run changes nothing
    migration.move_held_users(db.session.connection())
    db.session.expire_all()

    holding = Organization.query.filter_by(slug=HOLDING_ORGANISATION_SLUG).one()
    assert User.query.get(stranger.id).organization_id == holding.id
    assert User.query.get(member.id).organization_id == admin_org.id
    assert User.query.get(admin.id).organization_id == admin_org.id


def test_admin_set_password_creates_the_workspace_of_a_held_account(app, db_session, mail_on, outbox):
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.modules.admin.v2.services.admin_user_service_v2 import AdminUserService
    from app.services.billing_plans import HOLDING_ORGANISATION_SLUG

    email = _address("setpw")
    _sign_up(_client(app), email)
    user = _user(email)
    assert Organization.query.get(user.organization_id).slug == HOLDING_ORGANISATION_SLUG

    AdminUserService.set_user_password(user, "Another-horse-7")

    user = _user(email)
    org = Organization.query.get(user.organization_id)
    assert user.confirmed is True
    assert org.slug != HOLDING_ORGANISATION_SLUG
    assert OrgRole.query.filter_by(user_id=user.id, organization_id=org.id).count() == 1


def test_a_failed_workspace_leaves_the_account_unconfirmed_and_the_link_usable(app, db_session, mail_on, outbox, monkeypatch):
    from app.models.organization import Organization
    from app.modules.account.services.account_service import AccountService
    from app.services.billing_plans import HOLDING_ORGANISATION_SLUG

    email = _address("fails")
    _sign_up(_client(app), email)
    path = _link_path(outbox[0])
    token = path.rsplit("/", 1)[-1]
    original = AccountService._new_organisation

    def _broken(*args, **kwargs):
        raise RuntimeError("no workspace today")

    monkeypatch.setattr(AccountService, "_new_organisation", staticmethod(_broken))
    user, message = AccountService.confirm_account(token)

    assert user is None and "try" in message.lower()
    held = _user(email)
    assert held.confirmed is False
    assert Organization.query.get(held.organization_id).slug == HOLDING_ORGANISATION_SLUG

    monkeypatch.setattr(AccountService, "_new_organisation", original)
    user, _message = AccountService.confirm_account(token)
    assert user is not None and _user(email).confirmed is True
    assert Organization.query.get(_user(email).organization_id).slug != HOLDING_ORGANISATION_SLUG


def test_a_stamp_taken_from_one_session_is_refused_in_another(app, db_session, mail_on, outbox, checks_on):
    stolen = _form_token(_client(app))
    time.sleep(1.2)
    email = _address("replay")

    resp = _client(app).post(
        "/account/register", data=_data(email, form_issued=stolen, nickname=""), headers={"User-Agent": PERSON_AGENT}
    )

    assert resp.status_code == 400
    assert _user(email) is None
    assert "expired" in resp.get_data(as_text=True).lower()


def test_a_stamp_is_accepted_in_the_session_that_drew_the_form(app, db_session, mail_on, outbox, checks_on):
    email = _address("own")
    resp = _sign_up(_client(app), email)
    assert resp.status_code == 302
    assert _user(email) is not None


@pytest.mark.parametrize(
    "agent",
    [
        "Mozilla/5.0 (Linux; Android 11; Cubot X20 Pro) AppleWebKit/537.36 Chrome/110.0 Mobile Safari/537.36",
        "Mozilla/5.0 (Linux; Android 10; CUBOT_P40) AppleWebKit/537.36 Chrome/108.0 Mobile Safari/537.36",
    ],
)
def test_a_real_phone_agent_is_not_taken_for_a_bot(app, agent):
    from app.services import signup_guard

    with app.test_request_context("/account/register", method="POST", headers={"User-Agent": agent}):
        assert signup_guard._BOT_AGENT.search(agent) is None


@pytest.mark.parametrize(
    "agent",
    [
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
        "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
        "Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)",
        "WebslockBot/1.0",
        "Mozilla/5.0 (compatible; SomeCrawler/2.1)",
        "Scrapy/2.11 (+https://scrapy.org)",
        "bot",
    ],
)
def test_a_named_crawler_is_still_refused(agent):
    from app.services import signup_guard

    assert signup_guard._BOT_AGENT.search(agent) is not None
