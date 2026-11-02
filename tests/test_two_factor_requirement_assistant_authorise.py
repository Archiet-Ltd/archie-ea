"""With "Require two-factor sign-in" on, the assistant's authorise page sends an
administrator who has not enrolled to set-up, and still opens for a member who
does not administer the organisation. Two real organisations: the second one
requires nothing and its administrator is never sent anywhere."""
from __future__ import annotations

from tests.test_mfa_first_week import _other_workspace, _user
from app.services import mfa_service

AUTHORISE = "/oauth/authorize"


def _requiring_organisation(db_session, make_org):
    org = make_org("requires")
    org.settings = {mfa_service.REQUIRE_SETTING: True}
    admin = _user(db_session, org)
    member = _user(db_session, org, admin=False)
    db_session.commit()
    return org, admin, member


def test_an_administrator_is_sent_to_set_up_and_told_why_but_a_member_gets_the_page(
    app, db_session, make_org, login_as
):
    _org, admin, member = _requiring_organisation(db_session, make_org)
    _other_workspace(db_session, make_org)

    client = app.test_client()
    login_as(client, admin)
    resp = client.get(AUTHORISE)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/account/mfa-enrol")
    page = client.get("/account/mfa-enrol").get_data(as_text=True)
    assert "Your organisation requires two-factor sign-in" in page

    other = app.test_client()
    login_as(other, member)
    reached = other.get(AUTHORISE)
    assert reached.status_code == 400
    assert reached.get_json()["error"] == "invalid_request"


def test_an_administrator_of_an_organisation_that_requires_nothing_keeps_the_week(
    app, db_session, make_org, login_as
):
    quiet = make_org("quiet")
    admin = _user(db_session, quiet)
    client = app.test_client()
    login_as(client, admin)
    assert client.get(AUTHORISE).status_code == 400
