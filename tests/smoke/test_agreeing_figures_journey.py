"""One number per layer, risk and gap: the same on every screen that states it.

An enterprise architect opens the architecture dashboard and reads each layer tab's
count against the list beneath it, opens the Risk Assessment and reads its figures
against the Risks page, and reads the critical gap figure on the gap analysis page
against the one on the implementation page, and the gap register's Resolved figure against
the board's Closed figure. The organisation holds more critical gaps than the implementation
page lists, closed risks beside open ones and critical risks that are closed, so a screen that
counts the wrong population shows a different number. A second organisation holds its own
elements, risks and gaps so a leak between organisations would change a number. A layer's
figures read as loading while the list is fetched and say unavailable only when it failed.
"""
import os
import re
import uuid

import pytest

from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [
    ({"width": 1440, "height": 900}, "light"),
    ({"width": 390, "height": 844}, "dark"),
]


def _seed(org_id, noun, *, goals, stray, critical_gaps, closed_gaps=0, closed_risks=0):
    """Motivation elements (mirrored, unmirrored and one of a type the layer does not hold), risks and gaps."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import Plateau
    from app.models.motivation import Goal
    from app.models.risk import Risk, RiskStatus
    from app.services.gap_register_service import create_gap

    with create_app("testing").app_context():
        for index in range(goals):
            db.session.add(ArchiMateElement(
                name="%s architecture goal %d" % (noun, index), type="Goal", layer="Motivation",
                organization_id=org_id))
        for index in range(stray):
            db.session.add(ArchiMateElement(
                name="%s stray node %d" % (noun, index), type="Node", layer="Motivation",
                organization_id=org_id))
        db.session.commit()
        # Goals recorded only in the goal table (no architecture row behind them).
        for index in range(2):
            db.session.add(Goal(name="%s portfolio goal %d" % (noun, index), organization_id=org_id))
        db.session.commit()
        for title, likelihood, impact in ((noun + " severe", 5, 5), (noun + " serious", 4, 4), (noun + " minor", 1, 1)):
            db.session.add(Risk(title=title, description="%s could happen." % title, likelihood=likelihood,
                                impact=impact, status=RiskStatus("open"), owner="Owner",
                                mitigation_plan="Plan.", organization_id=org_id))
        # Risks that are no longer open: not current exposure, so no screen counts or lists them as open,
        # the critical ones included.
        for index in range(closed_risks):
            db.session.add(Risk(title="%s closed risk %d" % (noun, index), description="Dealt with.",
                                likelihood=5, impact=5, status=RiskStatus("closed" if index % 2 else "mitigated"),
                                owner="Owner", mitigation_plan="Done.", organization_id=org_id))
        db.session.commit()
        for index in range(critical_gaps):
            create_gap(org_id, "%s critical gap %d" % (noun, index), severity="critical", priority="medium")
        # Rated high on severity but critical on priority: critical by the one rule of the gap register.
        create_gap(org_id, "%s urgent by priority" % noun, severity="high", priority="critical")
        for index, status in enumerate(("resolved", "closed", "completed")[:closed_gaps]):
            create_gap(org_id, "%s settled gap %d" % (noun, index), severity="low", priority="low",
                       resolution_status=status)
        create_gap(org_id, "%s open gap" % noun, severity="low", priority="low")
        # A plateau-to-plateau step is not a capability shortfall and is not counted as a gap.
        today = Plateau(name="%s today" % noun, sequence_order=1, organization_id=org_id)
        later = Plateau(name="%s later" % noun, sequence_order=2, organization_id=org_id)
        db.session.add_all([today, later])
        db.session.flush()
        step, _ = create_gap(org_id, "%s plateau step" % noun, severity="critical", priority="critical")
        step.gap_kind = "plateau_transition"
        step.originating_plateau_id, step.target_plateau_id = today.id, later.id
        db.session.commit()
        db.session.remove()


def _db_layer_total(org_id, layer_value):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.models.motivation import Goal

    with create_app("testing").app_context():
        architecture = ArchiMateElement.query.filter(
            ArchiMateElement.organization_id == org_id,
            ArchiMateElement.type.in_(["Goal", "Assessment"]),  # a risk is mirrored as an assessment
            db.func.lower(ArchiMateElement.layer) == layer_value).count()
        recorded = Goal.query.filter(Goal.organization_id == org_id,
                                     Goal.archimate_element_id.is_(None)).count()
        db.session.remove()
        return architecture + recorded


def _frame(page, name, width, theme):
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        # Colours change over 150 ms when the class flips; freeze them so the frame is the settled page.
        # (A constructable stylesheet: the page's content security policy blocks an inline style element.)
        page.evaluate("""() => {
            const sheet = new CSSStyleSheet();
            sheet.replaceSync('*, *::before, *::after { transition: none !important; animation: none !important; }');
            document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
        }""")
        page.evaluate("theme => document.documentElement.classList.toggle('dark', theme === 'dark')", theme)
        page.wait_for_function(
            "theme => document.documentElement.classList.contains('dark') === (theme === 'dark')", arg=theme)
        page.screenshot(path="%s/figures-%s-%d-%s.png" % (folder, name, width, theme), full_page=True)


def _number(page, testid):
    figure = page.locator('[data-testid="%s"]' % testid)
    assert figure.count() >= 1, "no %s on %s: %s" % (testid, page.url, page.locator("body").inner_text()[:400])
    return int(re.search(r"\d+", figure.first.inner_text()).group(0))


def _tab_count(page, layer_name):
    tab = page.locator("#layer-spine-scroll a", has_text=layer_name).locator("span.tabular-nums")
    tab.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    return int(tab.inner_text())


def _list_total(page):
    footer = page.locator('[data-testid="architecture-pagination"] p').first
    footer.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    return int(re.search(r"of\s+(\d+)", footer.inner_text()).group(1))


def _new_page(browser, viewport):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_layer_risk_and_gap_figures_agree(browser, live_server, viewport, theme):
    noun = "Fig%s" % uuid.uuid4().hex[:6]
    other = "Rival%s" % uuid.uuid4().hex[:6]
    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    # 7 critical-by-severity gaps and 1 critical by priority: more than the 5 the implementation page lists.
    _seed(mine["org_id"], noun, goals=3, stray=2, critical_gaps=7, closed_gaps=2, closed_risks=4)
    _seed(theirs["org_id"], other, goals=7, stray=1, critical_gaps=4, closed_gaps=3, closed_risks=2)

    context, page = _new_page(browser, viewport)
    base = live_server
    sign_in(page, base, mine["emails"]["enterprise_architect"])
    try:
        # A layer's tab as shown on another layer's page (before it is opened), the same tab once it is
        # opened, the list beneath it and the database: one number.
        page.goto(base + "/architecture/dashboard?layer=strategy", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        tab_before = _tab_count(page, "Motivation")
        page.goto(base + "/architecture/dashboard?layer=motivation", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        list_total = _list_total(page)
        tab_after = _tab_count(page, "Motivation")
        card = page.locator('[data-testid="repository-elements-card"]')
        expect(card).to_have_attribute("aria-busy", "false", timeout=PAGE_TIMEOUT)
        card_total = _number(page, "repository-elements-card")
        expected = _db_layer_total(mine["org_id"], "motivation")
        # 3 goals + 3 open and 4 closed risk assessments + 2 goals held only in the goal table; the 2 nodes are not motivation elements
        assert expected == 12, expected
        assert tab_before == tab_after == list_total == card_total == expected, (
            tab_before, tab_after, list_total, card_total, expected)
        assert other not in page.locator("body").inner_text()
        _frame(page, "layer", viewport["width"], theme)

        # Risks page, then the Risk Assessment: about risks, counting and listing the open ones, which
        # are the register's open risks (closed ones, critical included, are in neither figure).
        page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        register_critical = _number(page, "register-critical")
        register_open = _number(page, "register-open")
        assert (register_critical, register_open) == (2, 3), (register_critical, register_open)
        # The Risk Assessment card on Strategic planning opens the assessment of the register's risks.
        page.goto(base + "/enterprise/strategic", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name=re.compile("Risk Assessment")).first.click()
        page.wait_for_url(re.compile(r"/strategic/risk-assessment$"), timeout=PAGE_TIMEOUT)
        body = page.locator("body").inner_text()
        assert page.locator("h1").first.inner_text().strip().lower() == "risk assessment"
        assert "Total Gaps" not in body and "Risk Items" not in body, body[:500]
        assert _number(page, "assess-critical-value") == register_critical
        assert _number(page, "assess-open-value") == register_open
        rows = page.locator('[data-testid="risk-assessment-row"]')
        assert rows.count() == register_open, (rows.count(), register_open)
        assert noun in rows.first.inner_text() and other not in body
        listed_critical = sum(1 for index in range(rows.count()) if "critical" in rows.nth(index).inner_text().lower())
        assert listed_critical == register_critical, (listed_critical, register_critical)
        assert "closed risk" not in body
        note = page.locator('[data-testid="assess-closed-note"]').inner_text()
        assert note.startswith("4 risks are mitigated, accepted or closed and not listed here"), note
        page.get_by_role("link", name="See every risk in the risk register").click()
        page.wait_for_url(re.compile(r"/risks/?$"), timeout=PAGE_TIMEOUT)
        assert _number(page, "register-open") == register_open
        page.go_back()
        page.locator('[data-testid="risk-assessment-row"] a').first.click()
        page.wait_for_url(re.compile(r"/risks/\d+"), timeout=PAGE_TIMEOUT)
        assert noun in page.locator("body").inner_text()
        page.go_back()
        _frame(page, "risk-assessment", viewport["width"], theme)

        # Critical gaps: the register page, the implementation page and the register's own list say the
        # same number (7 by severity and 1 by priority), the implementation page says how many it does not list.
        page.goto(base + "/enterprise/implementation/gap-analysis", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        register_gaps = _number(page, "gap-stat-critical-value")
        assert register_gaps == 8, register_gaps
        register_resolved = _number(page, "gap-stat-resolved-value")
        assert register_resolved == 2, register_resolved
        assert "No gap has been closed yet" not in page.locator("body").inner_text()
        _frame(page, "gap-analysis", viewport["width"], theme)
        page.goto(base + "/implementation/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        rows = page.locator('[data-testid="critical-gap-row"]')
        expect(rows).to_have_count(5, timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("critical-gaps-total")).to_contain_text(
            "Showing 5 of %d critical gaps" % register_gaps)
        text = page.locator('[data-testid="critical-gaps-list"]').inner_text()
        assert "plateau step" not in text and other not in text
        _frame(page, "implementation-gaps", viewport["width"], theme)
        page.get_by_test_id("critical-gaps-register-link").click()
        page.wait_for_url(re.compile("gap-analysis"), timeout=PAGE_TIMEOUT)
        assert page.locator('[data-severity="critical"]').count() == register_gaps

        # The board says the same about closed: resolved gaps are closed, planning work is not closing.
        page.goto(base + "/adm-kanban/gap-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        board_closed = _number(page, "gap-closed")
        total, unplanned, covered = (_number(page, t) for t in ("gap-total", "gap-unclosed", "gap-covered"))
        assert board_closed == register_resolved == 2, (board_closed, register_resolved)
        assert total == 11 and total == unplanned + covered, (total, unplanned, covered)
        assert "Unclosed" not in page.locator("body").inner_text()
        _frame(page, "board-gaps", viewport["width"], theme)
    finally:
        context.close()


def _business_org(noun):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    org = create_fresh_org("enterprise_architect")
    with create_app("testing").app_context():
        for index in range(4):
            db.session.add(ArchiMateElement(name="%s actor %d" % (noun, index), type="BusinessActor",
                                            layer="Business", organization_id=org["org_id"]))
        db.session.commit()
        db.session.remove()
    return org


@pytest.mark.parametrize("viewport,theme", VIEWPORTS)
def test_layer_figures_read_as_loading_then_the_total_and_unavailable_only_on_failure(
        browser, live_server, viewport, theme):
    noun = "Load%s" % uuid.uuid4().hex[:6]
    org = _business_org(noun)
    context, page = _new_page(browser, viewport)
    held = []
    page.route("**/architecture/api/layer/business/elements*", lambda route: held.append(route))
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/architecture/dashboard?layer=business", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        card = page.locator('[data-testid="repository-elements-card"]')
        # The list request is held: the figures say they are loading, with the loading state, not a dash.
        expect(card).to_have_attribute("aria-busy", "true", timeout=PAGE_TIMEOUT)
        expect(card.locator('[data-state="loading"]')).to_be_visible()
        body = page.locator("body").inner_text()
        assert "Repository total unavailable" not in body and "— total" not in body, body[:600]
        expect(page.locator('[data-testid="layer-total"]')).to_have_text("Loading")
        _frame(page, "layer-loading", viewport["width"], theme)

        while not held:
            page.wait_for_timeout(100)
        held.pop().continue_()
        expect(card).to_have_attribute("aria-busy", "false", timeout=PAGE_TIMEOUT)
        assert _number(page, "repository-elements-card") == 4
        expect(page.locator('[data-testid="layer-total"]')).to_have_text("4 total")
        assert _tab_count(page, "Business") == 4 == _list_total(page)
    finally:
        context.close()

    context, page = _new_page(browser, viewport)
    page.route("**/architecture/api/layer/business/elements*", lambda route: route.fulfill(
        status=500, content_type="application/json", body='{"error": "down"}'))
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/architecture/dashboard?layer=business", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        # The request failed: now, and only now, the page says the total is unavailable.
        expect(card_of(page)).to_contain_text("Repository total unavailable", timeout=PAGE_TIMEOUT)
        expect(card_of(page)).to_have_attribute("aria-busy", "false")
        expect(page.locator('[data-testid="layer-total"]')).to_have_text("— total")
        _frame(page, "layer-failed", viewport["width"], theme)
    finally:
        context.close()


def card_of(page):
    return page.locator('[data-testid="repository-elements-card"]')
