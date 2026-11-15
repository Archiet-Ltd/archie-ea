"""A cited answer never shows a raw reference marker, and each record is named once, as a link.

The person: an enterprise architect who asks which parts of the estate a service touches. Their goal: read
the answer and open the records it names. The model's text names two records the ways it really does: the
record's name followed by a marker, a marker with a space after the colon, and two ids in one marker, plus a
marker whose id reaches no record. The check is the rendered answer while it arrives, when it is finished,
and when the conversation is opened again from history.
"""

import json
import os
import re

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import open_chat
from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.intelligence_graph import seed_impact_graph

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("CHAT_REF_SHOTS")


def shoot(page, name):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height, theme in ((1440, 900, "light"), (390, 844, "dark")):
        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("(t) => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        page.screenshot(path=os.path.join(SHOTS, "%s-%d-%s.png" % (name, width, theme)), full_page=True)
    page.evaluate("() => document.documentElement.classList.remove('dark')")
    page.set_viewport_size({"width": 1440, "height": 900})


def _answer(graph):
    service, gateway = graph["names"]["service"], graph["names"]["gateway"]
    text = (
        "**%s** [ref:%s] is served through %s [ref: %s].\n\n"
        "Both are recorded together [ref:%s, %s].\n\n"
        "**The %s** [ref:%s] was named in bold with more words.\n\n"
        "A third thing was mentioned [ref:business:cap:424242]." % (service, graph["service"], gateway, graph["gateway"], graph["service"], graph["gateway"], service, graph["service"])
    )
    sources = [
        {"id": str(graph["service"]), "name": service, "url": "/intelligence/answers/element/%s" % graph["service"]},
        {"id": str(graph["gateway"]), "name": gateway, "url": "/intelligence/answers/element/%s" % graph["gateway"]},
    ]
    return text, sources


def _check(message, graph):
    shown = message.inner_text()
    assert "[ref" not in shown and "ref:" not in shown, shown
    # Bold around a name and more words never leaves one mark of the pair on screen.
    assert "**" not in shown, shown
    for key in ("service", "gateway"):
        name = graph["names"][key]
        links = message.locator("a", has_text=name)
        assert links.count() >= 1, shown
        # The name is the link: it is never written once as text and again as a link.
        assert not re.search(re.escape(name) + r"\s*\**\s*" + re.escape(name), shown), shown
    first = shown.split("\n")[0]
    assert first.count(graph["names"]["service"]) == 1, first
    hrefs = [message.locator("a").nth(i).get_attribute("href") for i in range(message.locator("a").count())]
    for key in ("service", "gateway"):
        assert "/intelligence/answers/element/%s" % graph[key] in hrefs, hrefs


def _open_each_link_lands_on_content(page, message, graph):
    """Click every reference link the answer shows, one after the other, and read the page each opens."""
    names = {"/intelligence/answers/element/%s" % graph[key]: graph["names"][key] for key in ("service", "gateway")}
    shown = message.locator("a[href^='/intelligence/answers/']").count()
    assert shown >= 2, shown
    chat_url = page.url
    clicked = 0
    for index in range(shown):
        link = message.locator("a[href^='/intelligence/answers/']").nth(index)
        name = names[link.get_attribute("href")]
        link.click()
        page.wait_for_url(re.compile(r".*/intelligence/answers/[0-9a-f]{32}"), timeout=PAGE_TIMEOUT)
        body = page.locator("#main-content").inner_text()
        assert len(body.strip()) > 80 and name in body, body[:200]
        clicked += 1
        page.goto(chat_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.evaluate("() => window.loadSession('history-1')")
        message = page.locator("#messages-container .message-content").last
        expect(message).to_contain_text("A third thing was mentioned", timeout=PAGE_TIMEOUT)
    assert clicked == shown, (clicked, shown)


def test_a_cited_answer_names_each_record_once_as_a_link_streamed_and_from_history(page, live_server):
    org = create_fresh_org("enterprise_architect", org_admin=True, platform_role="Administrator")
    other = create_fresh_org("enterprise_architect", org_admin=True, platform_role="Administrator")
    graph = seed_impact_graph(org["org_id"])
    seed_impact_graph(other["org_id"])
    sign_in(page, live_server, org["emails"]["enterprise_architect"])
    text, sources = _answer(graph)
    # The answer arrives in pieces, so a marker is cut part-way through at least once.
    cut = text.index("[ref: ") + 4
    events = [{"type": "token", "text": text[:cut]}, {"type": "token", "text": text[cut:]},
              {"type": "done", "response": text, "sources": sources, "actions_taken": [], "pending_approvals": []}]
    stream = "".join("data: %s\n\n" % json.dumps(e) for e in events)
    page.route("**/ai-chat/estate-answer", lambda r: r.fulfill(status=200, content_type="application/json", body='{"found": false}'))
    page.route("**/ai-chat/message/stream", lambda r: r.fulfill(status=200, content_type="text/event-stream", body=stream))
    open_chat(page, live_server)
    page.fill("#user-input", "What does the service touch?")
    page.press("#user-input", "Enter")
    message = page.locator("#messages-container .message-content").last
    expect(message).to_contain_text("A third thing was mentioned", timeout=PAGE_TIMEOUT)
    _check(message, graph)
    shoot(page, "streamed")

    # The same conversation opened again from history: the stored text carries the markers, no sources.
    page.route(
        "**/ai-chat/threads/history-1",
        lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"messages": [{"role": "user", "content": "What does the service touch?"}, {"role": "assistant", "content": text}]})),
    )
    page.evaluate("() => window.loadSession('history-1')")
    reloaded = page.locator("#messages-container .message-content").last
    expect(reloaded).to_contain_text("A third thing was mentioned", timeout=PAGE_TIMEOUT)
    shown = reloaded.inner_text()
    assert "ref:" not in shown and "**" not in shown, shown
    assert reloaded.locator("a").count() >= 2
    # Each record is named once in the replayed answer: the text carries the name, the marker adds no second copy.
    lines = [line for line in shown.split("\n") if line.strip()]
    for name in (graph["names"]["service"], graph["names"]["gateway"]):
        assert lines[0].count(name) == 1, lines[0]
    hrefs = [reloaded.locator("a").nth(i).get_attribute("href") for i in range(reloaded.locator("a").count())]
    assert "/intelligence/answers/element/%s" % graph["service"] in hrefs, hrefs
    shoot(page, "history")
    _open_each_link_lands_on_content(page, reloaded, graph)
