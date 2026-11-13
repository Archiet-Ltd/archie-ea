"""An architect with more than 500 gaps reads the register's own totals on the Gap register.

One organisation holds 501 gaps (101 critical, 100 high, 150 medium, 150 low) and another holds a few.
The old Gap Risk Assessment address lands on the gap register, whose four open-by-severity figures and its
recorded-gap figure equal the register's count for the organisation, none capped at the 500-row page, and
the page note says how many of how many gaps it lists.
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
PATH = "/enterprise/strategic/risk-assessment"
REGISTER = "/enterprise/implementation/gap-analysis"


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


def _tile(page, testid):
    return page.locator('[data-testid="%s-value"]' % testid).inner_text().strip()


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
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.url == live_server + REGISTER, page.url
        expect(page.get_by_role("heading", level=1)).to_contain_text("Gap", timeout=PAGE_TIMEOUT)

        recorded = _tile(page, "gap-stat-recorded")
        assert recorded == str(my_counts["total"]) == "501", recorded
        critical, high = _tile(page, "gap-open-critical"), _tile(page, "gap-open-high")
        assert int(critical) + int(high) == my_counts["high"] == 201, (critical, high)
        assert _tile(page, "gap-open-medium") == str(my_counts["medium"]) == "150"
        assert _tile(page, "gap-open-low") == str(my_counts["low"]) == "150"

        note = page.locator('[data-testid="gap-page-note"]')
        expect(note).to_contain_text("of 501", timeout=PAGE_TIMEOUT)
        shown = int(re.search(r"to (\d+) of 501", note.inner_text()).group(1))
        expect(page.locator('[data-testid="gap-row"]')).to_have_count(shown, timeout=PAGE_TIMEOUT)

        main = page.locator("main")
        expect(main).to_contain_text(noun, timeout=PAGE_TIMEOUT)
        expect(main).to_contain_text("501", timeout=PAGE_TIMEOUT)
        assert "Their%s" % theirs["suffix"] not in main.inner_text()

        out = os.environ.get("RISK_FIGURES_FRAMES_DIR")
        if out:
            os.makedirs(out, exist_ok=True)
            page.screenshot(path="%s/gap-totals-%d.png" % (out, viewport["width"]), full_page=True)
    finally:
        context.close()
