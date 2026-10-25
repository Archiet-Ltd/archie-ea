"""The bell opens the shared popover, and an architect can read and clear their notifications.

Signed in as the Enterprise Architect (a Viewer, never an administrator): the bell shows how many
are unread, opens a dialog named "Notifications" built by the popover component, lists only their
own organisation's notifications, lands each row on a page with content, and "Mark all read"
persists after a reload.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, _seed_standard_org
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture
def two_orgs(seeded, request):
    own = _seed_standard_org(request, None)
    other = _seed_standard_org(request, None)
    return own, other


def _persona(email, role):
    from app import create_app
    from app.models.user import User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == role, user.enterprise_role
        assert not user.is_admin() and not user.is_platform_admin
        return user.id, user.organization_id


def _shot(page, name):
    """Save a screenshot for a walk-through when SMOKE_WALK_DIR names a folder."""
    folder = os.environ.get("SMOKE_WALK_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.screenshot(path=os.path.join(folder, name))


def _notify(user_id, org_id, message, url):
    from app import create_app, db
    from app.models.models import Notification

    with create_app("testing").app_context():
        db.session.add(Notification(user_id=user_id, organization_id=org_id, message=message, url=url, read=False))
        db.session.commit()


def test_bell_popover_lists_own_notifications_and_mark_all_read_persists(browser, live_server, two_orgs):
    own, other = two_orgs
    email = own["emails"]["enterprise_architect"]
    uid, org_id = _persona(email, "enterprise_architect")
    _notify(uid, org_id, "Review the Payments Hub proposal", "/applications/")
    _notify(uid, org_id, "Capability map was updated", "/applications/")
    other_uid, other_org_id = _persona(other["emails"]["enterprise_architect"], "enterprise_architect")
    assert other_org_id != org_id
    _notify(other_uid, other_org_id, "Other organisation secret", "/applications/")

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/applications/", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")

        panel = page.locator("#notif-dropdown")
        # The panel is the shared popover: a dialog named by its heading.
        assert panel.get_attribute("data-popover") is not None
        assert panel.get_attribute("role") == "dialog"
        assert page.locator("#notif-title").inner_text().strip() == "Notifications"

        badge = page.locator("#notif-badge-count")
        expect(badge).to_have_text("2", timeout=PAGE_TIMEOUT)

        page.locator("#notif-btn").click()
        expect(panel).to_be_visible()
        text = panel.inner_text()
        assert "Review the Payments Hub proposal" in text and "Capability map was updated" in text
        assert "Other organisation secret" not in text
        _shot(page, "bell-1440-light.png")
        page.evaluate("document.documentElement.classList.add('dark')")
        _shot(page, "bell-1440-dark.png")
        page.evaluate("document.documentElement.classList.remove('dark')")
        page.set_viewport_size({"width": 390, "height": 800})
        box = panel.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390
        _shot(page, "bell-390-rows.png")
        page.set_viewport_size({"width": 1440, "height": 900})

        # The row lands on a page with content.
        panel.locator("a.notif-item").first.click()
        page.wait_for_url(re.compile(r"/applications/"), timeout=PAGE_TIMEOUT)
        assert page.locator("h1").first.inner_text().strip()

        # Mark all read: the count clears now and stays cleared after a reload.
        page.locator("#notif-btn").click()
        expect(panel.locator("#notif-mark-read-btn")).to_be_visible()
        panel.locator("#notif-mark-read-btn").click()
        expect(page.locator("#notif-badge")).to_be_hidden()
        page.wait_for_timeout(800)
        page.reload()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        expect(page.locator("#notif-badge")).to_be_hidden()
        page.locator("#notif-btn").click()
        expect(panel).to_contain_text("Review the Payments Hub proposal")
        expect(panel.locator("#notif-mark-read-btn")).to_be_hidden()
    finally:
        context.close()


def test_bell_popover_fits_a_phone_and_an_empty_one_says_what_it_is_for(browser, live_server, seeded):
    email = seeded["emails"]["solution_architect"]
    _persona(email, "solution_architect")
    context = browser.new_context(viewport={"width": 390, "height": 800})
    page = context.new_page()
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        page.locator("#notif-btn").click()
        panel = page.locator("#notif-dropdown")
        expect(panel).to_be_visible()
        expect(panel).to_contain_text("No notifications")
        expect(panel).to_contain_text("Updates about your work will appear here.")
        box = panel.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390
        _shot(page, "bell-390-empty.png")
    finally:
        context.close()
