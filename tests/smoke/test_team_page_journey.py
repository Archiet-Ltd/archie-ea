"""The team page tells the truth to the person who invites and to the person invited, and works on a phone.

From the goals: "invite a colleague who already has an account and know what happens next", "see every
member's role on a phone", "switch a permission on a phone and find it still switched after a reload".
"""
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_account_mail_journey import _seed_member, _sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = Path(os.environ.get("JRN01D_SHOTS", "test-results/jrn01d"))


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s.png" % name)), full_page=True)


def test_without_mail_the_invitation_is_saved_and_the_invitee_is_not_yet_a_member(browser, live_server, seeded, app):
    elsewhere = _seed_member(app)
    context = browser.new_context()
    page = context.new_page()
    try:
        from .test_archetype_journeys import _login

        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/admin/team", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        sentence = page.get_by_test_id("team-invite").inner_text()
        assert "joins the next time they sign in" not in sentence
        assert "saved below as not yet accepted" in sentence
        page.fill("#email", elsewhere["email"])
        page.select_option("#role", "architect")
        page.get_by_role("button", name="Send invitation").click()
        page.wait_for_url("**/admin/team", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("they have not been told", timeout=PAGE_TIMEOUT)
        row = page.locator('[data-testid="invitation-row"]').filter(
            has=page.locator('[data-invitation-email="%s"]' % elsewhere["email"]))
        expect(row).to_contain_text("Architect")
        expect(row).to_contain_text("Has an account")
    finally:
        context.close()

    # The invitee's side: signing in does not make them a member, exactly as the sentence says.
    context = browser.new_context()
    page = context.new_page()
    try:
        _sign_in(page, live_server, elsewhere["email"])
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).not_to_contain_text("Accept invitation")
    finally:
        context.close()
    with app.app_context():
        from app.models.org_role import OrgRole
        from app.models.user import User

        from app.models.pending_invitation import PendingInvitation

        member = User.find_by_email(elsewhere["email"])
        invitation = PendingInvitation.query.filter_by(user_id=member.id).first()
        assert invitation is not None and invitation.role == "architect"
        assert invitation.organization_id != member.organization_id
        assert OrgRole.get_role(invitation.organization_id, member.id) is None


@pytest.mark.parametrize("width", [390, 360])
def test_team_tables_can_be_read_and_switched_on_a_phone_in_dark(browser, live_server, seeded, width):
    from .test_archetype_journeys import _login

    context = browser.new_context(viewport={"width": width, "height": 800}, color_scheme="dark")
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/admin/team", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        role = page.get_by_test_id("member-role").first
        role.scroll_into_view_if_needed()
        box = role.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, box
        assert role.inner_text().strip() in {"Org admin", "Architect", "Viewer"}
        _shot(page, "team-%d-dark" % width)

        page.goto(live_server + "/admin/team?tab=permissions", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        toggles = page.locator('[data-testid^="permissions-grid-"]').first.locator('[role="switch"]')
        enabled = [i for i in range(toggles.count()) if toggles.nth(i).is_enabled()]
        assert enabled, "no switch can be used"
        for i in range(toggles.count()):
            toggle = toggles.nth(i)
            toggle.scroll_into_view_if_needed()
            covered = toggle.evaluate(
                "el => { const r = el.getBoundingClientRect(); const t = document.elementFromPoint("
                "r.left + r.width / 2, r.top + r.height / 2); return !(t === el || el.contains(t)); }")
            assert not covered, "switch %d is covered by another element" % i
        _shot(page, "permissions-%d-dark" % width)
        label = toggles.nth(enabled[0]).get_attribute("aria-label")
        before = toggles.nth(enabled[0]).get_attribute("aria-checked")
        toggles.nth(enabled[0]).click()
        page.wait_for_load_state("domcontentloaded")
        page.goto(live_server + "/admin/team?tab=permissions", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        switch = page.locator('[role="switch"][aria-label="%s"]' % label).first
        assert switch.get_attribute("aria-checked") != before, "the switch did not keep its new state after a reload"
        switch.click()  # put it back
    finally:
        context.close()
