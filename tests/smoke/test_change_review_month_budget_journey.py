"""A change waits, with the reason on its record, when the month's AI budget has no room for a review.

A technical founder signs up (a workspace of one) and asks the assistant to move an application to
production. DeepSeek is the only provider, stubbed on a loopback port, with a monthly ceiling too small
for one review. The chat says the change is in review because the month's AI budget is used up; the
record says the same; no review was sent to the model; the application is unchanged. The change is still
there on the approvals list, waiting, not declined.

Before this change the review was sent whenever the month's spend was under the ceiling, however little
room was left.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_change_review_gaps_journey import (  # noqa: F401 - fixtures and helpers of the sibling journey
    OTHERS, VIEWS, _ask, _context, _reviews, _shot, _sign_up, _status_args, _status_of, _workspace_of, shots, stub,
)
from tests.deepseek_stub import KEY

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def deepseek_server(request, app, stub):
    env = {name + "_API_KEY": "" for name in OTHERS}
    env.update(
        LLM_PROVIDER_ORDER="deepseek", DEEPSEEK_API_KEY=KEY, DEEPSEEK_API_BASE=stub.base_url,
        # Room for the chat's own small calls, none for a review that may use a full reply.
        LLM_SPEND_CEILING_USD_DEEPSEEK="0.01", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
    )
    return boot_live_server(request, None, app, extra_env=env)


@pytest.mark.parametrize("width,theme", VIEWS)
def test_a_change_waits_with_the_reason_shown_when_the_month_has_no_room_for_its_review(
    browser, deepseek_server, stub, shots, width, theme
):
    base = deepseek_server
    email = "founder.%s@example.com" % uuid.uuid4().hex[:8]
    name = "Atlas %s" % uuid.uuid4().hex[:6]
    context = _context(browser, width, theme)
    page = context.new_page()
    try:
        _sign_up(page, base, email)
        ws = _workspace_of(email, [name])
        reviews_before = len(_reviews(stub))
        verdicts = _ask(page, base, stub, _status_args(name), "Move %s to production." % name)
        text = verdicts.inner_text()
        assert "In review:" in text and "month's AI budget is used up" in text, text
        assert "Declined" not in text and "Applied:" not in text, text
        _shot(page, shots, "budget-wait-chat", theme)

        page.get_by_test_id("change-record-link").last.click()
        page.wait_for_url(re.compile(r"/ai-chat/approvals/inbox\?approval=\d+"), timeout=PAGE_TIMEOUT)
        approval_id = int(re.search(r"approval=(\d+)", page.url).group(1))
        expect(page.get_by_test_id("review-record-verdict")).to_have_text("In review")
        expect(page.get_by_test_id("review-record-waiting")).to_contain_text("month's AI budget is used up")
        expect(page.get_by_test_id("review-record")).to_contain_text(name)
        _shot(page, shots, "budget-wait-record", theme)

        # Nothing was sent to the paid model and nothing changed.
        assert len(_reviews(stub)) == reviews_before
        assert _status_of(ws["apps"][name]) == "development"

        # The change is still on the approvals list, waiting, and its link opens the same record.
        page.goto(base + "/ai-chat/approvals/inbox", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        card = page.get_by_test_id("approval-card-%d" % approval_id)
        expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(card).to_contain_text(name)
        _shot(page, shots, "budget-wait-list", theme)
    finally:
        context.close()
