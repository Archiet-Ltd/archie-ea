"""A member clicks Buy and is sent only to an administrator who can sign in, or to Contact us when there is none.

Goal: "I picked a plan; who do I ask to buy it?" Two organisations of the member's own. In the first, the only
administrator's account has been switched off, so naming them would send the member to someone who cannot sign in:
the page offers Contact us, and the link lands on the contact page. In the second, the administrator has no first or
last name, so the page names their address once, in the sentence and on the email button.
"""
import os
import pathlib
import tempfile
import uuid
from datetime import datetime

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
    """Organisation A: a member and a disabled administrator. Organisation B: a member and a nameless administrator."""
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    out, user_ids, org_ids = {}, [], []
    with app.app_context():
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").one()
        user_role = Role.query.filter_by(name="User").one()

        def person(org, label, admin, first, last):
            user = User(email="buy-%s-%s@example.com" % (label, suffix), first_name=first, last_name=last,
                        organization_id=org.id, role=admin_role if admin else user_role,
                        is_org_admin=admin, confirmed=True)
            user.password = PASSWORD
            db.session.add(user)
            db.session.flush()
            user_ids.append(user.id)
            return user

        for key in ("a", "b"):
            org = Organization(name="Buy %s %s" % (key, suffix), slug="buy-%s-%s" % (key, suffix))
            db.session.add(org)
            db.session.flush()
            org_ids.append(org.id)
            out[key] = org
        out["member_a"] = person(out["a"], "member-a", False, "Mia", "Member").email
        gone = person(out["a"], "gone-a", True, "Gina", "Left")
        gone.disabled_at = datetime.utcnow()
        out["gone_a"] = gone.email
        out["member_b"] = person(out["b"], "member-b", False, "Max", "Member").email
        out["nameless_b"] = person(out["b"], "nameless-b", True, None, None).email
        db.session.commit()
        out = {k: v for k, v in out.items() if isinstance(v, str)}

    def remove():
        from app.models.audit_log import AuditLog

        with app.app_context():
            db.session.remove()
            # Signing in records each member's own events in the audit log, which points at the user and the
            # organisation; those rows, scoped by these ids, go first. tenant-scoping-ok: only the rows this fixture created, by id
            AuditLog.query.filter(db.or_(AuditLog.user_id.in_(user_ids), AuditLog.organization_id.in_(org_ids))).delete(
                synchronize_session=False)
            User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)
            Organization.query.filter(Organization.id.in_(org_ids)).delete(synchronize_session=False)
            db.session.commit()

    request.addfinalizer(remove)
    return out


def test_a_member_whose_only_administrator_is_disabled_is_sent_to_contact_us(browser, live_server, two_organisations):
    people = two_organisations
    for width, height, scheme in SIZES:
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
        page = context.new_page()
        try:
            _login(page, live_server, people["member_a"])
            page.goto(live_server + BUY, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.get_by_role("heading", level=1)).to_contain_text(HEADING, timeout=PAGE_TIMEOUT)
            body = page.inner_text("body")
            assert people["gone_a"] not in body, "the page names an administrator who cannot sign in"
            assert "Gina Left" not in body
            assert "Contact us and we will put you in touch with the right person" in body
            assert page.locator('a[href^="mailto:"]').filter(has_text="Email").count() == 0
            _shot(page, "member-buy-disabled-admin", width, scheme)
            page.get_by_role("main").get_by_role("link", name="Contact us").click()
            page.wait_for_url(lambda url: url.rstrip("/").endswith("/contact"), timeout=PAGE_TIMEOUT)
            expect(page.get_by_role("heading", level=1)).not_to_be_empty(timeout=PAGE_TIMEOUT)
            assert page.locator("form").count() >= 1, "the contact page has no form to reach us"
        finally:
            context.close()


def test_a_member_whose_administrator_has_no_name_sees_the_address_once(browser, live_server, two_organisations):
    people = two_organisations
    address = people["nameless_b"]
    for width, height, scheme in SIZES:
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
        page = context.new_page()
        try:
            _login(page, live_server, people["member_b"])
            page.goto(live_server + BUY, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page.get_by_role("heading", level=1)).to_contain_text(HEADING, timeout=PAGE_TIMEOUT)
            body = page.inner_text("body")
            assert "Ask %s to buy the plan you chose." % address in body
            assert body.count(address) == 2, "the address should appear once in the sentence and once on the button"
            assert people["gone_a"] not in body and people["member_a"] not in body
            reach = page.get_by_role("link", name="Email " + address)
            expect(reach).to_have_attribute("href", "mailto:" + address)
            box = reach.bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= width, "the email button runs off the screen"
            _shot(page, "member-buy-nameless-admin", width, scheme)
        finally:
            context.close()
