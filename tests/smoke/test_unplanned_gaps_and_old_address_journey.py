"""The gaps with no work package are listed in full on every page, and the old severity address lists open gaps.

An enterprise architect works in an organisation with 505 recorded gaps, most of them covered by a work package.
She reads the change board's No work planned figure, then the register: the "Gaps with no work package" section
holds as many rows as the figure says on the first page and on the second, and the old gap-severity address with
a severity lists as many rows as the Open critical card counts. A second organisation holds gaps of its own, and
its person reaches only their own rows through the old address. An organisation with no gaps is told why.
"""
import os
import re
import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [
    ({"width": 1440, "height": 900}, "light"),
    ({"width": 390, "height": 844}, "dark"),
]

TOTAL = 505
UNPLANNED = 104       # open, no work package; the rest are covered
CRITICAL_OPEN = 6
CLOSED_CRITICAL = 1   # a settled critical gap the old address must not list
BOARD = "/adm-kanban/gap-analysis"
REGISTER = "/enterprise/implementation/gap-analysis"
OLD = "/enterprise/strategic/risk-assessment"


def _seed(org_id, noun):
    from app import create_app, db
    from app.models.implementation_migration import Gap
    from app.services import work_package_service
    from app.services.gap_register_service import create_gap

    with create_app("testing").app_context():
        covered = TOTAL - UNPLANNED
        db.session.add_all([
            Gap(organization_id=org_id, name="%s gap %03d" % (noun, index), description="", priority="low",
                severity="low", resolution_status="identified")
            for index in range(TOTAL - CRITICAL_OPEN - CLOSED_CRITICAL)])
        db.session.commit()
        criticals = [create_gap(org_id, "%s critical %d" % (noun, index), severity="critical", priority="low")[0]
                     for index in range(CRITICAL_OPEN)]
        create_gap(org_id, "%s settled critical" % noun, severity="critical", priority="low",
                   resolution_status="resolved")
        rows = Gap.query.filter(Gap.organization_id == org_id).order_by(Gap.id).all()
        assert len(rows) == TOTAL
        # every gap past the first UNPLANNED open ones is covered by the one work package
        open_rows = [g for g in rows if g.resolution_status != "resolved"]
        covered_rows = open_rows[UNPLANNED:] + [g for g in rows if g.resolution_status == "resolved"]
        assert len(covered_rows) >= covered - 1
        package = work_package_service.create_work_package(
            organization_id=org_id, name="%s close most gaps" % noun, status="in_progress")
        for gap in open_rows[UNPLANNED:]:
            work_package_service.link_gap(package.id, gap.id, organization_id=org_id)
        db.session.commit()
        assert criticals
        db.session.remove()


def _frame(page, name, width, theme):
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.evaluate("theme => document.documentElement.classList.toggle('dark', theme === 'dark')", theme)
        page.screenshot(path="%s/unplanned-%s-%d-%s.png" % (folder, name, width, theme), full_page=True)


def _number(page, testid):
    figure = page.locator('[data-testid="%s"]' % testid)
    assert figure.count() >= 1, "no %s on %s" % (testid, page.url)
    return int(re.search(r"\d+", figure.first.inner_text()).group(0))


def _section_rows(page):
    page.wait_for_selector('[data-testid="gaps-not-addressed"]', timeout=PAGE_TIMEOUT)
    return page.locator('[data-testid="not-addressed-gap-row"]').count()


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_the_unplanned_section_and_the_old_address_agree_with_the_figures(browser, live_server, viewport, theme):
    noun = "Plan%s" % uuid.uuid4().hex[:6]
    other = "Rival%s" % uuid.uuid4().hex[:6]
    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    _seed(mine["org_id"], noun)
    from app import create_app, db
    from app.services.gap_register_service import create_gap
    with create_app("testing").app_context():
        create_gap(theirs["org_id"], other + " critical", severity="critical", priority="low")
        create_gap(theirs["org_id"], other + " settled", severity="critical", priority="low",
                   resolution_status="resolved")
        db.session.commit()
        db.session.remove()
    base = live_server
    width = viewport["width"]

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    page = context.new_page()
    try:
        sign_in(page, base, mine["emails"]["enterprise_architect"])
        page.goto(base + BOARD, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        planned_none = _number(page, "gap-unclosed")
        assert planned_none == UNPLANNED, planned_none

        for number in (1, 2):
            page.goto("%s%s?page=%d" % (base, REGISTER, number), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            section = page.get_by_test_id("gaps-not-addressed")
            assert section.count() == 1, "no section on page %d" % number
            assert _section_rows(page) == planned_none, (number, _section_rows(page))
            assert str(planned_none) in page.get_by_test_id("gaps-not-addressed-total").inner_text()
            assert other not in page.locator("body").inner_text()
            _frame(page, "section-page%d" % number, width, theme)
        # the register's own filter lists the same rows
        page.goto("%s%s?status=open&work=none" % (base, REGISTER), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector('[data-testid="gap-row"]', timeout=PAGE_TIMEOUT)
        assert page.locator('[data-testid="gap-row"]').count() == planned_none
        # every row's link opens that gap
        page.goto(base + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        link = page.locator('[data-testid="not-addressed-gap-row"] a').first
        name = link.inner_text().strip()
        link.click()
        page.wait_for_url(re.compile(r"/Gap/\d+"), timeout=PAGE_TIMEOUT)
        assert name in page.locator("body").inner_text()

        # the old address with a severity lists the open gaps its figure counts, not the settled one
        page.goto(base + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        open_critical = _number(page, "gap-open-critical-value")
        assert open_critical == CRITICAL_OPEN
        page.goto(base + OLD + "?severity=critical", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector('[data-testid="gap-row"]', timeout=PAGE_TIMEOUT)
        assert page.locator('[data-testid="gap-row"]').count() == open_critical
        assert "settled critical" not in page.locator("body").inner_text()
        _frame(page, "old-address", width, theme)
    finally:
        context.close()

    their_context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    their_page = their_context.new_page()
    try:
        # signed out, the old address asks for a sign-in and returns to the same list after it
        their_page.goto(base + OLD + "?severity=critical", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        sign_in(their_page, base, theirs["emails"]["enterprise_architect"])
        their_page.goto(base + OLD + "?severity=critical", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        their_page.wait_for_selector('[data-testid="gap-row"]', timeout=PAGE_TIMEOUT)
        body = their_page.locator("body").inner_text()
        assert their_page.locator('[data-testid="gap-row"]').count() == 1
        assert other + " critical" in body and noun not in body
    finally:
        their_context.close()


@pytest.mark.parametrize("viewport,theme", VIEWPORTS[:1])
def test_an_organisation_with_no_gap_signals_is_told_why(browser, live_server, viewport, theme):
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        empty = page.get_by_test_id("gap-signals-empty")
        empty.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        text = empty.inner_text()
        assert "no signals" in text and "capability map" in text.lower()
        assert page.locator('[data-testid="gap-signal-row"]').count() == 0
    finally:
        context.close()
