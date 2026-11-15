"""A question is never dropped because the records lookup failed, a failure names its reason, and a reopened
chat that ended in a failure can be tried again.

The person: an administrator of an organisation who asks the assistant to record something. Their goal: get
an answer; when none comes, read plainly why and press Retry, in the chat they are in and in one they reopen
from Recent chats. The browser is real; only the assistant's replies and the broken lookup are supplied.
"""

import json
import os

import pytest
from playwright.sync_api import expect

from app.modules.ai_chat.services.failure_messages import ASSISTANT_UNAVAILABLE, FAILURE_REASONS
from tests.smoke.chat_ask import open_chat
from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("CHAT_FAILURE_SHOTS")
QUESTION = "Create a new Assessment element named Chaterr check"
ANSWER = "The Assessment Chaterr check is recorded."


def shoot(page, name):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height, theme in ((1440, 900, "light"), (390, 844, "dark")):
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("(t) => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        page.wait_for_timeout(400)
        page.screenshot(path=os.path.join(SHOTS, "%s-%d-%s.png" % (name, width, theme)), full_page=True)
    page.evaluate("() => document.documentElement.classList.remove('dark')")
    page.set_viewport_size({"width": 1440, "height": 900})


def _events(*events):
    return "".join("data: %s\n\n" % json.dumps(e) for e in events)


def _answer_events():
    return _events({"type": "token", "text": ANSWER},
                   {"type": "done", "response": ANSWER, "sources": [], "actions_taken": [], "pending_approvals": []})


def _ask(page, text):
    page.fill("#user-input", text)
    page.press("#user-input", "Enter")


def _admin(page, live_server):
    org = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    sign_in(page, live_server, org["emails"]["platform_admin"])
    return org


def test_a_question_goes_on_to_the_ai_when_the_records_lookup_fails(page, live_server):
    _admin(page, live_server)
    asked = []

    def stream(route):
        asked.append(json.loads(route.request.post_data)["message"])
        route.fulfill(status=200, content_type="text/event-stream", body=_answer_events())

    page.route("**/ai-chat/estate-answer", lambda r: r.fulfill(
        status=500, content_type="application/json", body=json.dumps({"success": False, "error": "lookup broke"})))
    page.route("**/ai-chat/message/stream", stream)
    open_chat(page, live_server)
    _ask(page, QUESTION)
    reply = page.locator("#messages-container .message-content", has_text=ANSWER).last
    expect(reply).to_be_visible(timeout=PAGE_TIMEOUT)
    assert asked == [QUESTION], asked
    assert ASSISTANT_UNAVAILABLE not in page.locator("#messages-container").inner_text()
    assert page.locator('#messages-container [role="alert"]').count() == 0
    shoot(page, "lookup-failed-question-answered")


def test_a_timeout_names_its_reason_and_retry_asks_the_question_again(page, live_server):
    _admin(page, live_server)
    asked = []

    def stream(route):
        asked.append(json.loads(route.request.post_data))
        if len(asked) == 1:
            body = _events({"type": "done", "response": ASSISTANT_UNAVAILABLE, "error": "provider timed out",
                            "reason_code": "timeout", "retryable": True, "actions_taken": [],
                            "pending_approvals": [], "sources": []})
        else:
            body = _answer_events()
        route.fulfill(status=200, content_type="text/event-stream", body=body)

    page.route("**/ai-chat/estate-answer", lambda r: r.fulfill(
        status=200, content_type="application/json", body='{"found": false}'))
    page.route("**/ai-chat/message/stream", stream)
    open_chat(page, live_server)
    _ask(page, QUESTION)
    card = page.locator('#messages-container [role="alert"]').last
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
    text = card.inner_text()
    assert "did not answer in time" in text, text
    assert "try again in a moment" not in text, text
    shoot(page, "timeout-reason")
    card.get_by_role("button", name="Retry").click()
    expect(page.locator("#messages-container .message-content", has_text=ANSWER).last).to_be_visible(timeout=PAGE_TIMEOUT)
    assert [a["message"] for a in asked] == [QUESTION, QUESTION]
    assert asked[1]["retry"] is True


@pytest.mark.parametrize("status,code", [(401, "signed_out"), (403, "role_not_allowed"), (413, "too_large")])
def test_a_refused_request_names_what_is_wrong_in_plain_words(page, live_server, status, code):
    _admin(page, live_server)
    page.route("**/ai-chat/estate-answer", lambda r: r.fulfill(
        status=200, content_type="application/json", body='{"found": false}'))
    page.route("**/ai-chat/message/stream", lambda r: r.fulfill(status=status, content_type="application/json", body="{}"))
    page.route("**/ai-chat/message", lambda r: r.fulfill(status=status, content_type="application/json", body="{}"))
    open_chat(page, live_server)
    _ask(page, QUESTION)
    card = page.locator('#messages-container [role="alert"]').last
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
    assert FAILURE_REASONS[code] in card.inner_text(), card.inner_text()
    # A role that is not allowed cannot be helped by asking again; the others can.
    assert card.get_by_role("button", name="Retry").count() == (0 if status in (401, 403) else 1)


def test_a_chat_reopened_from_recent_chats_that_ended_in_a_failure_has_a_working_retry(page, live_server):
    org = _admin(page, live_server)
    from app import create_app
    from app.models.user import User
    from app.services.conversation_history import get_history_service

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=org["emails"]["platform_admin"]).one()
        service = get_history_service()
        # The thread a turn that ended in a failure leaves behind, made in this person's organisation.
        thread_id = service.create_thread(user_id=user.id, title=QUESTION, model="unknown",
                                          initial_message=QUESTION, organization_id=org["org_id"]).id
        service.add_message(thread_id, "assistant", FAILURE_REASONS["timeout"], model="unknown")
    asked = []

    def stream(route):
        asked.append(json.loads(route.request.post_data))
        route.fulfill(status=200, content_type="text/event-stream", body=_answer_events())

    page.route("**/ai-chat/estate-answer", lambda r: r.fulfill(
        status=200, content_type="application/json", body='{"found": false}'))
    page.route("**/ai-chat/message/stream", stream)
    try:
        open_chat(page, live_server)
        page.locator('[data-thread-id="%s"] .js-thr-open' % thread_id).click()
        card = page.locator('#messages-container [role="alert"]').last
        expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "did not answer in time" in card.inner_text()
        # The question that was asked is still in the conversation, above its failure.
        assert QUESTION in page.locator("#messages-container").inner_text()
        shoot(page, "reopened-failed-chat")
        card.get_by_role("button", name="Retry").click()
        expect(page.locator("#messages-container .message-content", has_text=ANSWER).last).to_be_visible(timeout=PAGE_TIMEOUT)
        assert len(asked) == 1 and asked[0]["message"] == QUESTION and asked[0]["retry"] is True, asked
        assert asked[0]["thread_id"] == thread_id
    finally:
        with app.app_context():
            get_history_service().delete_thread(thread_id)
