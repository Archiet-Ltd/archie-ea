"""The Approvals page tells a person what is true about each record, and offers Approve only where it acts.

A technical founder signs up (a workspace of one). Writes are paused, so a change waits; the page says it waits for
writes to resume (and links to how it was checked, which says the same). A record the loop does not settle, asked
for by the founder, carries no Approve button and says why; a colleague's record of the same kind does. After
writes resume a change with a recorded check says it is applied or declined without waiting for a person, and a
change with no review on record makes no such claim. At 390 wide the footer, with Reject open, stays inside its
card. The empty page never promises a second person.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_change_review_gaps_journey import (  # noqa: F401 - fixtures and helpers of the sibling journey
    VIEWS, _app_context, _context, _second_person, _shot, _sign_up, _status_of, _waiting_change, _workspace_of,
    _writes, deepseek_server, shots, stub,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _out_of_loop_record(org_id, user_id, summary):
    from app import db

    app = _app_context()
    with app.app_context():
        from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

        row = create_approval_record(
            organization_id=org_id, operation_type="agent_charter_change", entity_type="agent_registration",
            original_command="system: charter change", operation_payload={"a": uuid.uuid4().hex[:6]},
            summary=summary, user_id=user_id)
        db.session.commit()
        out = row.id
        db.session.remove()
        return out


def _pre_loop_record(org_id, user_id, application_name):
    """A change recorded before automatic review: no rubric version, no review event, never swept."""
    from app import db

    app = _app_context()
    with app.app_context():
        from app.modules.ai_chat.services.ai_chat_approval_service import create_approval_record

        row = create_approval_record(
            organization_id=org_id, operation_type="tool_use", entity_type="update_application_status",
            original_command="old request",
            operation_payload={"application_name": application_name, "new_status": "production",
                               "rationale": "Asked for before automatic review"},
            summary="Old change to %s" % application_name, user_id=user_id)
        db.session.commit()
        out = row.id
        db.session.remove()
        return out


def _record_checks(approval_id):
    """Give a waiting change the checks the server records first, as a change that has been looked at once has."""
    from app import db

    app = _app_context()
    with app.app_context():
        from app.models.ai_chat_crud_approval import AIChatCRUDApproval

        row = AIChatCRUDApproval.query.filter_by(id=approval_id).one()
        row.review_reasons = {"checks": [{"name": "Target exists", "passed": True, "ran": True,
                                          "reason": "The application was found in this workspace."}],
                              "rounds": []}
        db.session.commit()
        db.session.remove()


def _inside(box, outer):
    return box["x"] >= outer["x"] - 1 and box["x"] + box["width"] <= outer["x"] + outer["width"] + 1


@pytest.mark.parametrize("width,theme", VIEWS)
def test_the_approvals_page_says_what_happens_to_each_record(browser, deepseek_server, stub, shots, width, theme):
    base = deepseek_server
    email = "founder.%s@example.com" % uuid.uuid4().hex[:8]
    name = "Atlas %s" % uuid.uuid4().hex[:6]
    checked_name = "Borealis %s" % uuid.uuid4().hex[:6]
    old_name = "Cirrus %s" % uuid.uuid4().hex[:6]
    context = _context(browser, width, theme)
    page = context.new_page()
    try:
        _sign_up(page, base, email)
        ws = _workspace_of(email, [name, checked_name, old_name])

        page.goto(base + "/ai-chat/approvals/inbox", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        body = page.locator("main").inner_text()
        assert "checked and reviewed automatically" in body, body
        assert "second person" not in body, body
        _shot(page, shots, "approvals-empty", theme)

        _writes(ws["org_id"], paused=True)
        older_id = _waiting_change(ws["org_id"], ws["user_id"], name)
        checked_id = _waiting_change(ws["org_id"], ws["user_id"], checked_name)
        _record_checks(checked_id)
        colleague = _second_person(ws["org_id"], switched_off=False)
        own_id = _out_of_loop_record(ws["org_id"], ws["user_id"], "Charter change asked for by the founder")
        theirs_id = _out_of_loop_record(ws["org_id"], colleague, "Charter change asked for by a colleague")
        pre_loop_id = _pre_loop_record(ws["org_id"], colleague, old_name)

        page.goto(base + "/ai-chat/approvals/inbox", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # While writes are paused every change in the loop waits, and says so; none claims a settlement and
        # Approve is not offered anywhere, because it would apply nothing.
        for approval_id in (older_id, checked_id):
            card = page.get_by_test_id("approval-card-%d" % approval_id)
            expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
            expect(card.get_by_test_id("approval-held-%d" % approval_id)).to_contain_text("paused for this workspace")
            expect(card.get_by_test_id("approval-held-%d" % approval_id)).to_contain_text("once they resume")
            expect(card.get_by_test_id("approval-in-review-%d" % approval_id)).to_have_count(0)
            expect(card.get_by_role("button", name="Approve")).to_have_count(0)
        # Only the change with a check on record offers the link to how it was checked.
        expect(page.get_by_test_id("approval-card-%d" % checked_id).get_by_role("link", name="See how it was checked")).to_have_count(1)
        expect(page.get_by_test_id("approval-card-%d" % older_id).get_by_role("link", name="See how it was checked")).to_have_count(0)
        # A row recorded before automatic review says nothing has checked it; it does not promise a check or
        # a review, and it has no link to a check that was never made.
        pre = page.get_by_test_id("approval-card-%d" % pre_loop_id)
        expect(pre).to_be_visible()
        expect(pre.get_by_test_id("approval-pre-loop-%d" % pre_loop_id)).to_contain_text("recorded before changes by the assistant were checked")
        expect(pre.get_by_test_id("approval-pre-loop-%d" % pre_loop_id)).to_contain_text("nothing has checked it")
        expect(pre.get_by_test_id("approval-held-%d" % pre_loop_id)).to_have_count(0)
        expect(pre.get_by_role("link", name="See how it was checked")).to_have_count(0)
        expect(pre.get_by_role("button", name="Approve")).to_have_count(0)
        _shot(page, shots, "approvals-pre-loop-paused", theme)
        own = page.get_by_test_id("approval-card-%d" % own_id)
        expect(own).to_be_visible()
        expect(own.get_by_role("button", name="Approve")).to_have_count(0)
        expect(own.get_by_test_id("approval-own-request-%d" % own_id)).to_contain_text("paused for this workspace")
        theirs = page.get_by_test_id("approval-card-%d" % theirs_id)
        expect(theirs.get_by_role("button", name="Approve")).to_have_count(0)
        _shot(page, shots, "approvals-held-and-own", theme)

        # The link of a held change lands on its record, with content: what it waits for and its checks.
        checked = page.get_by_test_id("approval-card-%d" % checked_id)
        checked.get_by_role("link", name="See how it was checked").click()
        page.wait_for_url(re.compile(r"approval=%d" % checked_id), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("In review")
        expect(page.get_by_test_id("review-record-waiting")).to_contain_text("paused for this workspace")
        expect(page.get_by_test_id("review-record-checks")).to_contain_text("The application was found in this workspace")
        _shot(page, shots, "held-record", theme)
        assert _status_of(ws["apps"][name]) == "development"
        assert _status_of(ws["apps"][checked_name]) == "development"

        # Writes resume. The change with a recorded check now says it is settled without a person, and its link
        # lands on those checks; the row with no review on record makes no such claim and is Approve-able.
        _writes(ws["org_id"], paused=False)
        page.goto(base + "/ai-chat/approvals/inbox", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        checked = page.get_by_test_id("approval-card-%d" % checked_id)
        expect(checked.get_by_test_id("approval-in-review-%d" % checked_id)).to_contain_text(
            "applied or declined without waiting")
        # Outside a pause the person's own record has no Approve and says another person approves it; a
        # colleague's record of the same kind has Approve.
        own = page.get_by_test_id("approval-card-%d" % own_id)
        expect(own).to_be_visible()
        expect(own.get_by_role("button", name="Approve")).to_have_count(0)
        expect(own.get_by_test_id("approval-own-request-%d" % own_id)).to_contain_text("another person")
        theirs = page.get_by_test_id("approval-card-%d" % theirs_id)
        expect(theirs.get_by_role("button", name="Approve")).to_have_count(1)
        _shot(page, shots, "approvals-own-and-colleague", theme)
        pre = page.get_by_test_id("approval-card-%d" % pre_loop_id)
        expect(pre.get_by_test_id("approval-pre-loop-%d" % pre_loop_id)).to_contain_text("nothing has checked it")
        expect(pre.get_by_test_id("approval-in-review-%d" % pre_loop_id)).to_have_count(0)
        older = page.get_by_test_id("approval-card-%d" % older_id)
        expect(older.get_by_test_id("approval-in-review-%d" % older_id)).to_have_count(0)
        expect(older.get_by_test_id("approval-held-%d" % older_id)).to_have_count(0)
        expect(older.get_by_role("button", name="Approve")).to_have_count(1)
        card_box = older.bounding_box()
        for button in (older.get_by_role("button", name="Approve"), older.locator("summary", has_text="Reject")):
            assert _inside(button.bounding_box(), card_box), "a footer control is clipped by its card"
        older.locator("summary", has_text="Reject").click()
        expect(older.get_by_role("button", name="Confirm Reject")).to_be_visible()
        card_box = older.bounding_box()
        assert _inside(older.get_by_role("button", name="Confirm Reject").bounding_box(), card_box)
        assert _inside(older.get_by_label("Why are you rejecting this?").bounding_box(), card_box)
        _shot(page, shots, "approvals-reject-open", theme)
        checked.get_by_role("link", name="See how it was checked").click()
        page.wait_for_url(re.compile(r"approval=%d" % checked_id), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("review-record-checks")).to_contain_text("The application was found in this workspace")
        # After resume the record no longer says writes are paused, whatever the last sweep recorded.
        expect(page.get_by_test_id("review-record-waiting")).to_have_count(0)
        assert "paused" not in page.get_by_test_id("review-record-verdict").locator("xpath=ancestor::*[1]").inner_text()
        _shot(page, shots, "resumed-record", theme)

        # The person's Approve goes through the review and the change is applied once.
        page.goto(base + "/ai-chat/approvals/inbox?approval=%d" % older_id, wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        page.get_by_test_id("approval-card-%d" % older_id).get_by_role("button", name="Approve").click()
        page.wait_for_load_state("domcontentloaded")
        assert _status_of(ws["apps"][name]) == "production"
        page.goto(base + "/ai-chat/approvals/inbox", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("approval-record-link-%d" % older_id)).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, shots, "approvals-after-resume", theme)
    finally:
        context.close()
