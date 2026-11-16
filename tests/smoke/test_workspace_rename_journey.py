"""An organisation administrator renames their workspace and a second member sees the new name.

Ada Lovelace signs up, so her workspace is called "Ada Lovelace's Workspace". She opens Settings,
changes the name to "Analytical Engines Ltd", saves, and the sidebar's workspace switcher shows the
new name on the next page. A colleague in the same organisation signs in afterwards and sees it on
their own screens. A blank name is refused next to the field and nothing changes.
"""

import os
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("WORKSPACE_RENAME_SHOTS") or os.path.join("test-results", "workspace-rename")
STAND_IN = "Ada Lovelace's Workspace"
NEW_NAME = "Analytical Engines Ltd"


def _stand_in(org_id, name):
    from app import create_app, db
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        db.session.get(Organization, org_id).name = name
        db.session.commit()


def _switcher(page):
    return page.locator("[data-sidebar='menu-button'][data-tooltip]").first


@pytest.mark.parametrize(
    "size,scheme",
    [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")],
)
def test_administrator_renames_workspace_and_colleague_sees_it(browser, live_server, size, scheme):
    base = str(live_server)
    tag = "%d-%s" % (size["width"], scheme)
    fresh = create_fresh_org("enterprise_architect", org_admin=True, extra_roles=("solution_architect",))
    name = "%s %s" % (NEW_NAME, uuid.uuid4().hex[:6])
    _stand_in(fresh["org_id"], STAND_IN)
    os.makedirs(SHOTS, exist_ok=True)
    context = browser.new_context(ignore_https_errors=True, viewport=size, color_scheme=scheme)
    page = context.new_page()
    try:
        sign_in(page, base, fresh["emails"]["enterprise_architect"])
        page.goto(base + "/settings", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        form = page.get_by_test_id("workspace-name-form")
        expect(form).to_be_visible()
        expect(form.get_by_label("Name")).to_have_value(STAND_IN)
        assert STAND_IN in (_switcher(page).get_attribute("data-tooltip") or "")
        page.screenshot(path=os.path.join(SHOTS, "settings-before-%s.png" % tag), full_page=True)

        form.get_by_label("Name").fill("   ")
        form.get_by_role("button", name="Save name").click()
        expect(page.get_by_test_id("workspace-name-error")).to_contain_text("Enter a name")
        assert STAND_IN in (_switcher(page).get_attribute("data-tooltip") or "")
        page.screenshot(path=os.path.join(SHOTS, "settings-blank-%s.png" % tag), full_page=True)

        page.get_by_label("Name").fill(name)
        page.get_by_role("button", name="Save name").click()
        expect(page.get_by_text("The workspace is now called %s." % name)).to_be_visible()
        expect(page.get_by_label("Name")).to_have_value(name)
        page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert name in (_switcher(page).get_attribute("data-tooltip") or "")
        assert STAND_IN not in page.content()
        page.screenshot(path=os.path.join(SHOTS, "dashboard-after-%s.png" % tag))
    finally:
        context.close()

    colleague = browser.new_context(ignore_https_errors=True, viewport=size, color_scheme=scheme)
    other = colleague.new_page()
    try:
        sign_in(other, base, fresh["emails"]["solution_architect"])
        other.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert name in (_switcher(other).get_attribute("data-tooltip") or "")
        assert STAND_IN not in other.content()
        other.screenshot(path=os.path.join(SHOTS, "colleague-%s.png" % tag))
        other.goto(base + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(other.get_by_test_id("active-organization-badge")).to_contain_text(name)
    finally:
        colleague.close()
