"""The owner of a new organisation opens Settings; refused and failed addresses show a usable page.

Three faults, each shown by a person's own request:
  - the person who signed up and owns a new organisation was refused at /settings;
  - three AI chat administration addresses answered a refusal with no heading or way back;
  - the portfolio patterns address showed a bare JSON sentence to a browser on a timeout.
"""
import uuid

import pytest

from tests.test_account_mail_flows import _anonymous, mail_off  # noqa: F401  (fixture and helper reused)
from tests.test_team_invite_acceptance import PASSWORD

NO_ACCESS = "You don&#39;t have access to this page"
BROWSER = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


class _Person:
    """A signed-in client that forgets who the last request ran as, so two people can take turns."""

    def __init__(self, client):
        self._client = client

    def _forget(self):
        from flask import g, has_app_context

        if has_app_context():
            for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
                if hasattr(g, cached):
                    delattr(g, cached)

    def get(self, *args, **kwargs):
        self._forget()
        return self._client.get(*args, **kwargs)

    def post(self, *args, **kwargs):
        self._forget()
        return self._client.post(*args, **kwargs)


def _sign_up(app, label):
    """A real sign-up: returns a logged-in client and the new owner's id."""
    from app.models.user import User

    client = _anonymous(app)
    email = "owner-%s-%s@example.com" % (label, uuid.uuid4().hex[:8])
    resp = client.post("/account/register", data={
        "first_name": "Casey", "last_name": label, "email": email,
        "password": PASSWORD, "password2": PASSWORD,
    })
    assert resp.status_code == 302, resp.status_code
    user = User.query.filter_by(email=email).one()
    user.confirmed = True
    from app import db

    db.session.commit()
    resp = client.post("/account/login", data={"email": email, "password": PASSWORD})
    assert resp.status_code == 302, resp.status_code
    return _Person(client), user


@pytest.fixture
def two_owners(app, db_session, mail_off, monkeypatch):
    from app.models.user import Role
    from app.services import mfa_service

    Role.insert_roles()
    monkeypatch.setattr(mfa_service, "required_for", lambda user: False)
    a_client, a = _sign_up(app, "alpha")
    b_client, b = _sign_up(app, "bravo")
    assert a.organization_id != b.organization_id
    return a_client, a, b_client, b


def test_the_owner_of_a_new_organisation_opens_settings(two_owners):
    a_client, a, _b_client, _b = two_owners
    resp = a_client.get("/settings")
    assert resp.status_code == 200, resp.status_code
    assert "System Settings" in resp.get_data(as_text=True)


def test_the_owner_sees_the_saved_values_but_cannot_change_them(two_owners):
    a_client, *_ = two_owners
    assert a_client.get("/api/system-settings").status_code == 200
    resp = a_client.post("/api/system-settings/save", json={"settings": {"app-name": "Mine"}})
    assert resp.status_code == 403
    html = a_client.get("/settings").get_data(as_text=True)
    assert 'data-testid="settings-read-only"' in html
    assert 'data-testid="btn-save-all"' not in html


def test_a_member_of_an_owners_organisation_without_an_administrator_role_is_refused(two_owners, app):
    """Owning an organisation opens Settings; belonging to one as a viewer does not."""
    from app.models.user import Role, User
    from app import db

    a_client, a, _b_client, b = two_owners
    # A member of organisation B who is not its owner holds no administrator role.
    viewer_role = Role.query.filter_by(name="Viewer").first()
    member = User(email="member-%s@example.com" % uuid.uuid4().hex[:8], first_name="M", last_name="B",
                  organization_id=b.organization_id, confirmed=True, role=viewer_role)
    member.password = PASSWORD
    db.session.add(member)
    db.session.commit()
    client = _Person(_anonymous(app))
    assert client.post("/account/login", data={"email": member.email, "password": PASSWORD}).status_code == 302
    assert client.get("/settings").status_code == 403


def test_a_second_organisations_owner_is_admitted_on_their_own_settings_and_is_still_not_a_platform_admin(two_owners):
    from app.middleware.tenant_decorators import is_platform_admin

    _a_client, a, b_client, b = two_owners
    assert b_client.get("/settings").status_code == 200
    assert not is_platform_admin(a) and not is_platform_admin(b)


@pytest.mark.parametrize("path", [
    "/ai-chat/admin/analytics", "/ai-chat/admin/prompts", "/ai-chat/run-records",
])
def test_a_refused_chat_admin_page_is_the_standard_refusal_page(app, db_session, path):
    from app import db
    from app.models.user import Role, User
    from tests.test_account_mail_flows import _anonymous as anonymous

    org_user = User(email="viewer-%s@example.com" % uuid.uuid4().hex[:8], first_name="V", last_name="W",
                    confirmed=True, role=Role.query.filter_by(name="Viewer").first())
    org_user.password = PASSWORD
    from app.models.organization import Organization

    org = Organization(name="Viewer org %s" % uuid.uuid4().hex[:8], slug="v-%s" % uuid.uuid4().hex[:10])
    db.session.add(org)
    db.session.flush()
    org_user.organization_id = org.id
    db.session.add(org_user)
    db.session.commit()
    client = _Person(anonymous(app))
    assert client.post("/account/login", data={"email": org_user.email, "password": PASSWORD}).status_code == 302
    page = client.get(path, headers=BROWSER)
    assert page.status_code == 403
    html = page.get_data(as_text=True)
    assert NO_ACCESS in html
    assert "<h1" in html
    assert "/dashboard/overview" in html
    # A script still gets JSON.
    script = client.get(path, headers={"Accept": "application/json"})
    assert script.status_code == 403
    assert script.is_json


def test_a_browser_that_times_out_on_the_patterns_address_gets_a_page(app, two_owners, monkeypatch):
    from app.services import application_pattern_classifier_service as svc

    def timed_out(self, *a, **k):
        raise svc.LLMClassificationTimeoutError("no answer")

    monkeypatch.setattr(svc.ApplicationPatternClassifierService, "classify_portfolio", timed_out)
    a_client, *_ = two_owners
    path = "/api/ea-workflows/sa/application-patterns"
    page = a_client.get(path, headers=BROWSER)
    assert page.status_code == 504
    html = page.get_data(as_text=True)
    assert "The AI provider did not answer in time. Try again in a moment." in html
    assert "<h1" in html
    assert "/dashboard/overview" in html
    script = a_client.get(path, headers={"Accept": "application/json"})
    assert script.status_code == 504
    assert script.get_json()["error"] == "The AI provider did not answer in time. Try again in a moment."
