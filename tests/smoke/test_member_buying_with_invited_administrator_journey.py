"""A member clicks Buy and is never sent to an administrator who has not yet taken up their invitation.

Goal: "I picked a plan; who do I ask to buy it?" Two organisations. In the first, the only administrator was invited
through the invitation service and has not set a password, so they cannot sign in: the member is offered Contact us,
and the link lands on the contact page. In the second, one administrator was invited and one is active: the page
names only the active one, and the email button reaches that address.
"""
import os
import pathlib
import tempfile
import uuid
from unittest import mock

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("BUY_WALK_DIR") or tempfile.gettempdir()) / "buy-walk"
SIZES = [(1440, 900, "light"), (390, 844, "dark")]
BUY = "/account/register?plan=team&interval=year"
HEADING = "An administrator of your organisation buys the plan"


def _shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))), full_page=True)


@pytest.fixture
def two_organisations(live_server, request):
    """A: a member and an invited administrator. B: a member, an invited administrator and an active one."""
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.modules.account.services import invitation_service

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    out, user_ids, org_ids = {}, [], []
    with app.app_context():
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").one()
        user_role = Role.query.filter_by(name="User").one()

        def person(org, label, admin, first, last):
            user = User(email="inv-%s-%s@example.com" % (label, suffix), first_name=first, last_name=last,
                        organization_id=org.id, role=admin_role if admin else user_role,
                        is_org_admin=admin, confirmed=True)
            user.password = PASSWORD
            db.session.add(user)
            db.session.flush()
            user_ids.append(user.id)
            return user

        def invite(org, inviter, label):
            email = "inv-%s-%s@example.com" % (label, suffix)
            with app.test_request_context("/"), \
                    mock.patch.object(invitation_service, "mail_available", lambda *a, **k: True), \
                    mock.patch.object(invitation_service, "deliver_email", lambda **k: (True, None)):
                row, _delivered, _error = invitation_service.invite_new_person(
                    org.id, inviter, email, org_role="viewer", first_name="Ivy", last_name="Invited",
                    platform_role=admin_role)
            user_ids.append(row.user_id)
            return email

        for key in ("a", "b"):
            org = Organization(name="Invite %s %s" % (key, suffix), slug="inv-%s-%s" % (key, suffix))
            db.session.add(org)
            db.session.flush()
            org_ids.append(org.id)
            out[key] = org
        # the inviter is an administrator of the organisation who is then switched off
        inviter_a = person(out["a"], "inviter-a", True, "Ines", "Inviter")
        out["member_a"] = person(out["a"], "member-a", False, "Mia", "Member").email
        out["invited_a"] = invite(out["a"], inviter_a, "invited-a")
        inviter_a.disabled_at = __import__("datetime").datetime.utcnow()
        active_b = person(out["b"], "active-b", True, "Abe", "Active")
        active_b.first_name, active_b.last_name = "Abe", "Active"
        out["member_b"] = person(out["b"], "member-b", False, "Max", "Member").email
        out["invited_b"] = invite(out["b"], active_b, "invited-b")
        out["active_b"] = active_b.email
        db.session.commit()
        out = {k: v for k, v in out.items() if isinstance(v, str)}

    def remove():
        from app.models.account_token import AccountToken
        from app.models.audit_log import AuditLog
        from app.models.org_role import OrgRole
        from app.models.pending_invitation import PendingInvitation

        with app.app_context():
            db.session.remove()
            # rows this fixture created, by id. tenant-scoping-ok: only the rows this fixture created, by id
            PendingInvitation.query.filter(PendingInvitation.user_id.in_(user_ids)).delete(synchronize_session=False)
            AccountToken.query.filter(AccountToken.user_id.in_(user_ids)).delete(synchronize_session=False)
            OrgRole.query.filter(OrgRole.user_id.in_(user_ids)).delete(synchronize_session=False)
            AuditLog.query.filter(db.or_(AuditLog.user_id.in_(user_ids), AuditLog.organization_id.in_(org_ids))).delete(
                synchronize_session=False)
            User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)
            Organization.query.filter(Organization.id.in_(org_ids)).delete(synchronize_session=False)
            db.session.commit()

    request.addfinalizer(remove)
    return out


def test_a_member_whose_only_administrator_has_not_taken_up_the_invitation_is_sent_to_contact_us(
        browser, live_server, two_organisations):
    people = two_organisations
    for width, height, scheme in SIZES:
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
        page = context.new_page()
        try:
            _login(page, live_server, people["member_a"])
            page.goto(live_server + BUY, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.get_by_role("heading", level=1)).to_contain_text(HEADING, timeout=PAGE_TIMEOUT)
            body = page.inner_text("body")
            assert people["invited_a"] not in body, "the page names an administrator who cannot sign in yet"
            assert "Ivy Invited" not in body
            assert "Contact us and we will put you in touch with the right person" in body
            assert page.locator('a[href^="mailto:"]').filter(has_text="Email").count() == 0
            _shot(page, "member-buy-invited-admin-only", width, scheme)
            page.get_by_role("main").get_by_role("link", name="Contact us").click()
            page.wait_for_url(lambda url: url.rstrip("/").endswith("/contact"), timeout=PAGE_TIMEOUT)
            expect(page.get_by_role("heading", level=1)).not_to_be_empty(timeout=PAGE_TIMEOUT)
            assert page.locator("form").count() >= 1, "the contact page has no form to reach us"
        finally:
            context.close()


def test_a_member_is_sent_only_to_the_active_administrator_when_another_is_still_invited(
        browser, live_server, two_organisations):
    people = two_organisations
    active = people["active_b"]
    for width, height, scheme in SIZES:
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
        page = context.new_page()
        try:
            _login(page, live_server, people["member_b"])
            page.goto(live_server + BUY, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.get_by_role("heading", level=1)).to_contain_text(HEADING, timeout=PAGE_TIMEOUT)
            body = page.inner_text("body")
            assert "Ask Abe Active (%s) to buy the plan you chose." % active in body
            assert people["invited_b"] not in body and "Ivy Invited" not in body
            assert people["invited_a"] not in body and people["member_a"] not in body
            reach = page.locator('a[href="mailto:%s"]' % active)
            expect(reach).to_have_count(1, timeout=PAGE_TIMEOUT)
            expect(reach).to_contain_text("Email")
            _shot(page, "member-buy-invited-and-active-admin", width, scheme)
        finally:
            context.close()
