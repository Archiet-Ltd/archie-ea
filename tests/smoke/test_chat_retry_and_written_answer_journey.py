"""A failed chat turn tells what it already did, Retry leaves it alone, and a written answer shows its code as written.

The person: the administrator of an organisation (and a member of it) who asks the assistant something.
Their goal: when a turn stops, read plainly what happened and press Retry without anything being done twice;
when the assistant is not set up, read that and (as an administrator) get to the provider settings; and
read a written answer whose code block keeps its indentation and backticks. The browser is real; only the
assistant's replies are supplied, so each case is exact.
"""

import json
import os
import re
import time

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import open_chat
from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.intelligence_graph import seed_impact_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("CHAT_WAIT_SHOTS")
NOT_SET_UP = "The assistant is not set up for your organisation yet."
BUSY = "The assistant is busy right now. Wait a minute, then press Retry."
ANSWER = "The goal Billing is recorded."
NO_MATCH = '{"found": false}'


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


def _stream(page, handler):
    page.route("**/ai-chat/message/stream", handler)


def _ask(page, text):
    page.fill("#user-input", text)
    page.press("#user-input", "Enter")


def test_a_turn_that_stopped_after_making_a_change_says_so_and_retry_does_not_send_it_to_be_made_again(page, live_server):
    org = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    sign_in(page, live_server, org["emails"]["platform_admin"])
    change = {"tool": "create_goal", "arguments": {"name": "Billing"}, "result": {"id": 7},
              "message": "Created the goal Billing.", "mutates": True}
    sent = []

    def respond(route):
        body = json.loads(route.request.post_data)
        sent.append(body)
        if len(sent) == 1:
            events = _events({"type": "done", "response": BUSY, "error": "stopped", "actions_taken": [change],
                              "pending_approvals": [], "sources": [], "turn_id": "turn-one"})
        else:
            events = _events({"type": "token", "text": ANSWER}, {"type": "done", "response": ANSWER, "sources": [],
                              "actions_taken": [], "pending_approvals": []})
        route.fulfill(status=200, content_type="text/event-stream", body=events)

    page.route("**/ai-chat/estate-answer", lambda r: r.fulfill(status=200, content_type="application/json", body=NO_MATCH))
    _stream(page, respond)
    open_chat(page, live_server)
    _ask(page, "Create a goal called Billing.")
    card = page.locator('#messages-container [role="alert"]').last
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
    text = card.inner_text()
    assert BUSY in text, text
    assert "Created the goal Billing." in text and "not repeated on Retry" in text, text
    # A first try is not a Retry and names no earlier turn; the browser never sends a list of changes.
    assert sent[0]["retry"] is False and sent[0]["failed_turn_id"] is None and "completed_actions" not in sent[0], sent[0]
    shoot(page, "retry-after-a-change")
    card.get_by_role("button", name="Retry").click()
    expect(page.locator("#messages-container .message-content", has_text=ANSWER).last).to_be_visible(timeout=PAGE_TIMEOUT)
    assert len(sent) == 2
    # Retry says only that it is one and which turn failed; what that turn changed is read by the server.
    assert sent[1]["retry"] is True and sent[1]["failed_turn_id"] == "turn-one", sent[1]
    assert "completed_actions" not in sent[1], sent[1]


def test_a_dropped_stream_hands_the_plain_request_the_time_already_spent(page, live_server):
    org = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    sign_in(page, live_server, org["emails"]["platform_admin"])
    seen = {}

    def slow_lookup(route):
        time.sleep(3)
        route.fulfill(status=200, content_type="application/json", body=NO_MATCH)

    def dropped_stream(route):
        seen["stream"] = json.loads(route.request.post_data)["waited_seconds"]
        time.sleep(3)
        route.abort()

    def plain(route):
        seen["plain"] = json.loads(route.request.post_data)["waited_seconds"]
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "success": True, "response": ANSWER, "actions_taken": [], "pending_approvals": [], "sources": []}))

    page.route("**/ai-chat/estate-answer", slow_lookup)
    _stream(page, dropped_stream)
    page.route(re.compile(r".*/ai-chat/message$"), plain)
    open_chat(page, live_server)
    _ask(page, "Which applications have no owner?")
    expect(page.locator("#messages-container .message-content", has_text=ANSWER).last).to_be_visible(timeout=PAGE_TIMEOUT)
    # The lookup took about three seconds and the dropped stream three more: the plain request is told so.
    assert seen["stream"] >= 2.5, seen
    assert seen["plain"] >= seen["stream"] + 2.5, seen


@pytest.mark.parametrize("who", ["platform_admin", "enterprise_architect"])
def test_when_the_assistant_is_not_set_up_a_member_reads_it_and_only_an_administrator_gets_the_settings_link(
    page, live_server, who
):
    org = create_fresh_org("platform_admin", org_admin=True, extra_roles=("enterprise_architect",), platform_role="Administrator")
    sign_in(page, live_server, org["emails"][who])
    events = _events({"type": "done", "response": NOT_SET_UP, "error": "no api key", "not_set_up": True,
                      "actions_taken": [], "pending_approvals": [], "sources": []})
    page.route("**/ai-chat/estate-answer", lambda r: r.fulfill(status=200, content_type="application/json", body=NO_MATCH))
    _stream(page, lambda r: r.fulfill(status=200, content_type="text/event-stream", body=events))
    open_chat(page, live_server)
    _ask(page, "Which applications have no owner?")
    card = page.locator('#messages-container [role="alert"]').last
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
    text = card.inner_text()
    assert NOT_SET_UP in text, text
    assert "Settings" not in text.replace("Check the AI provider settings", "") and "API" not in text, text
    assert card.get_by_role("button", name="Retry").count() == 0, text
    link = card.get_by_role("link", name="Check the AI provider settings")
    shoot(page, "not-set-up-" + who)
    if who == "platform_admin":
        expect(link).to_be_visible()
        link.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/admin/api-settings" in page.url
        assert len(page.locator("main").inner_text().strip()) > 40
    else:
        assert link.count() == 0


def test_a_written_answer_shows_a_fenced_code_block_exactly_as_written(page, live_server):
    org = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    graph = seed_impact_graph(org["org_id"])
    sign_in(page, live_server, org["emails"]["platform_admin"])
    text = ("The service runs from `docker-compose.yml`, and its record is [ref:%s].\n\n"
            "```sh\n  indented `date`\n```\n" % graph["service"])
    events = _events({"type": "token", "text": text},
                     {"type": "done", "response": text, "sources": [], "actions_taken": [], "pending_approvals": []})
    page.route("**/ai-chat/estate-answer", lambda r: r.fulfill(status=200, content_type="application/json", body=NO_MATCH))
    _stream(page, lambda r: r.fulfill(status=200, content_type="text/event-stream", body=events))
    open_chat(page, live_server)
    _ask(page, "What does the service depend on?")
    message = page.locator("#messages-container .message-content").last
    expect(message).to_contain_text("docker-compose.yml", timeout=PAGE_TIMEOUT)
    # The block is the code as written: its indentation and its final backtick are kept.
    assert message.locator("pre code").inner_text().rstrip("\n") == "  indented `date`"
    # A name written between backticks in the sentence is still drawn without them.
    assert message.locator("p code").first.inner_text() == "docker-compose.yml"
    shoot(page, "written-answer-code")
    link = message.locator("a").first
    href = link.get_attribute("href")
    assert href and href.startswith("/intelligence/answers/"), href
    page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert len(page.locator("main").inner_text().strip()) > 40
