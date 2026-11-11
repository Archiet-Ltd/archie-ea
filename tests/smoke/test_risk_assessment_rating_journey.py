"""The Risk Assessment page rates every gap the way the gap register does.

An enterprise architect opens Risk Assessment in an organisation holding a gap that is critical by
priority only, one critical by severity only, a high, a medium and a low gap, and a second
organisation's critical gap. The High Risk tile counts the gaps the register rates critical or high,
each row's Risk Level is the register's rating for that gap, every gap name opens that gap's page, and
the other organisation's gap never appears. An organisation with no gaps says so.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/enterprise/strategic/risk-assessment"
REGISTER = "/enterprise/implementation/gap-analysis"
VIEWPORTS = [{"width": 1440, "height": 900}, {"width": 390, "height": 844}]


def _context(browser, viewport):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _frame(page, name, viewport):
    out = os.environ.get("RISK_ASSESSMENT_FRAMES_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    dark = viewport["width"] == 390
    if dark:
        page.evaluate("document.documentElement.classList.add('dark')")
    page.screenshot(path="%s/%s-%d%s.png" % (out, name, viewport["width"], "-dark" if dark else ""), full_page=True)
    if dark:
        page.evaluate("document.documentElement.classList.remove('dark')")


def _tiles(page):
    text = page.locator("div.grid.md\\:grid-cols-4").first.inner_text()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return {lines[i]: lines[i + 1] for i in range(len(lines) - 1) if lines[i] in ("Total Gaps", "High Risk", "Medium Risk", "Low Risk")}


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_risk_assessment_rates_each_gap_as_the_register_does(browser, live_server, viewport):
    from app import create_app, db
    from app.services.gap_register_service import create_gap

    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    s = mine["suffix"]
    names = {
        "priority_only": "Critical by priority %s" % s,
        "severity_only": "Critical by severity %s" % s,
        "high": "High gap %s" % s,
        "medium": "Medium gap %s" % s,
        "low": "Low gap %s" % s,
    }
    other = "Their critical %s" % theirs["suffix"]
    with create_app("testing").app_context():
        create_gap(mine["org_id"], names["priority_only"], priority="critical", severity="medium")
        create_gap(mine["org_id"], names["severity_only"], priority="high", severity="critical")
        create_gap(mine["org_id"], names["high"], priority="high", severity="high")
        create_gap(mine["org_id"], names["medium"], priority="medium", severity="medium")
        create_gap(mine["org_id"], names["low"], priority="low", severity="low")
        create_gap(theirs["org_id"], other, priority="critical", severity="critical")
        db.session.commit()
        db.session.remove()

    context, page = _context(browser, viewport)
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])

        page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        register = {}
        for key, name in names.items():
            link = page.get_by_role("link", name=name, exact=True)
            expect(link).to_have_count(1, timeout=PAGE_TIMEOUT)
            register[key] = link.get_attribute("data-severity")
        assert register == {
            "priority_only": "critical", "severity_only": "critical", "high": "high", "medium": "medium", "low": "low",
        }, register

        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(names["low"], exact=True)).to_be_visible(timeout=PAGE_TIMEOUT)
        tiles = _tiles(page)
        assert tiles == {"Total Gaps": "5", "High Risk": "3", "Medium Risk": "1", "Low Risk": "1"}, tiles
        assert tiles["High Risk"] == str(sum(1 for v in register.values() if v in ("critical", "high")))
        for key, name in names.items():
            row = page.locator("tbody tr").filter(has=page.get_by_role("link", name=name, exact=True))
            expect(row).to_have_count(1)
            expect(row.locator("td").nth(1)).to_have_text(register[key].title())
        expect(page.get_by_text(other)).to_have_count(0)
        _frame(page, "risk-assessment", viewport)

        for name in names.values():
            page.get_by_role("link", name=name, exact=True).click()
            page.wait_for_url(re.compile(r"/Gap/\d+"), timeout=PAGE_TIMEOUT)
            expect(page.get_by_role("heading", name=name)).to_be_visible(timeout=PAGE_TIMEOUT)
            page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_risk_assessment_with_no_gaps_says_so(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    context, page = _context(browser, VIEWPORTS[0])
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Risk Assessment")).to_be_visible(timeout=PAGE_TIMEOUT)
        assert _tiles(page) == {"Total Gaps": "0", "High Risk": "0", "Medium Risk": "0", "Low Risk": "0"}
        expect(page.locator("tbody")).to_contain_text("No")
    finally:
        context.close()
