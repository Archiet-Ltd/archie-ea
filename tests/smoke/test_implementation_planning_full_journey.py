"""A portfolio manager creates edits and inspects work packages end to end.

In a brand-new organisation the portfolio manager creates a work package
through the form, views its detail page, adds a dependency, finds it on the
blocked page, and sees every link land on a page with content. A second
organisation's portfolio manager sees none of the first's data and the
blocked page shows nothing.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

CREATE = "/implementation/work-packages/create"
BLOCKED = "/implementation/work-packages/blocked"


def _new_page(browser):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _make_programmes(org_id, suffix):
    from app import create_app, db
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    app = create_app("testing")
    names = ("Platform Renewal %s" % suffix, "Customer Growth %s" % suffix)
    with app.app_context():
        for name in names:
            db.session.add(EnterpriseInitiative(name=name, organization_id=org_id))
        db.session.commit()
        db.session.remove()
    return names


def test_portfolio_manager_creates_views_and_finds_blocked_work_packages(browser, live_server):
    org = create_fresh_org("portfolio_manager")
    suffix = org["suffix"]
    first_programme, second_programme = _make_programmes(org["org_id"], suffix)
    blocked_name = "Renew billing %s" % suffix
    blocker_name = "Launch campaign tooling %s" % suffix
    other = create_fresh_org("portfolio_manager")

    # --- First organisation: portfolio_manager ---
    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["portfolio_manager"])

        # The signed-in user's role is the persona.
        heading = page.locator("h1").filter(has_text="Dashboard")
        expect(heading).to_have_count(1)

        # Create the first work package (the blocker) through the form.
        page.goto(live_server + CREATE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Create Work Package").first).to_be_visible()
        page.fill("#name", blocker_name)
        page.select_option("#programme_id", label=second_programme)
        page.fill("#description", "Campaign tooling for the growth initiative")
        with page.expect_response(
            lambda r: r.url.endswith(CREATE) and r.request.method == "POST", timeout=PAGE_TIMEOUT
        ):
            page.get_by_role("button", name="Create Work Package").click()
        # The form redirects to the edit page after creating.
        page.wait_for_url(re.compile(r"/implementation/work-packages/\d+/edit$"), timeout=PAGE_TIMEOUT)
        blocker_id = int(re.search(r"/work-packages/(\d+)/edit", page.url).group(1))
        expect(page.get_by_role("heading", name="Edit Work Package").first).to_be_visible()
        # The blocker's name and programme are on the page.
        expect(page.locator("#name")).to_have_value(blocker_name)

        # Create the second work package (the one that will be blocked).
        page.goto(live_server + CREATE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#name", blocked_name)
        page.select_option("#programme_id", label=first_programme)
        page.fill("#description", "Renew billing depends on campaign tooling")
        with page.expect_response(
            lambda r: r.url.endswith(CREATE) and r.request.method == "POST", timeout=PAGE_TIMEOUT
        ):
            page.get_by_role("button", name="Create Work Package").click()
        page.wait_for_url(re.compile(r"/implementation/work-packages/\d+/edit$"), timeout=PAGE_TIMEOUT)
        blocked_id = int(re.search(r"/work-packages/(\d+)/edit", page.url).group(1))

        # Before any dependency the blocked page shows nothing.
        page.goto(live_server + BLOCKED, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("#blocked-empty")).to_be_visible()

        # Add the blocker as a dependency.
        page.goto(live_server + "/implementation/work-packages/%d/edit" % blocked_id,
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.select_option("#dependency_id", label=blocker_name)
        with page.expect_response(
            lambda r: "/dependencies" in r.url and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ):
            page.get_by_role("button", name="Add dependency").click()
        expect(page.locator("#dependency-list")).to_contain_text(blocker_name)

        # Reload and confirm the dependency persists.
        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#dependency-list")).to_contain_text(blocker_name)

        # View the detail page.
        detail_url = "/implementation/work-packages/%d" % blocked_id
        page.goto(live_server + detail_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("work-package-detail")).to_be_visible()
        expect(page.get_by_test_id("wp-status")).to_be_visible()
        expect(page.get_by_test_id("wp-dates")).to_contain_text("to")

        # Follow the edit link from the detail page and land on content.
        edit_link = page.get_by_role("link", name="Edit")
        expect(edit_link).to_have_count(1)
        edit_link.click()
        page.wait_for_url(re.compile(r"/implementation/work-packages/%d/edit$" % blocked_id),
                          timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Edit Work Package").first).to_be_visible()

        # The blocked page now shows the blocked work package.
        page.goto(live_server + BLOCKED, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.locator("#blocked-table tbody tr").filter(has_text=blocked_name)
        expect(row).to_have_count(1)
        expect(row).to_contain_text(blocker_name)
        expect(row).to_contain_text(first_programme)
        expect(row).to_contain_text(second_programme)

        # Follow the link to the blocked work package's edit page.
        link = row.get_by_role("link", name=blocked_name)
        expect(link).to_have_count(1)
        link.click()
        page.wait_for_url(re.compile(r"/implementation/work-packages/%d/edit$" % blocked_id),
                          timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Edit Work Package").first).to_be_visible()
    finally:
        context.close()

    # --- Second organisation: sees none of the first's data ---
    other_context, other_page = _new_page(browser)
    try:
        sign_in(other_page, live_server, other["emails"]["portfolio_manager"])
        other_page.goto(live_server + BLOCKED, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert blocked_name not in other_page.content()
        response = other_page.goto(
            live_server + "/implementation/work-packages/%d/edit" % blocked_id,
            wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
        )
        assert response is not None and response.status == 404
    finally:
        other_context.close()