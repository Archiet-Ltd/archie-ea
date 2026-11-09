"""A signed-in member who is not an administrator and clicks Buy is told who buys, and how to reach them."""

from __future__ import annotations

import pytest

from tests.test_billing_plans_and_checkout import _org, _user, no_billing  # noqa: F401


def _people(db_session):
    org_a, org_b = _org(db_session, "buy-a"), _org(db_session, "buy-b")
    member = _user(db_session, org_a)
    admin_a = _user(db_session, org_a, admin=True)
    admin_b = _user(db_session, org_b, admin=True)
    admin_a.first_name, admin_a.last_name = "Priya", "Nair"
    admin_b.first_name, admin_b.last_name = "Other", "Company"
    db_session.commit()
    return member, admin_a, admin_b


@pytest.mark.parametrize("address", [
    "/account/register?plan=team&interval=year",
    "/admin/billing/?plan=team&interval=year",
])
def test_a_member_who_clicks_buy_is_told_which_administrator_to_ask(app, db_session, client, login_as, no_billing, address):
    member, admin_a, admin_b = _people(db_session)
    with app.app_context():
        login_as(client, member)
        page = client.get(address, follow_redirects=True)
    html = page.get_data(as_text=True)
    assert "An administrator of your organisation buys the plan" in html
    assert "Priya Nair" in html and admin_a.email in html
    assert "mailto:" + admin_a.email in html
    # the other organisation's administrator is never named
    assert admin_b.email not in html and "Other Company" not in html
    assert "Your role does not include this page" not in html


def test_an_organisation_with_no_administrator_is_pointed_to_contact_us(app, db_session, client, login_as, no_billing):
    org = _org(db_session, "buy-none")
    member = _user(db_session, org)
    db_session.commit()
    with app.app_context():
        login_as(client, member)
        html = client.get("/admin/billing/", follow_redirects=True).get_data(as_text=True)
    assert "An administrator of your organisation buys the plan" in html
    assert 'href="/contact"' in html


def test_an_administrator_still_reaches_the_plan_page(app, db_session, client, login_as, no_billing):
    _member, admin_a, _admin_b = _people(db_session)
    with app.app_context():
        login_as(client, admin_a)
        page = client.get("/admin/billing/?plan=team&interval=year")
    assert page.status_code == 200
    assert "buys the plan" not in page.get_data(as_text=True)
