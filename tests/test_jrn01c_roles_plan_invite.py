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


ROLE_WORDS = {"org_admin": "Org admin", "architect": "Architect", "viewer": "Viewer"}


def _cell(row, testid):
    found = re.search(r'data-testid="%s"[^>]*>(.*?)</td>' % testid, row, re.S)
    assert found, f"no {testid} cell in {row[:200]}"
    return _text(found.group(1))


def test_registered_users_and_team_show_the_same_role_for_every_member(app, db_session, login_as, client):
    admin, members = _seed(db_session, login_as, client)
    other_org = _make_org(db_session, "S")
    stranger = _make_user(db_session, other_org)
    db_session.commit()
    login_as(client, admin)
    team = client.get("/admin/team").get_data(as_text=True)
    login_as(client, admin)
    users = client.get("/admin/users").get_data(as_text=True)
    assert stranger.email not in team and stranger.email not in users
    for email, role in members.items():
        team_row = next(r for r in _rows(team) if email in r)
        users_row = next(r for r in _rows(users) if email in r)
        assert _cell(team_row, "member-role") == ROLE_WORDS[role]
        assert _cell(users_row, "user-org-role") == ROLE_WORDS[role], email
    # One role column on Registered Users, not a second "account type" beside it.
    assert "Account type" not in users and "Account Type" not in users


def test_invitee_reads_invited_with_the_role_they_will_get(app, db_session, login_as, client):
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation

    admin, _ = _seed(db_session, login_as, client)
    invitee = _make_user(db_session, db_session.get(type(admin), admin.id).organization)
    OrgRole.query.filter_by(organization_id=admin.organization_id, user_id=invitee.id).delete()
    db_session.add(PendingInvitation(organization_id=admin.organization_id, user_id=invitee.id, role="architect",
                                     invited_by=admin.id))
    db_session.commit()
    login_as(client, admin)
    users = client.get("/admin/users").get_data(as_text=True)
    row = next(r for r in _rows(users) if invitee.email in r)
    assert _cell(row, "user-org-role") == "Invited as architect"


def test_expired_invitation_does_not_read_invited(app, db_session, login_as, client):
    from datetime import timedelta

    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation, _utcnow

    admin, _ = _seed(db_session, login_as, client)
    org = db_session.get(type(admin), admin.id).organization
    open_one = _make_user(db_session, org)
    lapsed = _make_user(db_session, org)
    for person in (open_one, lapsed):
        OrgRole.query.filter_by(organization_id=admin.organization_id, user_id=person.id).delete()
    db_session.add(PendingInvitation(organization_id=admin.organization_id, user_id=open_one.id, role="architect",
                                     invited_by=admin.id, expires_at=_utcnow() + timedelta(days=3)))
    db_session.add(PendingInvitation(organization_id=admin.organization_id, user_id=lapsed.id, role="architect",
                                     invited_by=admin.id, expires_at=_utcnow() - timedelta(days=1)))
    db_session.commit()
    login_as(client, admin)
    users = client.get("/admin/users").get_data(as_text=True)
    rows = _rows(users)
    assert _cell(next(r for r in rows if open_one.email in r), "user-org-role") == "Invited as architect"
    assert not _cell(next(r for r in rows if lapsed.email in r), "user-org-role").startswith("Invited")


def test_saved_invitation_without_email_says_how_the_person_joins(app, db_session, login_as, client):
    from app.models.org_role import OrgRole
    from app.models.pending_invitation import PendingInvitation

    admin, _ = _seed(db_session, login_as, client)
    invitee = _make_user(db_session, db_session.get(type(admin), admin.id).organization)
    OrgRole.query.filter_by(organization_id=admin.organization_id, user_id=invitee.id).delete()
    db_session.add(PendingInvitation(organization_id=admin.organization_id, user_id=invitee.id, role="architect",
                                     invited_by=admin.id))
    db_session.commit()
    login_as(client, admin)
    page = client.get("/admin/team").get_data(as_text=True)
    note = re.search(r'data-invitation-kind="has-account">(.*?)</span>', page, re.S)
    assert note, "no note on the invitation of someone with an account"
    assert "e-mailed link" not in note.group(1)
    assert "saved as not yet accepted" in note.group(1)


