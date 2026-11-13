"""Nobody reads two different totals for "risks".

An enterprise architect holds seven recorded risks (five open, two critical) and three recorded gaps.
From Strategic planning they open the Risk Assessment entries and read the Risks register.
The page that counts risks shows the register's figures. The page that counts gaps says "gaps" in its
title, its description and every figure label, and sends the reader to the Risks register, which
shows its own totals. A second organisation holds risks and gaps of its own that never reach these figures.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [{"width": 1440, "height": 900}, {"width": 390, "height": 844}]


def _seed(org_id, noun, risks, gaps):
    from app import create_app, db
    from app.models.risk import Risk, RiskStatus
    from app.services.gap_register_service import create_gap

    with create_app("testing").app_context():
        for index, (likelihood, impact, status) in enumerate(risks):
            db.session.add(Risk(
                title="%s risk %d" % (noun, index), description="What could go wrong %d." % index,
                likelihood=likelihood, impact=impact, status=RiskStatus(status),
                owner="Risk Owner", organization_id=org_id))
        db.session.commit()
        for index, (priority, severity) in enumerate(gaps):
            create_gap(org_id, "%s gap %d" % (noun, index), priority=priority, severity=severity)
        db.session.commit()
        db.session.remove()


def _figures(page, labels):
    text = page.locator("main").inner_text()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return {lines[i]: lines[i + 1] for i in range(len(lines) - 1) if lines[i] in labels}


def _frame(page, name, viewport):
    out = os.environ.get("RISK_FIGURES_FRAMES_DIR")
    if not out:
        return
    os.makedirs(out, exist_ok=True)
    dark = viewport["width"] == 390
    if dark:
        page.evaluate("document.documentElement.classList.add('dark')")
    page.screenshot(path="%s/%s-%d%s.png" % (out, name, viewport["width"], "-dark" if dark else ""), full_page=True)
    if dark:
        page.evaluate("document.documentElement.classList.remove('dark')")


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_risk_assessment_pages_agree_with_the_risks_register(browser, live_server, viewport):
    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    noun = "Fig%s" % mine["suffix"]
    # Seven risks: two open critical (25, 20), one open high (9), one open medium (6), one open low (2),
    # one mitigated critical, one closed low.
    _seed(mine["org_id"], noun,
          [(5, 5, "open"), (5, 4, "open"), (3, 3, "open"), (3, 2, "open"), (1, 2, "open"),
           (5, 5, "mitigated"), (1, 1, "closed")],
          [("critical", "medium"), ("high", "high"), ("low", "low")])
    _seed(theirs["org_id"], "Their%s" % theirs["suffix"],
          [(5, 5, "open")] * 4, [("critical", "critical")] * 2)

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])

        page.goto(live_server + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="register-open"]')).to_have_count(1, timeout=PAGE_TIMEOUT)
        register_open = int(page.locator('[data-testid="register-open"]').inner_text().strip())
        register_critical = int(page.locator('[data-testid="register-critical"]').inner_text().strip())
        assert (register_open, register_critical) == (5, 2), (register_open, register_critical)

        # The Strategic planning card that names Risk Assessment lands on a page whose figures
        # are either the register's risks or plainly gaps.
        page.goto(live_server + "/enterprise/strategic", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        cards = page.get_by_role("link", name=re.compile("Risk Assessment"))
        assert cards.count() >= 1
        card_texts = cards.all_inner_texts()
        assert all("gap" in t.lower() for t in card_texts), card_texts
        cards.first.click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.get_by_role("heading", level=1)).to_contain_text("Gap", timeout=PAGE_TIMEOUT)
        main = page.locator("main").inner_text()
        assert "gaps" in main.lower()
        figures = _figures(page, ("Total gaps", "Critical or high gaps", "Medium gaps", "Low gaps"))
        assert figures == {"Total gaps": "3", "Critical or high gaps": "2", "Medium gaps": "0", "Low gaps": "1"}, figures
        assert "High Risk" not in main and "Medium Risk" not in main and "Low Risk" not in main
        assert "Their%s" % theirs["suffix"] not in main
        _frame(page, "gap-risk-assessment", viewport)

        # The gap page sends the reader to the register, which still shows its own totals.
        page.get_by_role("link", name=re.compile("Risks register")).first.click()
        page.wait_for_url(re.compile(r"/risks/"), timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="register-open"]')).to_have_text(str(register_open), timeout=PAGE_TIMEOUT)

        # The page that counts risks (the sidebar entry) shows the register's figures.
        page.goto(live_server + "/strategic/risk-assessment", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="assess-open-value"]')).to_have_text(str(register_open), timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="assess-critical-value"]')).to_have_text(str(register_critical))
        _frame(page, "risk-assessment", viewport)
    finally:
        context.close()
