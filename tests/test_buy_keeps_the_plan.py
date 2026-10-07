"""Buy on the pricing page keeps the chosen plan and interval through sign-up.

A new visitor goes pricing -> registration -> that plan's checkout step on the
billing page; a visitor with an account goes pricing -> registration page ->
sign-in -> the same step. Nothing the visitor sends is used as a redirect target
unless it is an internal path or a known plan and interval.
"""
import re
import uuid
from urllib.parse import urlparse

import pytest

from tests.test_account_mail_flows import (  # noqa: F401  (fixtures and helpers reused)
    _anonymous,
    _link,
    mail_off,
    mail_on,
    outbox,
)
from tests.test_billing_plans_and_checkout import no_billing  # noqa: F401
from tests.test_team_invite_acceptance import PASSWORD, _make_org, _make_user

@pytest.fixture
def no_mfa(monkeypatch):
    """Sign-in finishes at once; the administrator MFA step has its own test below."""
    from app.services import mfa_service

    monkeypatch.setattr(mfa_service, "required_for", lambda user: False)


CHOICES = [("startup", "month"), ("team", "year")]


def _target(plan, interval):
    return f"/admin/billing/?plan={plan}&interval={interval}#checkout"


def _register(client, email, plan=None, interval=None):
    query = f"?plan={plan}&interval={interval}" if plan else ""
    return client.post("/account/register" + query, data={
        "first_name": "Trial", "last_name": "Founder", "email": email,
        "password": PASSWORD, "password2": PASSWORD,
    })


def _email():
    return "buyer-{}@example.com".format(uuid.uuid4().hex[:8])


def _location(resp):
    return resp.headers["Location"]


def _follow_to_billing(client, resp):
    """Follow redirects as a browser does, stopping at the redirect that names the billing page."""
    hops = 0
    while resp.status_code == 302 and "/admin/billing/" not in _location(resp):
        resp = client.get(_location(resp))
        hops += 1
        assert hops < 5
    return resp


def test_pricing_buttons_lead_to_registration_with_plan_and_interval(app, client):
    """The registration URL below travels inside the click-tracking redirect's
    "next" parameter (app/main/views.py::track_plan_click), so a click both
    logs the plan and still keeps it through sign-up."""
    html = client.get("/pricing").get_data(as_text=True)
    assert "/t/plan-click?plan=startup&amp;next=/account/register?plan%3Dstartup%26interval%3Dyear" in html
    assert "/t/plan-click?plan=startup&amp;next=/account/register?plan%3Dstartup%26interval%3Dmonth" in html
    assert "/t/plan-click?plan=team&amp;next=/account/register?plan%3Dteam%26interval%3Dyear" in html
    assert "/t/plan-click?plan=team&amp;next=/account/register?plan%3Dteam%26interval%3Dmonth" in html
    assert re.search(
        r'<a href="/t/plan-click\?plan=enterprise&amp;next=/contact"[^>]*data-testid="buy-enterprise"', html)
    # Start free carries no plan.
    assert re.search(
        r'<a href="/t/plan-click\?plan=community&amp;next=/account/register"[^>]*>Start free</a>', html)


@pytest.mark.parametrize("plan,interval", CHOICES)
def test_new_visitor_keeps_the_plan_through_registration(
    app, db_session, mail_off, no_billing, plan, interval
):
    client = _anonymous(app)
    page = client.get(f"/account/register?plan={plan}&interval={interval}")
    assert page.status_code == 200
    # The sign-in link on the registration page carries the choice too.
    assert f"/account/login?plan={plan}&amp;interval={interval}" in page.get_data(as_text=True)

    resp = _follow_to_billing(client, _register(client, _email(), plan, interval))
    assert resp.status_code == 302
    assert _location(resp).endswith(_target(plan, interval))

    # Payments are not configured: the plan's page renders with the notice and no error.
    _anonymous(app)  # clears the per-request tenant cache this test's long-lived context keeps
    landed = client.get(f"/admin/billing/?plan={plan}&interval={interval}")
    html = landed.get_data(as_text=True)
    assert landed.status_code == 200
    assert "Online payment is not set up on this installation" in html
    assert 'data-testid="checkout-unavailable"' in html
    assert "/contact" in html


def test_new_visitor_confirming_by_email_lands_on_the_plan(app, db_session, mail_on, outbox, no_billing):
    client = _anonymous(app)
    resp = _register(client, _email(), "team", "year")
    assert _location(resp).endswith("/account/unconfirmed")
    path = _link(outbox[0], "/account/confirm-account/")
    confirmed = client.get(path)
    assert confirmed.status_code == 302
    assert _location(confirmed).endswith(_target("team", "year"))


