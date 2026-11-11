"""The register's "Gaps with no work package" shows a page of rows and takes a person to the rest.

An enterprise architect has more gaps with no work package than one page holds, and most other gaps covered, so the
register draws the section. The section says how many of how many it shows and links to the full list; the list
holds the same gaps, page after page, and the last page holds the last one. A second organisation, with one gap and
no work, reads only its own gap and sees no link.
"""
import os
import re

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [
    ({"width": 1440, "height": 900}, "light"),
    ({"width": 390, "height": 844}, "dark"),
]
REGISTER = "/enterprise/implementation/gap-analysis"
PAGE = 500
BARE = PAGE + 1
COVERED = PAGE + 2


def _seed(org, covered, bare):
    from app import create_app, db
    from app.services import work_package_service
    from app.services.gap_register_service import create_gap

    suffix = org["suffix"]
    with create_app("testing").app_context():
        for index in range(covered):
            gap, _ = create_gap(org["org_id"], "Covered gap %d %s" % (index, suffix))
            package = work_package_service.create_work_package(
                organization_id=org["org_id"], name="Close covered %d %s" % (index, suffix), status="in_progress")
            work_package_service.link_gap(package.id, gap.id, organization_id=org["org_id"])
        for index in range(bare):
            create_gap(org["org_id"], "Bare gap %04d %s" % (index, suffix))
        db.session.commit()
        db.session.remove()


def _frame(page, name, width, theme):
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.evaluate("theme => document.documentElement.classList.toggle('dark', theme === 'dark')", theme)
        page.screenshot(path="%s/uncovered-%s-%d-%s.png" % (folder, name, width, theme), full_page=True)


def test_the_uncovered_section_reaches_every_gap_with_no_work_package(browser, live_server):
    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    _seed(mine, covered=COVERED, bare=BARE)
    _seed(theirs, covered=0, bare=1)
    s = mine["suffix"]

    for viewport, theme in VIEWPORTS:
        context = browser.new_context(ignore_https_errors=True, viewport=viewport)
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
        page = context.new_page()
        try:
            sign_in(page, live_server, mine["emails"]["enterprise_architect"])
            page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            section = page.get_by_test_id("gaps-not-addressed")
            section.wait_for(state="visible", timeout=PAGE_TIMEOUT)
            assert section.get_by_test_id("gaps-not-addressed-total").inner_text().strip() == (
                "Showing gaps 1 to %d of %d with no work package. See all %d gaps with no work package"
                % (PAGE, BARE, BARE))
            assert section.locator('[data-testid="not-addressed-gap-row"]').count() == PAGE
            assert theirs["suffix"] not in page.locator("body").inner_text()
            _frame(page, "section", viewport["width"], theme)

            section.get_by_test_id("gaps-not-addressed-all").click()
            page.wait_for_url(re.compile(r"status=open&work=none"), timeout=PAGE_TIMEOUT)
            page.wait_for_selector('[data-testid="gap-row"]', timeout=PAGE_TIMEOUT)
            assert "gaps with no work package (%d of %d)" % (BARE, BARE + COVERED) in page.locator(
                '[data-testid="gap-severity-filter"]').inner_text()
            assert page.locator('[data-testid="gap-row"]').count() == PAGE
            assert "Covered gap" not in page.locator('[data-testid="gap-row"]').first.inner_text()
            assert page.get_by_test_id("gap-page-next").is_visible()
            _frame(page, "list", viewport["width"], theme)

            page.get_by_test_id("gap-page-next").click()
            page.wait_for_url(re.compile(r"page=2"), timeout=PAGE_TIMEOUT)
            page.wait_for_selector('[data-testid="gap-row"]', timeout=PAGE_TIMEOUT)
            rows = page.locator('[data-testid="gap-row"]')
            assert rows.count() == 1
            assert re.search(r"Bare gap \d{4} %s" % s, rows.first.inner_text())
        finally:
            context.close()

    other = browser.new_context(ignore_https_errors=True, viewport=VIEWPORTS[0][0])
    other_page = other.new_page()
    try:
        sign_in(other_page, live_server, theirs["emails"]["enterprise_architect"])
        other_page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        other_page.wait_for_selector('[data-testid="gap-row"]', timeout=PAGE_TIMEOUT)
        assert other_page.locator('[data-testid="gap-row"]').count() == 1
        assert other_page.get_by_test_id("gaps-not-addressed-all").count() == 0
        assert s not in other_page.locator("body").inner_text()
    finally:
        other.close()
