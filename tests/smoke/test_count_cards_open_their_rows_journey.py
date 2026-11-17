"""Every gap and risk count counts what its label says, and opens the rows it counts.

An enterprise architect works in an organisation with more gaps than a list shows: 523 recorded gaps,
seven of them critical (one of those resolved), and closed and in-progress gaps beside open ones. She reads
Gap analysis, the AI architecture analysis, the implementation page, the gap-severity page, the change
board, the Risks page and the Risk assessment, clicks every count card, and reads the length of the list
that opens against the figure on the card. A second organisation holds gaps and risks of its own, so a
count that reached across organisations would change a figure.
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

FILLER = 506          # open gaps with no severity rated: more than one list page of 500
OPEN_CRITICAL = 6     # five rated critical on severity, one on priority alone
CRITICAL = OPEN_CRITICAL + 1   # and one critical gap that has been resolved
HIGH, MEDIUM, LOW = 3, 2, 2   # open gaps; the two medium ones are in progress
CLOSED = 3 + 1        # resolved, closed, completed, and the resolved critical gap
RECORDED = FILLER + OPEN_CRITICAL + HIGH + MEDIUM + LOW + CLOSED


def _seed(org_id, noun, *, filler, critical, closed, high=HIGH, medium=MEDIUM, low=LOW, covered=True, signals=False):
    from app import create_app, db
    from app.models.implementation_migration import Gap
    from app.models.risk import Risk, RiskStatus
    from app.services import work_package_service
    from app.services.gap_register_service import create_gap

    with create_app("testing").app_context():
        db.session.add_all([
            Gap(organization_id=org_id, name="%s filler gap %03d" % (noun, index), description="", priority="low",
                severity=None, resolution_status="identified")
            for index in range(filler)])
        db.session.commit()
        # the column's default rates an unset severity medium; these gaps are the ones nobody has rated
        db.session.execute(db.update(Gap).where(
            Gap.organization_id == org_id, Gap.name.like("%s filler gap %%" % noun)).values(severity=None))
        db.session.commit()
        for index in range(critical):
            create_gap(org_id, "%s critical gap %d" % (noun, index), severity="critical", priority="medium")
        urgent, _ = create_gap(org_id, "%s urgent by priority" % noun, severity="high", priority="critical")
        create_gap(org_id, "%s resolved critical gap" % noun, severity="critical", priority="critical",
                   resolution_status="resolved")
        for index in range(high):
            create_gap(org_id, "%s high gap %d" % (noun, index), severity="high", priority="low")
        for index in range(medium):
            create_gap(org_id, "%s medium gap %d" % (noun, index), severity="medium", priority="low",
                       resolution_status="in_progress")
        for index in range(low):
            create_gap(org_id, "%s low gap %d" % (noun, index), severity="low", priority="low")
        for index, status in enumerate(("resolved", "closed", "completed")[:closed]):
            create_gap(org_id, "%s settled gap %d" % (noun, index), severity="low", priority="low",
                       resolution_status=status)
        if covered:
            package = work_package_service.create_work_package(
                organization_id=org_id, name="%s close the urgent gap" % noun, status="in_progress")
            work_package_service.link_gap(package.id, urgent.id, organization_id=org_id)
        if signals:
            from app.models.application_portfolio import ApplicationComponent
            from app.models.business_capabilities import BusinessCapability

            for label in ("Billing", "Onboarding"):
                db.session.add(BusinessCapability(name="%s %s" % (label, noun), organization_id=org_id))
            db.session.add(ApplicationComponent(name="Unmapped App %s" % noun, organization_id=org_id,
                                                lifecycle_status="operational", description="Mapped to no capability."))
        for title, likelihood, impact in ((noun + " severe", 5, 5), (noun + " serious", 4, 4), (noun + " minor", 1, 1)):
            db.session.add(Risk(title=title, description="%s could happen." % title, likelihood=likelihood,
                                impact=impact, status=RiskStatus("open"), owner="Owner",
                                mitigation_plan="Plan.", organization_id=org_id))
        for index in range(4):
            db.session.add(Risk(title="%s settled risk %d" % (noun, index), description="Dealt with.",
                                likelihood=5, impact=5, status=RiskStatus("closed" if index % 2 else "mitigated"),
                                owner="Owner", mitigation_plan="Done.", organization_id=org_id))
        db.session.commit()
        db.session.remove()


def _frame(page, name, width, theme):
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.evaluate("""() => {
            const sheet = new CSSStyleSheet();
            sheet.replaceSync('*, *::before, *::after { transition: none !important; animation: none !important; }');
            document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
        }""")
        page.evaluate("theme => document.documentElement.classList.toggle('dark', theme === 'dark')", theme)
        page.wait_for_function(
            "theme => document.documentElement.classList.contains('dark') === (theme === 'dark')", arg=theme)
        page.screenshot(path="%s/cards-%s-%d-%s.png" % (folder, name, width, theme), full_page=True)


def _number(page, testid):
    figure = page.locator('[data-testid="%s"]' % testid)
    assert figure.count() >= 1, "no %s on %s: %s" % (testid, page.url, page.locator("body").inner_text()[:400])
    return int(re.search(r"\d+", figure.first.inner_text()).group(0))


def _gap_rows(page):
    """The gap rows the list holds (the rows themselves, not the pager's page of them)."""
    page.wait_for_selector('[data-testid="gap-row"], [data-testid="gaps-empty"]', timeout=PAGE_TIMEOUT)
    return page.locator('[data-testid="gap-row"]').count()


def _risk_rows(page):
    """The risk rows the table shows (the table is drawn in the browser, without paging)."""
    rows = page.locator("#risk-register-table tbody tr:visible")
    rows.first.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    return rows.count()


def _click_card(page, card_testid, listed, figure, *, rows, shot=None, viewport=None, theme=None):
    """Click a count card; the list it opens holds exactly the figure's rows."""
    card = page.locator('a[data-testid="%s"]' % card_testid)
    assert card.count() == 1, "the %s card is not a link on %s" % (card_testid, page.url)
    card.click()
    page.wait_for_url(re.compile(listed), timeout=PAGE_TIMEOUT)
    held = rows(page)
    assert held == figure, "%s says %d and opens a list of %d rows (%s)" % (card_testid, figure, held, page.url)
    if shot:
        _frame(page, shot, viewport["width"], theme)


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_every_count_card_opens_the_rows_it_counts(browser, live_server, viewport, theme):
    noun = "Cards%s" % uuid.uuid4().hex[:6]
    other = "Rival%s" % uuid.uuid4().hex[:6]
    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    _seed(mine["org_id"], noun, filler=FILLER, critical=OPEN_CRITICAL - 1, closed=3, signals=True)
    # Their gaps: fewer, with a resolved critical one too, so a count across organisations is a different number.
    _seed(theirs["org_id"], other, filler=3, critical=4, closed=2, covered=False)

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    base = live_server
    width = viewport["width"]
    sign_in(page, base, mine["emails"]["enterprise_architect"])
    register = base + "/enterprise/implementation/gap-analysis"
    try:
        # ---- Gap analysis: counts come from count queries, so they hold with more gaps than the list shows
        page.goto(register, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert (_number(page, "gap-stat-recorded-value"), _number(page, "gap-stat-critical-value"),
                _number(page, "gap-stat-in-progress-value"), _number(page, "gap-stat-resolved-value")
                ) == (RECORDED, CRITICAL, MEDIUM, CLOSED)
        body = page.locator("body").inner_text()
        assert "%d still open" % OPEN_CRITICAL in body, body[:600]
        assert "severity or priority" in body and "by any means" in body
        assert other not in body
        assert _gap_rows(page) == 500
        assert "Showing gaps 1 to 500 of %d" % RECORDED in page.get_by_test_id("gap-page-note").inner_text()
        _frame(page, "gap-analysis", width, theme)
        page.get_by_test_id("gap-page-next").click()
        page.wait_for_url(re.compile(r"page=2"), timeout=PAGE_TIMEOUT)
        assert _gap_rows(page) == RECORDED - 500
        page.goto(register, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _click_card(page, "gap-stat-critical", r"severity=critical", CRITICAL, rows=_gap_rows,
                    shot="gaps-critical", viewport=viewport, theme=theme)
        assert "%d of %d" % (CRITICAL, RECORDED) in page.get_by_test_id("gap-severity-filter").inner_text()
        assert other not in page.locator("body").inner_text()
        page.goto(register, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _click_card(page, "gap-stat-in-progress", r"status=in_progress", MEDIUM, rows=_gap_rows)
        page.goto(register, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _click_card(page, "gap-stat-resolved", r"status=closed", CLOSED, rows=_gap_rows,
                    shot="gaps-resolved", viewport=viewport, theme=theme)
        page.goto(register, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _click_card(page, "gap-stat-recorded", r"gap-analysis$", 500, rows=_gap_rows)

        # ---- AI architecture analysis: "Open Gaps (Critical)" counts open gaps and opens exactly those
        page.goto(base + "/enterprise/analysis/ai-architecture", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert _number(page, "ai-analysis-gaps-critical-value") == OPEN_CRITICAL
        assert _number(page, "ai-analysis-gaps-high-value") == HIGH
        _frame(page, "ai-analysis", width, theme)
        _click_card(page, "ai-analysis-gaps-critical", r"severity=critical&status=open", OPEN_CRITICAL, rows=_gap_rows)
        assert "resolved critical gap" not in page.locator("body").inner_text()
        page.go_back()
        _click_card(page, "ai-analysis-gaps-high", r"severity=high&status=open", HIGH, rows=_gap_rows)

        # ---- Implementation page: the critical gaps listed are open ones, the resolved one is not first
        page.goto(base + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("critical-gaps-total").wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert "Showing 5 of %d open critical gaps" % OPEN_CRITICAL in page.get_by_test_id("critical-gaps-total").inner_text()
        listed = page.locator('[data-testid="critical-gaps-list"]').inner_text()
        assert "resolved critical gap" not in listed and other not in listed, listed
        _frame(page, "implementation", width, theme)
        page.get_by_test_id("critical-gaps-register-link").click()
        page.wait_for_url(re.compile(r"severity=critical&status=open"), timeout=PAGE_TIMEOUT)
        assert _gap_rows(page) == OPEN_CRITICAL

        # ---- The earlier gap-severity address lands on the register, which counts open gaps by severity
        old_address = base + "/enterprise/strategic/risk-assessment"
        page.goto(old_address, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.url == register, page.url
        assert _gap_rows(page) == 500
        assert [_number(page, "gap-open-%s-value" % level) for level in ("critical", "high", "medium", "low")] == [
            OPEN_CRITICAL, HIGH, MEDIUM, LOW]
        assert "Total Gaps" not in page.locator("body").inner_text()
        _frame(page, "old-address", width, theme)
        for level, count in (("critical", OPEN_CRITICAL), ("high", HIGH), ("medium", MEDIUM), ("low", LOW)):
            page.goto(register, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            _click_card(page, "gap-open-" + level, r"severity=%s&status=open" % level, count, rows=_gap_rows,
                        shot="gaps-open-" + level if level == "high" else None, viewport=viewport, theme=theme)
        page.goto(old_address + "?severity=critical", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.url == register + "?severity=critical&status=open", page.url
        # the old address lists the open gaps its figure counted, not the resolved critical one as well
        assert _gap_rows(page) == OPEN_CRITICAL
        assert "%d of %d" % (OPEN_CRITICAL, RECORDED) in page.get_by_test_id("gap-severity-filter").inner_text()
        page.goto(old_address + "?severity=bogus", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.url == register, page.url
        # the real Risk assessment keeps its own figures
        page.goto(base + "/strategic/risk-assessment", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator("h1").first.inner_text().strip().lower() == "risk assessment"
        assert (_number(page, "assess-open-value"), _number(page, "assess-critical-value")) == (3, 2)

        # ---- A page past the end shows the last page with its note; a huge number is not an offset
        last_rows = RECORDED - 500
        for number in (99, 10 ** 12):
            page.goto("%s?page=%d" % (register, number), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert _gap_rows(page) == last_rows, number
            assert "Showing gaps 501 to %d of %d" % (RECORDED, RECORDED) in page.get_by_test_id(
                "gap-page-note").inner_text()
            assert "No gaps recorded yet" not in page.locator("body").inner_text()
            if number == 99:
                _frame(page, "gaps-page-99", width, theme)

        # ---- A filter with no rows says which filter, and offers the way back
        page.goto(register + "?severity=high&status=closed", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        empty = page.get_by_test_id("gaps-filter-empty")
        empty.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        text = empty.inner_text()
        assert "high severity, resolved" in text and "%d gaps" % RECORDED in text, text
        assert "No gaps recorded yet" not in page.locator("body").inner_text()
        _frame(page, "gaps-filter-empty", width, theme)
        page.get_by_test_id("gaps-filter-empty-clear").click()
        page.wait_for_url(re.compile(r"gap-analysis$"), timeout=PAGE_TIMEOUT)
        assert _gap_rows(page) == 500

        # ---- Risk assessment: open, critical, high, medium risks each open the register's rows
        assessment = base + "/strategic/risk-assessment"
        page.goto(assessment, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        open_risks, critical_risks = _number(page, "assess-open-value"), _number(page, "assess-critical-value")
        assert (open_risks, critical_risks) == (3, 2), (open_risks, critical_risks)
        _frame(page, "risk-assessment", width, theme)
        _click_card(page, "assess-open", r"/risks/\?show=open", open_risks, rows=_risk_rows,
                    shot="risks-open", viewport=viewport, theme=theme)
        assert other not in page.locator("body").inner_text()
        page.goto(assessment, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _click_card(page, "assess-critical", r"/risks/\?show=critical", critical_risks, rows=_risk_rows)
        assert "Showing only the open risks at the critical level (2 of 7)" in page.get_by_test_id(
            "register-filter-note").inner_text()
        # a card that counts nothing is not a link to an empty list: no open risk here is medium or high
        page.goto(assessment, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator('a[data-testid="assess-medium"]').count() == 0 == page.locator('a[data-testid="assess-high"]').count()

        # ---- Risks page: total, open, critical and mitigated each open their rows
        page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert (_number(page, "register-total"), _number(page, "register-open"),
                _number(page, "register-critical"), _number(page, "register-mitigated")) == (7, 3, 2, 2)
        _frame(page, "risks", width, theme)
        for card, show, count in (("open", "open", 3), ("critical", "critical", 2), ("mitigated", "mitigated", 2),
                                  ("total", None, 7)):
            page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            _click_card(page, "register-card-" + card, r"/risks/(\?show=%s)?$" % show if show else r"/risks/$",
                        count, rows=_risk_rows)

        # ---- Change board: Recorded, No work planned, Covered and Closed open the register rows they count
        board = base + "/adm-kanban/gap-analysis"
        page.goto(board, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        total, unplanned, covered, closed = (_number(page, t) for t in ("gap-total", "gap-unclosed", "gap-covered", "gap-closed"))
        assert (total, covered, closed) == (RECORDED, 1, CLOSED), (total, covered, closed)
        assert unplanned == RECORDED - 1 - CLOSED and total == unplanned + covered + closed
        # The empty table's message and "Add a gap record" are on the page and not under a pinned column.
        message = page.get_by_test_id("gaps-table-empty")
        button = page.get_by_role("link", name="Add a gap record")
        for target in (message, button):
            target.scroll_into_view_if_needed()
            box = target.bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= width, (box, width)
            on_top = target.evaluate(
                "el => { const r = el.getBoundingClientRect(); const t = document.elementFromPoint("
                "r.x + r.width / 2, r.y + r.height / 2); return el.contains(t); }")
            assert on_top, "something covers %s at %d wide" % (target, width)
        _frame(page, "board", width, theme)
        for key, listed_url, count in (
                ("gap-unclosed", r"status=open&work=none", unplanned),
                ("gap-covered", r"status=open&work=covered", covered),
                ("gap-closed", r"status=closed", closed)):
            page.goto(board, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            link = page.locator('a:has([data-testid="%s"])' % key)
            assert link.count() == 1, "%s is not a link on the board" % key
            link.click()
            page.wait_for_url(re.compile(listed_url), timeout=PAGE_TIMEOUT)
            held = _gap_rows(page)
            assert held == (min(count, 500)), (key, count, held)
            if count > 500:
                assert "of %d" % count in page.get_by_test_id("gap-page-note").inner_text()
        # The Gap signals figure opens the page that lists those signals, with the same figure on it
        page.goto(board, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        signals = _number(page, "gap-signals")
        assert signals > 0
        _frame(page, "board-signals", width, theme)
        link = page.locator('a:has([data-testid="gap-signals"])')
        assert link.count() == 1, "Gap signals is not a link on the board"
        link.click()
        page.wait_for_url(re.compile(r"/capability-roadmap"), timeout=PAGE_TIMEOUT)
        landed = page.get_by_test_id("rdm-gap-signals")
        landed.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        page.wait_for_function("el => /: [0-9]+ signals/.test(el.innerText)", arg=landed.element_handle(),
                               timeout=PAGE_TIMEOUT)
        assert "Detected: %d signals" % signals in landed.inner_text(), landed.inner_text()
        # the page lists each signal the figure counts, one row for each, and each row opens its capability
        listed = page.locator('[data-testid="gap-signal-row"]')
        listed.first.wait_for(state="visible", timeout=PAGE_TIMEOUT)
        assert listed.count() == signals, (signals, listed.count())
        assert "#gap-signals" in page.url, page.url
        first = listed.first.locator("a").first
        first_name = first.inner_text().strip()
        assert noun in first_name, first_name
        _frame(page, "signals-landing", width, theme)
        first.click()
        page.wait_for_url(re.compile(r"/capabilities/\d+/fact-sheet"), timeout=PAGE_TIMEOUT)
        assert first_name in page.locator("body").inner_text(), page.locator("body").inner_text()[:300]
        page.go_back(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert other not in page.locator("body").inner_text()
    finally:
        context.close()

    # The other organisation reads its own figures, from the same pages.
    other_context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    other_page = other_context.new_page()
    try:
        sign_in(other_page, base, theirs["emails"]["enterprise_architect"])
        other_page.goto(register, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        theirs_recorded = 3 + 4 + 1 + 1 + HIGH + MEDIUM + LOW + 2
        assert _number(other_page, "gap-stat-recorded-value") == theirs_recorded
        assert _number(other_page, "gap-stat-critical-value") == 6
        assert noun not in other_page.locator("body").inner_text()
    finally:
        other_context.close()
