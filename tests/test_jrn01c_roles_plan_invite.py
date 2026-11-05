"""One role per member on every screen, a plan page with no button that cannot finish,
an invite form that keeps what was typed, the person's own settings behind the avatar
menu, and plain words on the AI settings and permissions screens (journeys J6 and J7)."""
import re

import pytest

from tests.test_team_invite_acceptance import _make_org, _make_user


def _rows(html):
    return re.findall(r"<tr\b.*?</tr>", html, flags=re.S)


def _text(fragment):
    return " ".join(re.sub(r"<[^>]+>", " ", fragment).split())


def _seed(db_session, login_as, client):
    from app.models.org_role import OrgRole

    from app.services import billing_plans

    org = _make_org(db_session, "R")
    billing_plans.set_contract_plan(org, "enterprise", None)  # room for the whole team
    admin = _make_user(db_session, org, org_admin=True)
    admin.mfa_enabled = True  # an administrator with others in the organisation is enrolled in two-factor
    members = {}
    for role in ("architect", "viewer", "architect"):
        u = _make_user(db_session, org)
        OrgRole.set_role(org.id, u.id, role, granted_by_id=admin.id)
        members[u.email] = role
    db_session.commit()
    login_as(client, admin)
    return admin, members


def test_registered_users_and_team_show_the_same_role_for_every_member(app, db_session, login_as, client):
    admin, members = _seed(db_session, login_as, client)
    team = client.get("/admin/team").get_data(as_text=True)
    login_as(client, admin)
    users = client.get("/admin/users").get_data(as_text=True)
    expected = {e: r.replace("_", " ").capitalize() for e, r in members.items()}
    for email, label in expected.items():
        team_row = next(r for r in _rows(team) if email in r)
        users_row = next(r for r in _rows(users) if email in r)
        team_role = re.search(r'data-testid="member-role"[^>]*>(.*?)</td>', team_row, re.S)
        users_role = re.search(r'data-testid="user-org-role"[^>]*>(.*?)</td>', users_row, re.S)
        assert users_role, f"Registered Users has no organisation role cell for {email}"
        assert _text(team_role.group(1)) == label
        assert _text(users_role.group(1)) == _text(team_role.group(1)), email


def test_plan_page_offers_no_buy_button_that_cannot_finish(app, db_session, login_as, client):
    admin, _ = _seed(db_session, login_as, client)
    html = client.get("/admin/billing/").get_data(as_text=True)
    assert "Online payment is not open yet" in html
    assert "Choose Startup" not in html and "Choose Team" not in html
    assert "Talk to us about Startup" in html and "Talk to us about Team" in html
    note = re.search(r'data-testid="plans-currency-note"[^>]*>(.*?)</p>', html, re.S)
    assert note and "US dollars" in note.group(1) and "available to use today" in note.group(1)
    assert " of no limit" not in html


def test_invite_form_keeps_what_was_typed_after_an_error(app, db_session, login_as, client, monkeypatch):
    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", None)
    monkeypatch.setitem(app.config, "MAIL_USERNAME", None)
    admin, _ = _seed(db_session, login_as, client)
    resp = client.post("/admin/team/invite", data={"email": "kept.person@example.test", "persona": "business_architect", "role": "architect"})
    html = resp.get_data(as_text=True)
    assert resp.status_code == 503, (resp.status_code, resp.headers.get("Location"))
    email_input = re.search(r"<input[^>]*id=\"email\"[^>]*>", html).group(0)
    assert 'value="kept.person@example.test"' in email_input
    assert re.search(r'<option[^>]*value="architect"[^>]*selected', html) or re.search(r'<option[^>]*selected[^>]*value="architect"', html)
    assert "who already has an account here; they join" not in html


def test_avatar_menu_has_the_persons_settings_and_a_named_administration_entry(app):
    from app.navigation import registry

    by_key = {link.key: link for link in registry.USER_MENU}
    assert "Settings" in by_key["account"].aliases and by_key["account"].endpoint == "account.manage"
    assert by_key["settings"].label == "Organisation administration"
    assert by_key["settings"].endpoint == "admin.index"


def test_ai_settings_and_permissions_use_plain_words(app, db_session, login_as, client):
    admin, _ = _seed(db_session, login_as, client)
    html = client.get("/admin/api-settings").get_data(as_text=True)
    assert "<title>AI settings" in html or "AI settings" in html
    assert "API Settings" not in html
    login_as(client, admin)
    perms = _text(client.get("/admin/team?tab=permissions").get_data(as_text=True))
    for raw in ("ORG_ADMIN", "org_admin", "motivation_elements", "MOTIVATION_ELEMENTS"):
        assert raw not in perms, raw
