"""A person who administers a second organisation opens its Connections page.

Their account-wide role is Architect and their persona is a portfolio manager,
who has no connecting-architect standing. They belong to a first organisation as
a member and administer a second one through their organisation role. They
switch to the second organisation, open Connections from the administration
page and see the GitHub form; they switch back to the first, where they are
only a member, and the same page is refused with the standard refusal page.

Played at 1440 wide in the light theme and 390 wide in the dark theme.
Screenshots go to the directory named by ORGADMIN_JOURNEY_SHOTS, or a
temporary directory.
"""

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def estate():
    """A first organisation where the traveller is a member, a second where they administer."""
    first = create_fresh_org("portfolio_manager")
    second = create_fresh_org("enterprise_architect", org_admin=True)
    traveller_id = first["user_ids"]["portfolio_manager"]

    from app import create_app, db
    from app.models.org_role import OrgRole
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        OrgRole.set_role(second["org_id"], traveller_id, "org_admin", granted_by_id=second["user_ids"]["enterprise_architect"])
        db.session.commit()
        names = {
            "first": db.session.get(Organization, first["org_id"]).name,
            "second": db.session.get(Organization, second["org_id"]).name,
        }
        db.session.remove()
    return dict(email=first["emails"]["portfolio_manager"], first_id=first["org_id"],
                second_id=second["org_id"], names=names)


def _shot_dir(tmp_path):
    root = os.environ.get("ORGADMIN_JOURNEY_SHOTS")
    path = Path(root) if root else tmp_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _switch_to(page, live_server, org_id, org_name):
    page.goto(live_server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator('input[type="radio"][name="organization_id"][value="%s"]' % org_id).check(force=True)
    page.get_by_test_id("organization-memberships-card").get_by_role("button", name="Switch organisation").click()
    page.wait_for_url("**/account/manage**", timeout=PAGE_TIMEOUT)
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert org_name in page.locator('[data-testid="active-organization-chip"]').inner_text()


@pytest.mark.parametrize(
    "width,height,dark",
    [(1440, 900, False), (390, 844, True)],
    ids=["1440-light", "390-dark"],
)
def test_second_organisation_administrator_opens_connections(
    browser, live_server, estate, tmp_path, width, height, dark
):
    shots = _shot_dir(tmp_path)
    tag = "%d-%s" % (width, "dark" if dark else "light")
    context = browser.new_context(viewport={"width": width, "height": height},
                                  color_scheme="dark" if dark else "light")
    page = context.new_page()
    try:
        sign_in(page, live_server, estate["email"])
        if dark:
            page.evaluate("() => document.documentElement.classList.add('dark')")

        _switch_to(page, live_server, estate["second_id"], estate["names"]["second"])

        # Organisation administration lists Connections; following it lands on the GitHub form.
        page.goto(live_server + "/admin/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "You don't have access to this page" not in page.content()
        page.goto(live_server + "/admin/connectors/github", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("github-connect-form")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Connect GitHub").first).to_be_visible()
        assert "You don't have access to this page" not in page.content()
        page.screenshot(path=str(shots / ("connections-second-%s.png" % tag)))

        # Back in the organisation where they are only a member: refused, with the standard page.
        _switch_to(page, live_server, estate["first_id"], estate["names"]["first"])
        refused = page.goto(live_server + "/admin/connectors/github", wait_until="domcontentloaded",
                            timeout=PAGE_TIMEOUT)
        assert refused.status == 403
        expect(page.get_by_text("You don't have access to this page").first).to_be_visible()
        assert page.get_by_test_id("github-connect-form").count() == 0
        page.screenshot(path=str(shots / ("connections-first-refused-%s.png" % tag)))
    finally:
        context.close()
