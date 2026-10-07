"""Browser journey (R1-B88): a platform administrator invites a teammate with the
non-technical-owner persona after reading the plain-words preview, the teammate
joins from the e-mailed link, and the administrator then runs an access review:
a grant nobody has used is flagged, removed, and the closed review is final.

The app runs as its own server with a real SMTP configuration pointed at a small
SMTP sink, as in the account mail journeys, so the invitation the teammate
accepts is the message the product actually sent.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_account_mail_journey import (  # noqa: F401  (fixtures are used by name)
    NEW_PASSWORD,
    _DISMISS_ONBOARDING_SCRIPT,
    _link_in,
    _sign_in,
    mail_server,
    smtp_sink,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_review_org(app):
    """An organisation with a platform administrator and two members: one used
    the product 10 days ago, one never has."""
    from app import db
    from app.models.audit_log import AuditLog
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.services.billing_plans import set_contract_plan

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Review Journey %s" % suffix, slug="review-journey-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        db.session.commit()

        admin_role = Role.query.filter_by(name="Administrator").one()
        architect_role = Role.query.filter_by(name="Architect").one()

        def person(label, role, **flags):
            user = User(
                first_name=label, last_name="Journey %s" % suffix,
                email="%s.%s@example.com" % (label.lower(), suffix), password=PASSWORD,
                confirmed=True, organization_id=org.id, enterprise_role="solution_architect",
                **flags,
            )
            user.role = role
            db.session.add(user)
            db.session.flush()
            return user

        admin = person("Admin", admin_role, is_org_admin=True, is_platform_admin=True)
        admin.enterprise_role = "platform_admin"
        recent = person("Recent", architect_role)
        idle = person("Idle", architect_role)
        OrgRole.set_role(org.id, admin.id, "org_admin", granted_by_id=admin.id)
        OrgRole.set_role(org.id, recent.id, "architect", granted_by_id=admin.id)
        OrgRole.set_role(org.id, idle.id, "architect", granted_by_id=admin.id)
        db.session.add(AuditLog(
            organization_id=org.id, user_id=recent.id, action="update",
            table_name="applications", created_at=datetime.utcnow() - timedelta(days=10),
        ))
        db.session.commit()
        return {
            "org_id": org.id, "org_name": org.name, "admin": admin.email,
            "recent": recent.email, "idle": idle.email, "idle_id": idle.id,
        }


def test_invite_with_a_preview_then_recertify_access(browser, mail_server, smtp_sink, app):
    world = _seed_review_org(app)
    invitee = "owner.%s@example.com" % uuid.uuid4().hex[:8]

    # -- the administrator reads the preview, then sends the invitation --------
    context = browser.new_context()
    context.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
    page = context.new_page()
    try:
        _sign_in(page, mail_server, world["admin"])
        page.goto(mail_server + "/admin/team", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        page.fill("#email", invitee)
        page.select_option("#persona", "non_technical_owner")
        page.select_option("#role", "architect")
        preview = page.locator("[data-persona-preview]:not([hidden])")
        expect(preview).to_have_count(1)
        expect(preview).to_contain_text("Owns an application or a business capability")
        expect(preview).to_contain_text("add and change records")
        send = page.get_by_role("button", name="Send invitation")
        assert preview.bounding_box()["y"] < send.bounding_box()["y"], "the preview must come before Send"

        page.select_option("#persona", "finance")
        expect(preview).to_contain_text("Sees what each application and contract costs")
        page.select_option("#persona", "non_technical_owner")
        expect(preview).to_contain_text("Owns an application or a business capability")

        send.click()
        page.wait_for_url("**/admin/team", timeout=PAGE_TIMEOUT)
        row = page.locator('[data-invitation-email="%s"]' % invitee)
        row.wait_for(timeout=PAGE_TIMEOUT)
        link = _link_in(smtp_sink.text_to(invitee), "/account/join/")
    finally:
        context.close()

    # -- the teammate takes up the invitation ----------------------------------
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(link, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("heading", name="Join %s" % world["org_name"]).wait_for(timeout=PAGE_TIMEOUT)
        page.fill("#password", NEW_PASSWORD)
        page.fill("#password2", NEW_PASSWORD)
        page.get_by_role("button", name="Join %s" % world["org_name"]).click()
        page.wait_for_url("**/account/login", timeout=PAGE_TIMEOUT)
    finally:
        context.close()

    # -- the role and persona are there after a reload, then the review runs ---
    context = browser.new_context()
    context.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
    page = context.new_page()
    try:
        _sign_in(page, mail_server, world["admin"])
        page.goto(mail_server + "/admin/team", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        member_row = page.locator("[data-team-members] tbody tr", has_text=invitee)
        expect(member_row).to_have_count(1)
        expect(member_row.locator('select[name="role"]')).to_have_value("architect")
        assert page.locator('[data-invitation-email="%s"]' % invitee).count() == 0

        with app.app_context():
            from app.models.user import User

            joined = User.find_by_email(invitee)
            assert joined.enterprise_role == "non_technical_owner"

        page.get_by_role("link", name="Access review").click()
        page.wait_for_url("**/admin/access/reviews", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Start a review").click()
        page.wait_for_url("**/admin/access/reviews/*", timeout=PAGE_TIMEOUT)
        review_url = page.url

        idle_row = page.locator('[data-item-email="%s"]' % world["idle"])
        recent_row = page.locator('[data-item-email="%s"]' % world["recent"])
        expect(idle_row.locator("[data-unused-flag]")).to_have_count(1)
        expect(recent_row.locator("[data-unused-flag]")).to_have_count(0)

        # Remove the unused grant; after a reload it still reads as reduced.
        idle_row.get_by_role("button", name="Remove").click()
        page.wait_for_url(review_url, timeout=PAGE_TIMEOUT)
        page.reload(wait_until="domcontentloaded")
        expect(page.locator('[data-item-email="%s"] [data-decision-label]' % world["idle"])).to_contain_text(
            "Access reduced to view only and signed out"
        )
        assert page.locator('[data-item-email="%s"]' % world["idle"]).get_attribute("data-item-decision") == "removed"

        # Keep everyone else, then close.
        while page.locator('button[name="decision"][value="kept"]').count():
            page.locator('button[name="decision"][value="kept"]').first.click()
            page.wait_for_url(review_url, timeout=PAGE_TIMEOUT)
            page.reload(wait_until="domcontentloaded")
        close = page.get_by_role("button", name="Close review")
        expect(close).to_be_enabled()
        close.click()
        page.wait_for_url(review_url, timeout=PAGE_TIMEOUT)
        page.reload(wait_until="domcontentloaded")
        expect(page.locator("[data-review-state]")).to_contain_text("Closed")
        expect(page.get_by_role("link", name="Download evidence")).to_be_visible()
        assert page.get_by_role("button", name="Close review").count() == 0
    finally:
        context.close()

    with app.app_context():
        from app.models.org_role import OrgRole

        assert OrgRole.get_role(world["org_id"], world["idle_id"]) is None
