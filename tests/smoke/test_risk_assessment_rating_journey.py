"""The earlier open-gaps-by-severity address lands on the gap register, which rates every gap its own way.

An enterprise architect opens the address that used to be called Risk Assessment in an organisation holding a
gap that is critical by priority only, one critical by severity only, a high, a medium and a low gap, and a
second organisation's critical gap. She lands on the register, whose open counts for Critical, High, Medium
and Low equal the register's own rating of each gap, every gap name opens that gap's page, and the other
organisation's gap never appears. A severity in the address keeps the list on that severity, an unknown one
is dropped, and a signed-out visitor is asked to sign in. An organisation with no gaps lands on the
register's empty state.
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
    return {level: page.locator('[data-testid="gap-open-%s-value"]' % level).inner_text().strip()
            for level in ("critical", "high", "medium", "low")}


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_the_old_address_lands_on_the_register_whose_counts_follow_its_ratings(browser, live_server, viewport):
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

        # what a person reads in each row's Severity cell (the narrow layout's line under the name) is the severity
        # that was recorded, not only the attribute the link carries
        seeded = {"priority_only": "critical", "severity_only": "critical", "high": "high", "medium": "medium",
                  "low": "low"}
        for key, name in names.items():
            row = page.locator('[data-testid="gap-row"]').filter(has=page.get_by_role("link", name=name, exact=True))
            expect(row).to_have_count(1)
            shown = row.inner_text().replace(name, "").lower()
            assert seeded[key] in shown, (key, shown)
            assert register[key] == seeded[key], (key, register[key])

        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.url == live_server + REGISTER, page.url
        expect(page.get_by_text(names["low"], exact=True)).to_be_visible(timeout=PAGE_TIMEOUT)
        tiles = _tiles(page)
        assert tiles == {"critical": "2", "high": "1", "medium": "1", "low": "1"}, tiles
        for level in tiles:
            assert tiles[level] == str(sum(1 for v in register.values() if v == level)), (level, tiles, register)
        for key, name in names.items():
            link = page.get_by_role("link", name=name, exact=True)
            expect(link).to_have_count(1)
            assert link.get_attribute("data-severity") == register[key]
            row = page.locator('[data-testid="gap-row"]').filter(has=link)
            shown = row.inner_text().replace(name, "").lower()
            assert seeded[key] in shown, (key, shown)
        expect(page.get_by_text(other)).to_have_count(0)
        _frame(page, "risk-assessment", viewport)

        for level in ("critical", "high"):
            page.goto("%s%s?severity=%s" % (live_server, PATH, level), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert page.url == "%s%s?severity=%s&status=open" % (live_server, REGISTER, level), page.url
            listed = page.locator('[data-testid="gap-row"]')
            expect(listed).to_have_count(int(tiles[level]), timeout=PAGE_TIMEOUT)
            expect(page.get_by_text(other)).to_have_count(0)
        page.goto(live_server + PATH + "?severity=bogus", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.url == live_server + REGISTER, page.url

        for name in names.values():
            page.goto(live_server + REGISTER, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.get_by_role("link", name=name, exact=True).click()
            page.wait_for_url(re.compile(r"/Gap/\d+"), timeout=PAGE_TIMEOUT)
            expect(page.get_by_role("heading", name=name)).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_the_old_address_with_no_gaps_lands_on_the_registers_empty_state(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    context, page = _context(browser, VIEWPORTS[0])
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.url == live_server + REGISTER, page.url
        expect(page.get_by_test_id("gaps-empty")).to_contain_text("No gaps recorded yet", timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_the_old_address_asks_a_signed_out_visitor_to_sign_in(browser, live_server):
    context, page = _context(browser, VIEWPORTS[0])
    try:
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/gap-analysis" not in page.url and "login" in page.url, page.url
    finally:
        context.close()
