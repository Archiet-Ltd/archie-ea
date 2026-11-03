"""An unconfirmed account sits in the shared holding organisation and can reach nothing but the confirm prompt.

Every unconfirmed sign-up shares one organisation until its address is confirmed, so isolation between
those people is proved by driving the routes as a signed-in unconfirmed account, next to a confirmed
person who owns a real organisation with members.
"""
import uuid

import pytest


PASSWORD = "Correct-horse-9"
SENDER = "no-reply@example.com"
PERSON_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
PROMPT = "/account/unconfirmed"
# What a held account may open: the confirm prompt and its resend, the link itself, and signing out.
ALLOWED = {
    "account.unconfirmed",
    "account.confirm_request",
    "account.confirm",
    "account.logout",
    "account.login",
    "account.register",
    "account.reset_password_request",
    "account.reset_password",
    "account.session_keepalive",
    "account.join",
    "account.join_from_invite",
    "account.sso_login",
    "account.sso_callback",
    "static",
}


@pytest.fixture
def mail_on(app, monkeypatch):
    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", SENDER)


def _fresh(app):
    from flask import g, has_app_context

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)
    return app.test_client()


def _sign_up(app, tag):
    """A real sign-up through the form; the returned client is signed in and unconfirmed."""
    email = "hold-{}-{}@example.com".format(tag, uuid.uuid4().hex[:8])
    client = _fresh(app)
    resp = client.post(
        "/account/register",
        data={"first_name": tag.title(), "last_name": "Holder", "email": email, "password": PASSWORD, "password2": PASSWORD},
        headers={"User-Agent": PERSON_AGENT},
    )
    assert resp.status_code == 302 and resp.headers["Location"].endswith(PROMPT)
    return client, email


@pytest.fixture
def held(app, db_session, mail_on):
    """Two unconfirmed people in the holding organisation, and a confirmed organisation with members."""
    from app.models.user import User

    first, first_email = _sign_up(app, "first")
    second, second_email = _sign_up(app, "second")
    a, b = User.find_by_email(first_email), User.find_by_email(second_email)
    assert a.organization_id == b.organization_id and a.confirmed is False
    return {"first": first, "second": second, "first_email": first_email, "second_email": second_email, "holding": a.organization_id}


def _as(app, client, path, method="GET"):
    """One request as *client*'s person. Flask-Login caches the resolved user on ``g``, which the
    test's app context keeps between requests, so the cache is cleared first."""
    _fresh(app)
    return client.open(path, method=method)


def _sample_url(rule):
    args = {}
    for name, conv in rule._converters.items():
        kind = conv.__class__.__name__
        if kind == "IntegerConverter":
            args[name] = 1
        elif kind == "UUIDConverter":
            args[name] = str(uuid.uuid4())
        elif kind == "AnyConverter":
            args[name] = conv.regex.split("|")[0].strip("()") if getattr(conv, "regex", "") else "x"
        else:
            args[name] = "x"
    try:
        return rule.build(args, append_unknown=False)[1]
    except ValueError:
        return None


def _login_required(app, rule, method):
    resp = _as(app, app.test_client(), _sample_url(rule), method)
    return resp.status_code in (401, 403) or (
        resp.status_code in (301, 302, 303, 307, 308) and "login" in resp.headers.get("Location", "")
    )


def test_a_held_account_is_sent_to_the_confirm_prompt_from_every_page_api_and_assistant_route(app, held):
    asked = 0
    leaked = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint in ALLOWED or rule.endpoint.startswith("static"):
            continue
        if _sample_url(rule) is None:
            continue
        for method in sorted(rule.methods & {"GET", "POST", "PUT", "PATCH", "DELETE"}):
            if not _login_required(app, rule, method):
                continue
            asked += 1
            resp = _as(app, held["first"], _sample_url(rule), method)
            location = resp.headers.get("Location", "")
            if not (resp.status_code in (302, 303) and location.endswith(PROMPT)):
                leaked.append("{} {} -> {} {}".format(method, rule.rule, resp.status_code, location))
    assert asked > 200, "the sweep found too few signed-in routes to mean anything"
    assert not leaked, "a held account reached:\n" + "\n".join(leaked[:40])


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/account/manage"),
        ("POST", "/account/switch-organization"),
        ("POST", "/account/manage/preferences"),
        ("POST", "/account/manage/notification-preferences"),
        ("GET", "/account/manage/change-password"),
        ("GET", "/account/manage/change-email"),
        ("POST", "/account/invitation/1/accept"),
        ("POST", "/account/invitation/1/decline"),
    ],
)
def test_the_account_pages_that_belong_to_a_workspace_are_closed_to_a_held_account(app, held, method, path):
    resp = _as(app, held["first"], path, method)
    assert resp.status_code in (302, 303) and resp.headers["Location"].endswith(PROMPT), (method, path, resp.status_code)


def test_an_unknown_address_does_not_crash_the_gate_for_a_held_account(app, held):
    resp = _as(app, held["first"], "/no-such-page-" + uuid.uuid4().hex[:6])
    assert resp.status_code in (302, 404)


def test_a_held_account_never_sees_another_held_account(app, held):
    page = _as(app, held["first"], PROMPT).get_data(as_text=True)
    assert held["second_email"] not in page
    assert held["first_email"].lower() in page.lower() or "confirm" in page.lower()


def test_the_confirm_prompt_names_nothing_the_holding_organisation_holds(app, held):
    page = _as(app, held["first"], PROMPT).get_data(as_text=True)
    from app.models import Organization

    holding = Organization.query.get(held["holding"])
    assert holding.name not in page or holding.name == ""
    assert held["second_email"] not in page


def test_a_held_account_is_not_counted_as_a_member_where_a_person_reads_it(app, db_session, held, login_as, make_org):
    """The holding organisation has no members: its seat count, usage and platform list all read zero."""
    from app import db
    from app.services.billing_plans import _count_members
    from app.services.org_service import OrgService

    holding = held["holding"]
    assert _count_members(db.session.connection(), holding, "all") == 0
    assert OrgService.get_org_usage(holding)["users"] == 0

    from app.models.user import User

    admin = User(first_name="Plat", last_name="Admin", email="plat-{}@example.com".format(uuid.uuid4().hex[:8]),
                 organization_id=make_org().id, confirmed=True, is_platform_admin=True, is_org_admin=True)
    admin.password = PASSWORD
    db.session.add(admin)
    db.session.flush()
    client = _fresh(app)
    login_as(client, admin)
    page = client.get("/admin/organizations").get_data(as_text=True)
    row = page[page.index("Default Organization"):]
    row = row[: row.index("</tr>")] if "</tr>" in row else row[:2000]
    assert "unconfirmed sign-ups wait here" in row
    assert "0 of" in " ".join(row.split()) or ">0<" in " ".join(row.split()).replace("> ", ">").replace(" <", "<")


def test_a_confirmed_organisation_still_counts_its_members(app, db_session, make_org):
    from app import db
    from app.models.user import User
    from app.services.billing_plans import _count_members
    from app.services.org_service import OrgService

    org_a, org_b = make_org(), make_org()
    for org, count in ((org_a, 2), (org_b, 1)):
        for n in range(count):
            db.session.add(User(first_name="M", last_name=str(n), email="m{}-{}@example.com".format(n, uuid.uuid4().hex[:8]),
                                organization_id=org.id, confirmed=True))
    db.session.flush()
    assert _count_members(db.session.connection(), org_a.id, "all") == 2
    assert _count_members(db.session.connection(), org_b.id, "all") == 1
    assert OrgService.get_org_usage(org_a.id)["users"] == 2
