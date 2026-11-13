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


def test_a_disabled_administrator_is_never_named_and_the_member_is_sent_to_contact_us(
        app, db_session, client, login_as, no_billing):
    from datetime import datetime

    member, admin_a, admin_b = _people(db_session)
    admin_a.disabled_at = datetime.utcnow()
    db_session.commit()
    with app.app_context():
        login_as(client, member)
        html = client.get("/admin/billing/", follow_redirects=True).get_data(as_text=True)
    assert admin_a.email not in html and "Priya Nair" not in html
    assert admin_b.email not in html
    assert 'href="/contact"' in html


def test_a_disabled_administrator_is_skipped_for_the_one_who_can_sign_in(app, db_session):
    from datetime import datetime

    from app.services.administrator_guard import administrators_to_ask

    org_a, org_b = _org(db_session, "buy-skip-a"), _org(db_session, "buy-skip-b")
    gone = _user(db_session, org_a, admin=True)
    here = _user(db_session, org_a, admin=True)
    other = _user(db_session, org_b, admin=True)
    gone.disabled_at = datetime.utcnow()
    db_session.commit()
    with app.app_context():
        assert [a["email"] for a in administrators_to_ask(org_a.id)] == [here.email]
        assert [a["email"] for a in administrators_to_ask(org_b.id)] == [other.email]


def test_an_administrator_with_no_name_shows_the_address_once(app, db_session, client, login_as, no_billing):
    org = _org(db_session, "buy-nameless")
    member = _user(db_session, org)
    admin = _user(db_session, org, admin=True)
    admin.first_name = admin.last_name = None
    db_session.commit()
    with app.app_context():
        login_as(client, member)
        html = client.get("/admin/billing/", follow_redirects=True).get_data(as_text=True)
    assert "Ask %s to buy the plan" % admin.email in html
    assert "%s (%s)" % (admin.email, admin.email) not in html
    assert "Email " + admin.email in html


def test_an_administrator_still_reaches_the_plan_page(app, db_session, client, login_as, no_billing):
    _member, admin_a, _admin_b = _people(db_session)
    with app.app_context():
        login_as(client, admin_a)
        page = client.get("/admin/billing/?plan=team&interval=year")
    assert page.status_code == 200
    assert "buys the plan" not in page.get_data(as_text=True)


def _invite_administrator(app, db_session, org, inviter, monkeypatch, email):
    """Invite an administrator through the real invitation path, as the admin new-user form does."""
    from app.models.user import Role
    from app.modules.account.services import invitation_service

    monkeypatch.setattr(invitation_service, "mail_available", lambda *a, **k: True)
    monkeypatch.setattr(invitation_service, "deliver_email", lambda **k: (True, None))
    role = Role.query.filter_by(name="Administrator").first()
    with app.test_request_context("/"):
        row, _delivered, _error = invitation_service.invite_new_person(
            org.id, inviter, email, org_role="viewer", first_name="Ivy", last_name="Invited", platform_role=role)
    return row.user


def test_an_administrator_who_has_not_taken_up_the_invitation_is_never_named(
        app, db_session, client, login_as, no_billing, monkeypatch):
    org_a, org_b = _org(db_session, "buy-inv-a"), _org(db_session, "buy-inv-b")
    member = _user(db_session, org_a)
    inviter = _user(db_session, org_a, admin=True)
    other = _user(db_session, org_b, admin=True)
    invited = _invite_administrator(app, db_session, org_a, inviter, monkeypatch, "ivy.invited@example.com")
    # the only administrator who can sign in is switched off: the invited one is all that is left
    from datetime import datetime

    inviter.disabled_at = datetime.utcnow()
    db_session.commit()
    from app.services.administrator_guard import administrators_to_ask

    with app.app_context():
        assert administrators_to_ask(org_a.id) == []
        assert [a["email"] for a in administrators_to_ask(org_b.id)] == [other.email]
        login_as(client, member)
        html = client.get("/admin/billing/", follow_redirects=True).get_data(as_text=True)
    assert invited.email not in html and "Ivy Invited" not in html
    assert other.email not in html
    assert 'href="/contact"' in html


def test_an_invited_administrator_is_skipped_for_the_one_who_can_sign_in(app, db_session, monkeypatch):
    from app.services.administrator_guard import administrators_to_ask

    org = _org(db_session, "buy-inv-skip")
    active = _user(db_session, org, admin=True)
    _invite_administrator(app, db_session, org, active, monkeypatch, "ivy.skipped@example.com")
    with app.app_context():
        assert [a["email"] for a in administrators_to_ask(org.id)] == [active.email]


def test_the_contact_us_button_carries_the_mail_icon(app, db_session, client, login_as, no_billing):
    import re

    org = _org(db_session, "buy-icon")
    member = _user(db_session, org)
    db_session.commit()
    with app.app_context():
        login_as(client, member)
        html = client.get("/admin/billing/", follow_redirects=True).get_data(as_text=True)
    button = re.search(r'<a[^>]*href="/contact"[^>]*>(.*?)</a>', html, re.S)
    assert button, "the error page has no Contact us button"
    assert "lucide-mail" in button.group(1) or 'data-lucide="mail"' in button.group(1), button.group(1)
