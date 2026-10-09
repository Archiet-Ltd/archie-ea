"""A programme manager sees which work packages wait on another programme.

In a brand-new organisation with two programmes, the programme manager creates
a work package in each from the work package form, opens the first, adds the
second as something it depends on, reloads, and finds the first on the
"Blocked by another programme" page beside the work package that blocks it and
that work package's programme. A second new organisation's programme manager
sees none of it, and cannot open the first organisation's work package.
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
    """Programmes are created by the portfolio module; this journey starts from
    an organisation that already has two."""
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


def _create_work_package(page, base, name, programme):
    page.goto(base + CREATE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#name", name)
    page.select_option("#programme_id", label=programme)
    with page.expect_response(
        lambda r: r.url.endswith(CREATE) and r.request.method == "POST", timeout=PAGE_TIMEOUT
    ):
        page.get_by_role("button", name="Create Work Package").click()
    page.wait_for_url(re.compile(r"/implementation/work-packages/\d+/edit$"), timeout=PAGE_TIMEOUT)
    return int(re.search(r"/work-packages/(\d+)/edit", page.url).group(1))


def test_programme_manager_sees_work_package_blocked_by_another_programme(browser, live_server):
    org = create_fresh_org("portfolio_manager")
    first_programme, second_programme = _make_programmes(org["org_id"], org["suffix"])
    other = create_fresh_org("portfolio_manager")
    suffix = org["suffix"]
    blocked_name = "Renew billing %s" % suffix
    blocker_name = "Launch campaign tooling %s" % suffix

    context, page = _new_page(browser)
    try:
        sign_in(page, live_server, org["emails"]["portfolio_manager"])
        blocked_id = _create_work_package(page, live_server, blocked_name, first_programme)
        _create_work_package(page, live_server, blocker_name, second_programme)

        # Before any dependency exists the blocked page is empty.
        page.goto(live_server + BLOCKED, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("#blocked-empty")).to_be_visible()

        page.goto(live_server + "/implementation/work-packages/%d/edit" % blocked_id,
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.select_option("#dependency_id", label=blocker_name)
        with page.expect_response(
            lambda r: r.url.endswith("/dependencies") and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ):
            page.get_by_role("button", name="Add dependency").click()
        expect(page.locator("#dependency-list")).to_contain_text(blocker_name)

        # Reload, then open the blocked page.
        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#dependency-list")).to_contain_text(blocker_name)
        page.goto(live_server + BLOCKED, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.locator("#blocked-table tbody tr").filter(has_text=blocked_name)
        expect(row).to_have_count(1)
        expect(row).to_contain_text(blocker_name)
        expect(row).to_contain_text(first_programme)
        expect(row).to_contain_text(second_programme)
    finally:
        context.close()

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
