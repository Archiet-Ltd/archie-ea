"""The last sign-up step connects the person's own AI chat to their workspace.

The person: a startup CTO who already uses an AI chat. The goal: add their workspace to that chat from
the last sign-up step, see the step say it is connected the moment the chat signs in (without a reload),
find the same connection on Connected assistants, and reach the built-in chat's settings from the same
step. A second organisation never sees the first one's connection. Set ONB_WALK_DIR to save frames.
"""
import base64
import hashlib
import os
import re
import time
import urllib.parse

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_signup_brings_the_company_in_journey import (  # noqa: F401  (these are fixtures)
    people,
    provider,
    server,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("ONB_WALK_DIR")
CALLBACK = "http://localhost/callback"
VERIFIER = "v" * 64


def _shot(page, name):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]


def _no_accessibility_violations(page):
    from axe_playwright_python.sync_playwright import Axe

    report = Axe().run(page, options={"runOnly": {"type": "tag", "values": AXE_TAGS}})
    data = report.response if hasattr(report, "response") else report
    found = [(v["id"], [n["target"] for n in v["nodes"]][:3]) for v in data.get("violations", [])]
    assert not found, found


def _step_four(page, base):
    page.goto(base + "/account/welcome?step=4", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return page.get_by_test_id("signup-ai-chat")


def _register_client(app, name):
    from app.modules.oauth_provider.models import OAuthClient

    with app.app_context():
        return OAuthClient.register(client_name=name, redirect_uris=CALLBACK).client_id


def _sign_in_from_the_chat(page, base, client_id):
    """What Claude, ChatGPT or Cursor does once the address is added: the consent page, Allow, then the code is traded for a token."""
    challenge = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
    resource = base.rstrip("/") + "/mcp"
    query = urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": CALLBACK, "response_type": "code", "code_challenge": challenge,
        "code_challenge_method": "S256", "scope": "mcp:read", "resource": resource, "state": "xyz",
    })
    captured = []

    def _chat_receives(route):
        # The chat's own callback: answered here, so the exchange is read from a real intercepted request.
        captured.append(route.request.url)
        route.fulfill(status=200, content_type="text/html", body="<p>connected</p>")

    page.route(CALLBACK + "**", _chat_receives)
    page.goto(base + "/oauth/authorize?" + query, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name=re.compile("^Allow", re.I)).click()
    page.wait_for_timeout(1500)
    assert captured, "the consent page did not send the chat back with a code: %s %s" % (page.url, page.locator("body").inner_text()[:300])
    sent = urllib.parse.parse_qs(urllib.parse.urlsplit(captured[0]).query)
    assert sent["state"] == ["xyz"], sent
    code = sent["code"][0]
    token = page.request.post(base + "/oauth/token", form={
        "grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK, "client_id": client_id,
        "code_verifier": VERIFIER, "resource": resource,
    })
    assert token.ok, token.text()
    return token.json()["access_token"]


def _first_cited_answer(page, base, access_token):
    """The sample company is loaded from the chat's own offer; the connected chat then asks what breaks and gets cited rows."""
    import json
    import urllib.request

    page.goto(base + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("startup-first-steps").get_by_role("button", name="See a sample company").click()
    expect(page.get_by_test_id("sample-company-indicator")).to_be_visible(timeout=PAGE_TIMEOUT * 2)

    def call(name, arguments):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}).encode()
        request = urllib.request.Request(base.rstrip("/") + "/mcp", data=body, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + access_token})
        with urllib.request.urlopen(request, timeout=60) as reply:
            return json.loads(json.loads(reply.read())["result"]["content"][0]["text"])

    found = call("search_elements", {"query": "Payments Service"})
    text = json.dumps(found)
    assert "Payments" in text, text[:400]
    element = (found.get("data") or [None])[0]
    assert element, text[:400]
    impact = json.dumps(call("ask_impact", {"element_id": element.get("id")}))
    assert "Billing" in impact or "source" in impact.lower(), impact[:400]
    return impact