@pytest.mark.parametrize("plan,interval", CHOICES)
def test_existing_user_keeps_the_plan_through_login(app, db_session, no_billing, no_mfa, plan, interval):
    """Sign-in lands on the plan's checkout step."""
    org = _make_org(db_session, "buy")
    user = _make_user(db_session, org, email=_email())
    db_session.commit()

    client = _anonymous(app)
    login_page = client.get(f"/account/login?plan={plan}&interval={interval}")
    assert f"/account/register?plan={plan}&amp;interval={interval}" in login_page.get_data(as_text=True)

    resp = client.post(f"/account/login?plan={plan}&interval={interval}",
                       data={"email": user.email, "password": PASSWORD})
    assert resp.status_code == 302
    assert _location(resp).endswith(_target(plan, interval))


@pytest.mark.parametrize("plan,interval", CHOICES)
def test_administrator_login_carries_the_plan_through_the_mfa_step(app, db_session, plan, interval):
    org = _make_org(db_session, "buymfa")
    user = _make_user(db_session, org, org_admin=True, email=_email())
    db_session.commit()

    client = _anonymous(app)
    resp = client.post(f"/account/login?plan={plan}&interval={interval}",
                       data={"email": user.email, "password": PASSWORD})
    assert _location(resp).endswith("/account/mfa-challenge")
    with client.session_transaction() as sess:
        assert sess["_mfa_pending_next"].endswith(_target(plan, interval))


def test_signed_in_user_pressing_buy_goes_straight_to_the_plan(app, db_session, login_as, client):
    org = _make_org(db_session, "buy2")
    user = _make_user(db_session, org, org_admin=True, email=_email())
    db_session.commit()
    login_as(client, user)
    resp = client.get("/account/register?plan=team&interval=month")
    assert resp.status_code == 302
    assert _location(resp).endswith(_target("team", "month"))


@pytest.mark.parametrize("evil", [
    "https://evil.example/x", "//evil.example/x", "/\\evil.example", "javascript:alert(1)",
])
def test_off_site_next_is_ignored(app, db_session, no_mfa, evil):
    org = _make_org(db_session, "buy3")
    user = _make_user(db_session, org, email=_email())
    db_session.commit()
    client = _anonymous(app)
    resp = client.post("/account/login", query_string={"next": evil},
                       data={"email": user.email, "password": PASSWORD})
    assert resp.status_code == 302
    assert "evil.example" not in _location(resp)
    assert urlparse(_location(resp)).netloc in ("", "localhost")
    assert _location(resp).endswith("/dashboard/overview")


def test_internal_next_still_works_and_beats_the_plan(app, db_session, no_mfa):
    org = _make_org(db_session, "buy4")
    user = _make_user(db_session, org, email=_email())
    db_session.commit()
    client = _anonymous(app)
    resp = client.post("/account/login?next=/dashboard/overview&plan=team&interval=year",
                       data={"email": user.email, "password": PASSWORD})
    assert _location(resp).endswith("/dashboard/overview")


@pytest.mark.parametrize("query", [
    "plan=bogus&interval=year",
    "plan=enterprise&interval=year",   # sold by contract, not by checkout
    "plan=..%2F..%2Fadmin&interval=year",
])
def test_unknown_plan_falls_back_to_pricing(app, db_session, mail_off, query):
    client = _anonymous(app)
    resp = client.get("/account/register?" + query)
    assert resp.status_code == 302
    assert _location(resp).endswith("/pricing")
    posted = client.post("/account/register?" + query, data={
        "first_name": "A", "last_name": "B", "email": _email(),
        "password": PASSWORD, "password2": PASSWORD,
    })
    assert _location(posted).endswith("/pricing")


def test_unknown_interval_means_annual(app, db_session, mail_off):
    client = _anonymous(app)
    resp = _follow_to_billing(client, _register(client, _email(), "team", "forever"))
    assert _location(resp).endswith("/admin/billing/?plan=team&interval=year#checkout")


def test_start_free_lands_on_the_normal_page_with_no_plan(app, db_session, mail_off):
    client = _anonymous(app)
    assert client.get("/account/register").status_code == 200
    resp = _register(client, _email())
    assert resp.status_code == 302
    assert "plan=" not in _location(resp) and "billing" not in _location(resp)
    with client.session_transaction() as sess:
        assert "_buy_intent" not in sess
