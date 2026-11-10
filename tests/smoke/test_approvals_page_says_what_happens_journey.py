"""The Approvals page tells a person what is true about each record, and offers Approve only where it acts.

A technical founder signs up (a workspace of one). Writes are paused, so a change waits; the page says the loop
reviews it automatically and links to how it was checked. A record the loop does not settle, asked for by the
founder, carries no Approve button and says why; a colleague's record of the same kind does. After writes resume
the held change is settled and the page lists it as decided. The empty page never promises a second person.
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


@pytest.mark.parametrize("width,theme", VIEWS)
def test_the_approvals_page_says_what_happens_to_each_record(browser, deepseek_server, stub, shots, width, theme):
    base = deepseek_server
    email = "founder.%s@example.com" % uuid.uuid4().hex[:8]
    name = "Atlas %s" % uuid.uuid4().hex[:6]
    context = _context(browser, width, theme)
    page = context.new_page()
    try:
        _sign_up(page, base, email)
        ws = _workspace_of(email, [name])

        page.goto(base + "/ai-chat/approvals/inbox", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        body = page.locator("main").inner_text()
        assert "checked and reviewed automatically" in body, body
        assert "second person" not in body, body
        _shot(page, shots, "approvals-empty", theme)

        _writes(ws["org_id"], paused=True)
        held_id = _waiting_change(ws["org_id"], ws["user_id"], name)
        colleague = _second_person(ws["org_id"], switched_off=False)
        own_id = _out_of_loop_record(ws["org_id"], ws["user_id"], "Charter change asked for by the founder")
        theirs_id = _out_of_loop_record(ws["org_id"], colleague, "Charter change asked for by a colleague")

        page.goto(base + "/ai-chat/approvals/inbox", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        held = page.get_by_test_id("approval-card-%d" % held_id)
        expect(held).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(held.get_by_test_id("approval-in-review-%d" % held_id)).to_contain_text("applied or declined without waiting")
        own = page.get_by_test_id("approval-card-%d" % own_id)
        expect(own).to_be_visible()
        expect(own.get_by_role("button", name="Approve")).to_have_count(0)
        expect(own.get_by_test_id("approval-own-request-%d" % own_id)).to_contain_text("another person")
        theirs = page.get_by_test_id("approval-card-%d" % theirs_id)
        expect(theirs.get_by_role("button", name="Approve")).to_have_count(1)
        _shot(page, shots, "approvals-held-and-own", theme)

        # The link lands on the record of that held change, with content.
        held.get_by_role("link", name="See how it was checked").click()
        page.wait_for_url(re.compile(r"approval=%d" % held_id), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("In review")
        _shot(page, shots, "held-record", theme)
        assert _status_of(ws["apps"][name]) == "development"

        # Writes resume: the held change is settled in the loop (the person's Approve goes through review).
        _writes(ws["org_id"], paused=False)
        page.goto(base + "/ai-chat/approvals/inbox?approval=%d" % held_id, wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        page.get_by_test_id("approval-card-%d" % held_id).get_by_role("button", name="Approve").click()
        page.wait_for_load_state("domcontentloaded")
        assert _status_of(ws["apps"][name]) == "production"
        page.goto(base + "/ai-chat/approvals/inbox", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("approval-record-link-%d" % held_id)).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, shots, "approvals-after-resume", theme)
    finally:
        context.close()