@pytest.mark.parametrize("name,viewport", [("1440", {"width": 1440, "height": 900}), ("390", {"width": 390, "height": 844})])
def test_the_last_step_connects_the_persons_own_ai_chat(browser, server, people, app, name, viewport):
    context = browser.new_context(viewport=viewport, ignore_https_errors=True, permissions=["clipboard-read", "clipboard-write"])
    context.set_default_timeout(PAGE_TIMEOUT)
    if os.environ.get("ONB_THEME") == "dark":
        context.add_init_script("try { localStorage.setItem('theme', 'dark'); } catch (e) {} document.documentElement.classList.add('dark');")
    page = context.new_page()
    try:
        registered = time.monotonic()
        sign_up(page, server, people)
        block = _step_four(page, server)
        expect(page.get_by_role("heading", name="Your AI chat")).to_be_visible()
        expect(block).to_be_visible()
        for client in ("Claude", "ChatGPT", "Cursor"):
            expect(block.get_by_role("tab", name=client)).to_be_visible()
        expect(block.get_by_text(re.compile("Connected: "))).to_have_count(0)

        address = block.get_by_test_id("mcp-address")
        expect(address).to_contain_text(server.rstrip("/") + "/mcp")
        block.get_by_role("button", name=re.compile("Copy")).first.click()
        assert page.evaluate("navigator.clipboard.readText()").strip() == server.rstrip("/") + "/mcp"
        block.get_by_role("tab", name="Cursor").click()
        expect(block.get_by_role("tab", name="Cursor")).to_have_attribute("aria-selected", "true")
        block.get_by_role("tab", name="Claude").click()
        _shot(page, "step4-before-%s" % name)
        _no_accessibility_violations(page)

        client_id = _register_client(app, "Claude")
        helper = context.new_page()
        access_token = _sign_in_from_the_chat(helper, server, client_id)
        helper.close()

        # No reload: the open step notices the sign-in on its own.
        connected = block.get_by_test_id("assistant-connected")
        expect(connected).to_contain_text(re.compile(r"Connected: Claude at \d"), timeout=20000)
        seconds = time.monotonic() - registered
        print("\n[onb06] %s wide: register to connected chat: %.1f s" % (name, seconds))
        _shot(page, "step4-after-%s" % name)
        _no_accessibility_violations(page)
        answer = _first_cited_answer(page, server, access_token)
        print("[onb06] %s wide: register to first cited answer through the connector: %.1f s" % (name, time.monotonic() - registered))
        assert answer

        # The same connection is on Connected assistants.
        page.goto(server + "/account/manage/connected-assistants", wait_until="domcontentloaded")
        rows = page.get_by_test_id("connected-assistant-row")
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text("Claude")

        # The built-in chat's link lands on the person's own settings, with content.
        _step_four(page, server)
        page.get_by_test_id("signup-built-in").get_by_role("link", name="Account settings").click()
        page.wait_for_url("**/account/manage**")
        assert "/settings" not in page.url.replace("/account/manage", "")
        assert page.locator("main").inner_text().strip()
    finally:
        context.close()


def test_a_second_organisation_never_sees_the_first_ones_connection(browser, server, people, app):
    first = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
    second = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
    for c in (first, second):
        c.set_default_timeout(PAGE_TIMEOUT)
    try:
        page_a, page_b = first.new_page(), second.new_page()
        sign_up(page_a, server, people)
        sign_up(page_b, server, people)
        _sign_in_from_the_chat(page_a, server, _register_client(app, "Cursor"))
        status_a = page_a.request.get(server + "/account/welcome/status").json()
        status_b = page_b.request.get(server + "/account/welcome/status").json()
        assert status_a["assistant_connected"]["client"] == "Cursor", status_a
        assert status_b["assistant_connected"] is None, status_b
        block = _step_four(page_b, server)
        expect(block).to_be_visible()
        expect(block.get_by_text(re.compile("Connected: "))).to_have_count(0)
        page_b.goto(server + "/account/manage/connected-assistants", wait_until="domcontentloaded")
        expect(page_b.get_by_test_id("connected-assistant-row")).to_have_count(0)
    finally:
        first.close()
        second.close()


def test_the_connected_time_is_never_shown_before_it_is_in_local_time(browser, server, people, app):
    """A person in Auckland: the step, loaded with its script held back, shows no time at all; with the script it shows local time."""
    context = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True, timezone_id="Pacific/Auckland", locale="en-GB")
    context.set_default_timeout(PAGE_TIMEOUT)
    try:
        page = context.new_page()
        sign_up(page, server, people)
        helper = context.new_page()
        _sign_in_from_the_chat(helper, server, _register_client(app, "Claude"))
        helper.close()

        held = context.new_page()
        held.route("**/js/account/signup_steps.js*", lambda route: route.abort())
        block = _step_four(held, server)
        line = block.get_by_test_id("assistant-connected")
        expect(line).to_contain_text("Connected: Claude")
        expect(line.locator("time")).to_be_hidden()  # no UTC time flashes while the script has not run
        assert not re.search(r"\d\d:\d\d", line.inner_text()), line.inner_text()
        held.close()

        block = _step_four(page, server)
        stamp = block.get_by_test_id("assistant-connected").locator("time")
        expect(stamp).to_be_visible()
        local = stamp.evaluate("el => new Date(el.getAttribute('datetime')).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})")
        assert stamp.inner_text() == local, (stamp.inner_text(), local)
        utc = stamp.evaluate("el => el.getAttribute('datetime').slice(11, 16)")
        assert stamp.inner_text() != utc, "the time is still the UTC time: %s" % utc  # Auckland is 12 or 13 hours ahead
    finally:
        context.close()


def test_the_status_poll_backs_off_and_stops_with_a_sentence(browser, server, people):
    context = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
    context.set_default_timeout(PAGE_TIMEOUT)
    try:
        page = context.new_page()
        sign_up(page, server, people)
        page.clock.install()
        asked = []
        page.on("request", lambda request: asked.append(request.url) if "/account/welcome/status" in request.url else None)
        block = _step_four(page, server)
        expect(block).to_be_visible()

        def run(seconds):
            for _ in range(seconds // 30):
                page.clock.run_for(30000)
                page.wait_for_timeout(120)

        run(60)
        in_first_minute = len(asked)
        assert 1 <= in_first_minute <= 10, in_first_minute  # a steady three-second poll would ask twenty times
        run(11 * 60)
        line = block.get_by_test_id("assistant-connected")
        expect(line).to_contain_text("Still waiting for your assistant; reload this step once you have connected it")
        stopped_at = len(asked)
        run(5 * 60)
        assert len(asked) == stopped_at, (stopped_at, len(asked))
    finally:
        context.close()
