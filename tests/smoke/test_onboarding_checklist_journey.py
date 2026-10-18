"""An enterprise architect in a brand-new organisation works the setup checklist
in a real browser: the capability step opens the create dialog, the saved
capability survives a reload, and the checklist then stops calling the
workspace empty. The import step opens the import dialog.

A second organisation holding data sits beside it, so the checklist is read
against two organisations, not one.
"""
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _empty_organisation_user(live_server):
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        from app.models.organization import Organization
        from app.models.user import Role, User
        from app.services.billing_plans import set_contract_plan

        Role.insert_roles()
        org = Organization(name="Checklist Org %s" % suffix, slug="checklist-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        user = User(
            email="checklist-%s@example.com" % suffix,
            first_name="Checklist",
            last_name="Architect",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        return user.email, suffix


@pytest.mark.smoke
def test_architect_adds_a_first_capability_from_the_checklist(browser, live_server, seeded):
    email, suffix = _empty_organisation_user(live_server)
    name = "Checklist capability %s" % suffix

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    try:
        # The first-run role dialog is a separate flow; mark it seen.
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        page = context.new_page()
        _login(page, live_server, email)

        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="guided-setup"]')).to_be_visible(timeout=PAGE_TIMEOUT)

        # The import step opens the import dialog on the applications list.
        page.get_by_role("link", name="Import").first.click()
        page.wait_for_url("**/applications/?import=1", timeout=PAGE_TIMEOUT)
        expect(page.locator("#application-import-modal")).to_be_visible(timeout=PAGE_TIMEOUT)

        # The capability step opens the create dialog.
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Add a capability").click()
        page.wait_for_url("**/capability-map/capabilities?new=1", timeout=PAGE_TIMEOUT)
        expect(page.locator("#cap-dialog")).to_be_visible(timeout=PAGE_TIMEOUT)

        page.fill("#cap-name", name)
        page.locator("#cap-save").click()
        expect(page.get_by_text("Capability created")).to_be_visible(timeout=PAGE_TIMEOUT)

        # Persisted: after a reload the capability is listed...
        page.goto(live_server + "/enterprise/capability-map/capabilities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=name)).to_be_visible(timeout=PAGE_TIMEOUT)

        # ...and the dashboard no longer calls the workspace empty.
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="guided-setup"]')).to_have_count(0)
    finally:
        context.close()
