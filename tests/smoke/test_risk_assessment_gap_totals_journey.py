"""An architect with more than 500 gaps reads the register's own totals on the Gap Risk Assessment page.

One organisation holds 501 gaps (101 critical, 100 high, 150 medium, 150 low) and another holds a few.
The page's four figures equal the gap register's count for the organisation, none capped at 500, and the
table says how many of how many gaps it lists and links to the Gaps page, which shows the full register.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORTS = [{"width": 1440, "height": 900}, {"width": 390, "height": 844}]
MINE = [("critical", 101), ("high", 100), ("medium", 150), ("low", 150)]
THEIRS = [("critical", 2), ("medium", 1)]


def _seed(org_id, noun, plan):
    from app import create_app, db
    from app.services.gap_register_service import create_gap

    with create_app("testing").app_context():
        for severity, number in plan:
            for index in range(number):
                create_gap(org_id, "%s %s gap %d" % (noun, severity, index), priority=severity, severity=severity)
        db.session.commit()
        counts = _register_counts(org_id)
        db.session.remove()
    return counts


def _register_counts(org_id):
    from app.services.gap_register_service import count_gaps

    return {
        "total": count_gaps(org_id),
        "high": count_gaps(org_id, severity="critical") + count_gaps(org_id, severity="high"),
        "medium": count_gaps(org_id, severity="medium"),
        "low": count_gaps(org_id, severity="low"),
    }


def _figures(page, labels):
    lines = [line.strip() for line in page.locator("main").inner_text().splitlines() if line.strip()]
    return {lines[i]: lines[i + 1] for i in range(len(lines) - 1) if lines[i] in labels}


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_gap_risk_assessment_totals_are_not_capped(browser, live_server, viewport):
    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    noun = "Cap%s" % mine["suffix"]
    my_counts = _seed(mine["org_id"], noun, MINE)
    their_counts = _seed(theirs["org_id"], "Their%s" % theirs["suffix"], THEIRS)
    assert my_counts == {"total": 501, "high": 201, "medium": 150, "low": 150}, my_counts
    assert their_counts["total"] == 3, their_counts

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])
        page.goto(live_server + "/enterprise/strategic/risk-assessment", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", level=1)).to_contain_text("Gap", timeout=PAGE_TIMEOUT)
        figures = _figures(page, ("Total gaps", "Critical or high gaps", "Medium gaps", "Low gaps"))
        assert figures == {
            "Total gaps": str(my_counts["total"]),
            "Critical or high gaps": str(my_counts["high"]),
            "Medium gaps": str(my_counts["medium"]),
            "Low gaps": str(my_counts["low"]),
        }, figures
        assert "Their%s" % theirs["suffix"] not in page.locator("main").inner_text()

        note = page.locator('[data-testid="gap-rows-note"]')
        expect(note).to_contain_text("of 501 gaps")
        shown = int(re.search(r"Showing (\d+) of 501", note.inner_text()).group(1))
        assert page.locator("main tbody tr").count() == shown

        out = os.environ.get("RISK_FIGURES_FRAMES_DIR")
        if out:
            os.makedirs(out, exist_ok=True)
            page.screenshot(path="%s/gap-totals-%d.png" % (out, viewport["width"]), full_page=True)

        note.get_by_role("link", name=re.compile("Gaps page")).click()
        page.wait_for_url(re.compile(r"/implementation/gap-analysis"), timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text(noun, timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text("501")
    finally:
        context.close()
