"""The administrator of an organisation opens the four My Applications pages in
a real browser. They own no application, so each page says what to do next, and
the link it offers leads to the portfolio, where owners are assigned.
"""
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
PAGES = ("/my-applications/", "/my-applications/list", "/my-applications/health", "/my-applications/roadmap")


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _organisation_administrator(live_server):
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        from app.models.organization import Organization
        from app.models.user import Role, User
        from app.services.billing_plans import set_contract_plan

        Role.insert_roles()
        org = Organization(name="My Apps Org %s" % suffix, slug="my-apps-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        user = User(
            email="my-apps-admin-%s@example.com" % suffix,
            first_name="Org",
            last_name="Administrator",
            organization_id=org.id,
            enterprise_role="enterprise_architect",
            confirmed=True,
        )
        user.role = Role.query.filter_by(name="Administrator").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        return user.email


@pytest.mark.smoke
def test_organisation_administrator_reaches_my_applications_and_is_told_what_to_do(
    browser, live_server, seeded
):
    email = _organisation_administrator(live_server)

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        page = context.new_page()
        _login(page, live_server, email)

        for path in PAGES:
            response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response.status == 200, "%s answered %s" % (path, response.status)
            expect(page.locator('[data-testid="my-applications-empty-title"]')).to_be_visible(
                timeout=PAGE_TIMEOUT
            )
            expect(page.locator('[data-testid="my-applications-empty-next-step"]')).to_contain_text(
                "add yourself as an owner"
            )

        page.get_by_role("link", name="Browse all applications").click()
        page.wait_for_url("**/applications/", timeout=PAGE_TIMEOUT)
    finally:
        context.close()
