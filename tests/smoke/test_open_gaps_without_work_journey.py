"""The register's "Gaps with no work package" lists the open gaps the board counts as No work planned.

An enterprise architect has four gaps with a work package, two open gaps without one and a completed gap
without one. The board says 2 gaps have no work planned; the register's table of gaps with no work package
holds those two and not the completed one, and the board's figure opens the same two rows. A second
organisation, with one open and one resolved gap and no work, reads its own figure.
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
BOARD = "/adm-kanban/gap-analysis"
REGISTER = "/enterprise/implementation/gap-analysis"


def _seed(org, covered, bare, closed_bare):
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
            create_gap(org["org_id"], "Bare gap %d %s" % (index, suffix))
        for index in range(closed_bare):
            create_gap(org["org_id"], "Settled gap %d %s" % (index, suffix), resolution_status="completed")
        db.session.commit()
        db.session.remove()


def _frame(page, name, width, theme):
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.evaluate("theme => document.documentElement.classList.toggle('dark', theme === 'dark')", theme)
        page.screenshot(path="%s/open-work-%s-%d-%s.png" % (folder, name, width, theme), full_page=True)


def _figure(page, testid):
    return int(re.search(r"\d+", page.locator('[data-testid="%s"]' % testid).inner_text()).group(0))


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_the_registers_gaps_without_work_are_the_boards_no_work_planned(browser, live_server, viewport, theme):
    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    _seed(mine, covered=4, bare=2, closed_bare=1)
    _seed(theirs, covered=0, bare=1, closed_bare=1)
    s = mine["suffix"]

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])
        page.goto(live_server + BOARD, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        planned = _figure(page, "gap-unclosed")
        assert planned == 2
        _frame(page, "board", viewport["width"], theme)

        page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        section = page.get_by_test_id("gaps-not-addressed")
        section.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert section.locator('[data-testid="not-addressed-gap-row"]').count() == planned
        text = section.inner_text()
        assert "Bare gap 0 %s" % s in text and "Bare gap 1 %s" % s in text
        assert "Settled gap" not in text and "Covered gap" not in text
        assert theirs["suffix"] not in page.locator("body").inner_text()
        _frame(page, "register", viewport["width"], theme)

        page.goto(live_server + BOARD, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator('a:has([data-testid="gap-unclosed"])').click()
        page.wait_for_url(re.compile(r"status=open&work=none"), timeout=PAGE_TIMEOUT)
        page.wait_for_selector('[data-testid="gap-row"]', timeout=PAGE_TIMEOUT)
        assert page.locator('[data-testid="gap-row"]').count() == planned
    finally:
        context.close()

    other = browser.new_context(ignore_https_errors=True, viewport=viewport)
    other_page = other.new_page()
    try:
        sign_in(other_page, live_server, theirs["emails"]["enterprise_architect"])
        other_page.goto(live_server + BOARD, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert _figure(other_page, "gap-unclosed") == 1
        other_page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        other_page.wait_for_selector('[data-testid="gap-row"]', timeout=PAGE_TIMEOUT)
        assert other_page.locator('[data-testid="gap-row"]').count() == 2
        assert s not in other_page.locator("body").inner_text()
    finally:
        other.close()