def test_plan_card_opens_a_panel_that_says_payment_opens_soon(app, db_session, login_as, client):
    admin, _ = _seed(db_session, login_as, client)
    html = client.get("/admin/billing/").get_data(as_text=True)
    assert re.search(r'<a[^>]*href="[^"]*plan=team[^"]*#checkout"[^>]*>\s*Choose Team', html), "no Choose Team link"
    note = re.search(r'data-testid="plans-currency-note"[^>]*>(.*?)</p>', html, re.S)
    assert note and "US dollars" in note.group(1) and "available to use today" in note.group(1)
    assert " of no limit" not in html
    login_as(client, admin)
    panel = client.get("/admin/billing/?plan=team").get_data(as_text=True)
    unavailable = re.search(r'data-testid="checkout-unavailable"[^>]*>(.*?)</p>', panel, re.S)
    assert unavailable and "Payment opens soon" in _text(unavailable.group(1))
    assert 'href="/contact' in unavailable.group(1) or "contact" in unavailable.group(1).lower()
    assert "data-checkout-legal" in panel and 'data-legal-link="terms"' in panel


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


def test_without_mail_an_existing_account_invite_says_what_really_happens(app, db_session, login_as, client, monkeypatch):
    monkeypatch.setitem(app.config, "MAIL_DEFAULT_SENDER", None)
    monkeypatch.setitem(app.config, "MAIL_USERNAME", None)
    admin, _ = _seed(db_session, login_as, client)
    page = _text(client.get("/admin/team").get_data(as_text=True))
    assert "joins the next time they sign in" not in page
    assert "saved below as not yet accepted" in page and "Resend" in page
    other_org = _make_org(db_session, "T")
    existing = _make_user(db_session, other_org)
    db_session.commit()
    login_as(client, admin)
    resp = client.post("/admin/team/invite", data={"email": existing.email, "role": "architect"}, follow_redirects=True)
    after = _text(resp.get_data(as_text=True))
    assert "they have not been told" in after and "use Resend once e-mail is set up" in after
    assert existing.email in after  # the saved invitation is listed, as the sentence says
    # And it did not add them: they hold no role here until they take the invitation up.
    from app.models.org_role import OrgRole

    assert OrgRole.get_role(admin.organization_id, existing.id) is None


def test_avatar_menu_has_settings_for_a_platform_administrator_only(app, db_session, login_as, client):
    admin, _ = _seed(db_session, login_as, client)
    plain = client.get("/admin/team").get_data(as_text=True)
    assert 'data-testid="user-menu-platform_settings"' not in plain
    assert "Organisation administration" in plain
    admin.is_platform_admin = True
    db_session.commit()
    login_as(client, admin)
    shown = client.get("/admin/team").get_data(as_text=True)
    link = re.search(r'<a[^>]*data-testid="user-menu-platform_settings"[^>]*>(.*?)</a>', shown, re.S)
    assert link and _text(link.group(1)) == "Settings"
    assert re.search(r'href="/settings/?"[^>]*data-testid="user-menu-platform_settings"|data-testid="user-menu-platform_settings"[^>]*href="/settings/?"', shown)
    login_as(client, admin)
    assert "System Settings" in client.get("/settings").get_data(as_text=True)


def test_ai_settings_list_only_ai_providers_and_permissions_use_plain_words(app, db_session, login_as, client):
    admin, _ = _seed(db_session, login_as, client)
    html = client.get("/admin/api-settings").get_data(as_text=True)
    assert "API Settings" not in html
    select = re.search(r'<select[^>]*name="provider".*?</select>', html, re.S)
    assert select and "Anthropic" in select.group(0) and "JIRA" not in select.group(0)
    assert "JIRA" not in _text(re.search(r'data-testid="llm-spend-absent"[^>]*>(.*?)</p>', html, re.S).group(1)) \
        if 'data-testid="llm-spend-absent"' in html else True
    login_as(client, admin)
    perms_html = client.get("/admin/team?tab=permissions").get_data(as_text=True)
    perms = _text(perms_html)
    for raw in ("ORG_ADMIN", "org_admin", "ARCHITECT", "VIEWER", "motivation_elements", "MOTIVATION_ELEMENTS"):
        assert raw not in perms, raw
    heads = re.findall(r'<td[^>]*data-role="[a-z_]+"[^>]*>(.*?)</td>', perms_html, re.S)
    assert {"Org admin", "Architect", "Viewer"} <= {_text(h) for h in heads}
