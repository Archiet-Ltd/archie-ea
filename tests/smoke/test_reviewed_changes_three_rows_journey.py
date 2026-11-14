"""Reviewed changes with one applied, one declined and one in review change: a person opens the page from the
sidebar, sees all three with their reasons and the badge counting the one in review, follows the links to the
records, finds no Approve or Reject, and sees the badge go once the review has decided the last one.

Checked at 1440 and 390 wide. The assistant's models are stubbed on a loopback port: one drafts, one reviews, and
the reviewer fails any change to an application called "Old Ledger".
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_change_review_gaps_journey import (  # noqa: F401 - fixtures and helpers of the sibling journey
    REVIEWER_REASON,
    _ask as _ask_for_status,
    _context,
    _no_decision_control,
    _shot as _shot_themed,
    _sign_up,
    _status_of,
    _the_review_runs,
    _workspace_of,
    _writes,
    deepseek_server,
    shots,
    stub,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _badge(page):
    badges = page.locator('[data-testid="nav-count-approvals"]')
    return badges.first.text_content().strip() if badges.count() else None


@pytest.mark.parametrize("width", [1440, 390])
def test_reviewed_changes_lists_an_applied_a_declined_and_an_in_review_change_and_the_badge_follows(
    browser, deepseek_server, stub, shots, width
):
    base = deepseek_server
    email = "founder.%s@example.com" % uuid.uuid4().hex[:8]
    applied_name = "Atlas %s" % uuid.uuid4().hex[:6]
    declined_name = "Old Ledger %s" % uuid.uuid4().hex[:6]
    waiting_name = "Cirrus %s" % uuid.uuid4().hex[:6]
    context = _context(browser, width, "light")
    page = context.new_page()
    try:
        _sign_up(page, base, email)
        ws = _workspace_of(email, [applied_name, declined_name, waiting_name])

        def args(name):
            return {"application_name": name, "new_status": "production", "rationale": "It is live for customers now."}

        _ask_for_status(page, base, stub, args(applied_name), "Move %s to production, it is live now." % applied_name)
        _ask_for_status(page, base, stub, args(declined_name), "Move %s to production." % declined_name)
        _writes(ws["org_id"], paused=True)
        _ask_for_status(page, base, stub, args(waiting_name), "Move %s to production." % waiting_name)

        # The sidebar item opens the page; its badge counts the one change in review.
        page.goto(base + "/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert _badge(page) == "1", _badge(page)
        if width < 768:
            page.get_by_test_id("sidebar-trigger").first.click()
        page.get_by_role("link", name=re.compile(r"^Reviewed changes")).first.click()
        page.wait_for_url(re.compile(r"/ai-chat/approvals/inbox"), timeout=PAGE_TIMEOUT)

        waiting = page.get_by_test_id("approval-queue")
        expect(waiting).to_contain_text(waiting_name, timeout=PAGE_TIMEOUT)
        expect(waiting).to_contain_text("In review for")
        expect(waiting).to_contain_text("Drafted by")
        expect(waiting).to_contain_text("paused for this workspace")
        decided = page.get_by_test_id("approval-decided")
        expect(decided).to_contain_text(applied_name)
        expect(decided).to_contain_text("Applied")
        expect(decided).to_contain_text(declined_name)
        expect(decided).to_contain_text("Declined")
        expect(decided).to_contain_text(REVIEWER_REASON)
        expect(decided).to_contain_text("Drafted by")
        _no_decision_control(page)
        _shot_themed(page, shots, "reviewed-changes-three", "light")

        # The declined row's link lands on its record with the reason; the applied row's on its record.
        declined_row = page.get_by_test_id("approval-decided-row").filter(has_text=declined_name)
        declined_row.get_by_role("link", name="See how it was checked").click()
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("Declined", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("review-record-reasons")).to_contain_text(REVIEWER_REASON)
        assert _status_of(ws["apps"][declined_name]) == "development"
        page.go_back()
        applied_row = page.get_by_test_id("approval-decided-row").filter(has_text=applied_name)
        applied_row.get_by_role("link", name="See how it was checked").click()
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("Applied", timeout=PAGE_TIMEOUT)
        assert _status_of(ws["apps"][applied_name]) == "production"

        # Writes resume and the review runs: the change is decided and the badge goes away.
        card_id = int(page.get_by_test_id("approval-queue").locator('[data-testid^="approval-card-"]').first
                      .get_attribute("data-testid").rsplit("-", 1)[1])
        _writes(ws["org_id"], paused=False)
        _the_review_runs(ws["org_id"], card_id, stub)
        page.goto(base + "/ai-chat/approvals/inbox", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert _badge(page) is None, _badge(page)
        expect(page.get_by_test_id("approval-queue")).to_have_count(0)
        expect(page.get_by_test_id("approval-decided")).to_contain_text(waiting_name)
        assert _status_of(ws["apps"][waiting_name]) == "production"
        _shot_themed(page, shots, "reviewed-changes-after-review", "light")
    finally:
        context.close()
