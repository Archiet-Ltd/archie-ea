"""The health scorecard's risk and review figures are the organisation's own records.

An enterprise architect opens the Architecture Health Scorecard in an organisation with
a critical, a high and a low open risk and two reviews (one submitted, one approved).
Each risk level and each pipeline count reads the records, the Open Risks card is their
sum, and the link under the review pipeline lands on the decision register. A second,
empty organisation reads measured zeros, not another organisation's figures.
"""
import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

HEALTH_PATH = "/dashboard/health"


def _seed(org, risks, reviews):
    from app import create_app, db
    from app.models.architecture_review_board import ARBReviewItem
    from app.models.risk import Risk

    with create_app("testing").app_context():
        for title, likelihood, impact in risks:
            db.session.add(Risk(
                title="%s %s" % (title, org["suffix"]), likelihood=likelihood, impact=impact,
                organization_id=org["org_id"]))
        for title, status in reviews:
            db.session.add(ARBReviewItem(
                organization_id=org["org_id"], review_number="REV-%s" % uuid.uuid4().hex[:10],
                title="%s %s" % (title, org["suffix"]), review_type="architecture_change", status=status,
                submitter_id=org["user_ids"]["enterprise_architect"]))
        db.session.commit()
        db.session.remove()


def _cell(page, container, label):
    """The figure printed under a label inside one of the scorecard's count grids."""
    box = page.locator('[data-testid="%s"]' % container)
    return box.locator("div", has_text=label).last.locator("p").nth(1).inner_text().strip()


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
def test_risk_and_review_counts_are_the_records(browser, live_server, viewport):
    mine = create_fresh_org("enterprise_architect")
    theirs = create_fresh_org("enterprise_architect")
    _seed(mine, [("Data loss", 5, 5), ("Late supplier", 3, 3), ("Typo risk", 1, 1)],
          [("Move billing", "submitted"), ("Retire CRM", "approved")])

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    page = context.new_page()
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])
        page.goto(live_server + HEALTH_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("[data-testid='risk-by-impact']", timeout=PAGE_TIMEOUT)

        assert [_cell(page, "risk-by-impact", level) for level in ("critical", "high", "medium", "low")] == \
            ["1", "1", "0", "1"]
        assert _cell(page, "arb-pipeline-counts", "Pending") == "1"
        assert _cell(page, "arb-pipeline-counts", "Approved") == "1"
        open_risks = page.get_by_text("Open Risks").first.locator("xpath=ancestor::*[self::div][2]").inner_text()
        assert "3" in open_risks

        page.get_by_role("link", name="View ARB decisions").click()
        page.wait_for_load_state("domcontentloaded")
        assert "/arb/decisions" in page.url
        assert page.locator("main").inner_text().strip(), "the decision register opened empty"
    finally:
        context.close()

    other = browser.new_context(ignore_https_errors=True, viewport=viewport)
    p2 = other.new_page()
    try:
        sign_in(p2, live_server, theirs["emails"]["enterprise_architect"])
        p2.goto(live_server + HEALTH_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        p2.wait_for_selector("[data-testid='risk-by-impact']", timeout=PAGE_TIMEOUT)
        assert [_cell(p2, "risk-by-impact", level) for level in ("critical", "high", "medium", "low")] == \
            ["0", "0", "0", "0"]
        assert _cell(p2, "arb-pipeline-counts", "Pending") == "0"
        assert "Data loss" not in p2.content()
    finally:
        other.close()
