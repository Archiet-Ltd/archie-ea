"""Journey: the founder emails a hand-picked person a register link; the product itself says who signed up,
where from, and how far they got.

A visitor opens /account/register with the three values, signs up and confirms the company profile by pressing
Continue through the set-up steps. A platform admin then opens Platform admin, follows Sign-ups, and finds that
person with the source, campaign, ref and the step reached; the new person cannot open the list.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from . import fresh_org
from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

LINK = "/account/register?utm_source=outreach&utm_campaign=wave1&ref=w1-07"


def _platform_admin():
    from app import create_app, db
    from app.models.user import User

    org = fresh_org.create_fresh_org("enterprise_architect", org_admin=True, platform_role="Administrator")
    app = create_app("testing")
    with app.app_context():
        user = db.session.get(User, org["user_ids"]["enterprise_architect"])
        user.is_platform_admin = True
        db.session.commit()
        db.session.remove()
    return org["emails"]["enterprise_architect"]


def test_a_register_link_person_appears_on_the_signups_list_with_their_source_and_step(
        live_server, browser, page):
    address = "wave.%s@example.com" % uuid.uuid4().hex[:8]

    # The visitor: opens the emailed link, signs up, and presses Continue through the set-up steps.
    page.goto(live_server + LINK, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#first_name", "Wanda")
    page.fill("#last_name", "Wave")
    page.fill("#email", address)
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)
    page.click("#submit")
    page.wait_for_url("**/account/welcome", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("signup-continue").click()
    page.wait_for_url("**/account/welcome?step=3", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("signup-continue").click()
    page.wait_for_url("**/account/welcome?step=4", timeout=PAGE_TIMEOUT)

    # They cannot read the list of other people's sign-ups.
    reply = page.goto(live_server + "/admin/signups", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert reply.status in (302, 401, 403) or "/admin/signups" not in page.url
    assert page.locator("[data-testid=signups-table]").count() == 0
    assert reply.status != 200

    # The platform admin: Platform admin -> Sign-ups, finds the person with where they came from and how far they got.
    admin = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        admin_page = admin.new_page()
        fresh_org.sign_in(admin_page, live_server, _platform_admin())
        admin_page.goto(live_server + "/admin/platform", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        admin_page.get_by_role("link", name=re.compile("Sign-ups")).first.click()
        admin_page.wait_for_url("**/admin/signups", timeout=PAGE_TIMEOUT)
        expect(admin_page.get_by_role("heading", name="Sign-ups").first).to_be_visible()
        row = admin_page.get_by_role("row").filter(has_text=address)
        expect(row).to_have_count(1)
        for shown in ("outreach", "wave1", "w1-07", "Profile confirmed"):
            expect(row).to_contain_text(shown)
        expect(row).to_contain_text("Not yet")  # no first answer: said in words, not a dash

        narrow = admin.new_page()
        narrow.set_viewport_size({"width": 390, "height": 844})
        narrow.goto(live_server + "/admin/signups", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(narrow.get_by_text(address)).to_be_visible()
        assert narrow.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        # On a phone the columns that say where the person came from and how far they got can still be
        # scrolled to and are not covered by a pinned column.
        for shown in ("outreach", "w1-07", "Profile confirmed"):
            cell = narrow.get_by_role("row").filter(has_text=address).get_by_text(shown).first
            cell.scroll_into_view_if_needed()
            assert narrow.evaluate(
                """(el) => { const r = el.getBoundingClientRect();
                    const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                    return !!top && (top === el || el.contains(top) || top.contains(el)); }""",
                cell.element_handle(),
            ), "%s is covered or cut off on a 390 wide screen" % shown
    finally:
        admin.close()
