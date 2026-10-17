"""Gap Analysis journey: an enterprise architect links a gap to a work package
through the picker, reloads and sees it listed (and the gap gone from the
not-addressed list), then removes the link and reloads again.
"""

import re
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


@pytest.fixture
def gap_and_work_package(seeded):
    from app import create_app, db
    from app.services import work_package_service
    from app.services.gap_register_service import create_gap

    suffix = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        gap, _ = create_gap(org_id, "Data quality gap %s" % suffix, severity="medium")
        wp = work_package_service.create_work_package(
            organization_id=org_id, name="Cleanse master data %s" % suffix
        )
        db.session.commit()
        return {"gap": gap.name, "wp": wp.name}


@pytest.mark.smoke
def test_gap_links_to_work_package_and_back(browser, live_server, seeded, gap_and_work_package):
    gap, wp = gap_and_work_package["gap"], gap_and_work_package["wp"]
    url = live_server + "/enterprise/implementation/gap-analysis"
    page = browser.new_context(ignore_https_errors=True).new_page()
    page.set_default_timeout(PAGE_TIMEOUT)

    def not_addressed():
        card = page.locator("[data-slot=card]", has_text="Gaps Not Addressed by Any Work Package")
        return card.locator("[data-testid^=not-addressed-gap-link-]", has_text=gap)

    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(url, wait_until="domcontentloaded")
    expect(not_addressed()).to_have_count(1)

    row = page.locator("tr", has_text=gap).first
    row.get_by_role("button", name=re.compile("Link a work package to")).click()
    search = row.get_by_role("searchbox", name="Search work packages")
    search.fill(wp)
    with page.expect_navigation():
        row.get_by_role("option", name=wp).click()

    expect(not_addressed()).to_have_count(0)
    row = page.locator("tr", has_text=gap).first
    expect(row.locator("[data-testid^=gap-work-packages-]")).to_contain_text(wp)

    page.reload(wait_until="domcontentloaded")
    row = page.locator("tr", has_text=gap).first
    expect(row.locator("[data-testid^=gap-work-packages-]")).to_contain_text(wp)

    with page.expect_navigation():
        row.get_by_role("button", name="Remove link to " + wp).click()
    page.reload(wait_until="domcontentloaded")
    row = page.locator("tr", has_text=gap).first
    expect(row.locator("[data-testid^=gap-work-packages-]")).not_to_contain_text(wp)
    expect(not_addressed()).to_have_count(1)
