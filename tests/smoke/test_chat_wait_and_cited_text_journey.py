"""A typed question gets an answer or a plain failure within a minute, and a cited answer reads cleanly.

The person: the administrator of an organisation who types a question while the AI provider is slow.
Their goal: see what is happening, then an answer, or a plain failure with Retry, without being sent to a
page of provider keys they do not use. The provider is a loopback double that accepts the call and never
replies. The step before the chat turn, which looks for a stored answer and may ask the provider to
classify the question, is answered "no stored answer" here, so these journeys time the chat turn itself.
A second journey reads a cited answer whose text holds file names and the answer address in
backticks and a reference marker, and opens each link it shows.
"""

import json
import os
import re
import time

import pytest
from playwright.sync_api import expect

from tests.smoke.chat_ask import open_chat
from tests.smoke.conftest import PAGE_TIMEOUT, boot_live_server
from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.intelligence_graph import seed_impact_graph
from tests.test_chat_turn_budget import _Peer

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("CHAT_WAIT_SHOTS")
BUSY = "The assistant is busy right now. Wait a minute, then press Retry."
WAITING = "Still working: asking the AI. This usually takes under a minute."
OWN_KEY = "sk-own-key-for-the-journey-never-real"
PROVIDERS = ("ANTHROPIC", "AZURE", "CLAUDE", "GEMINI", "GOOGLE", "HUGGINGFACE", "NVIDIA", "OPENAI", "OPENROUTER")


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


@pytest.fixture(scope="module")
def never_answers():
    peer = _Peer()
    yield peer
    peer.close()


@pytest.fixture(scope="module")
def slow_provider_server(request, app, never_answers):
    """The application on its own port, whose only AI provider is the double that never replies."""
    env = {}
    for name in PROVIDERS:
        for suffix in ["", "_SECONDARY"] + ["_%d" % n for n in range(1, 10)]:
            env[name + "_API_KEY" + suffix] = ""
    env.update(
        LLM_API_KEY="", AZURE_OPENAI_API_KEY="", NVIDIA_API_KEY="platform-key-for-the-journey",
        AI_PROVIDER_ORDER="nvidia", AI_PROVIDER_BASE_URL_NVIDIA=never_answers.url,
        AI_PROVIDER_BASE_URL_OPENAI=never_answers.url,
        OPENAI_BASE_URL=never_answers.url, PYTHON_DOTENV_DISABLED="1",
        NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1",
    )
    return boot_live_server(request, None, app, extra_env=env)


def _give_own_key(org_id):
    from app import create_app, db
    from app.models.models import APISettings

    app = create_app("testing")
    with app.app_context():
        row = APISettings(provider="openai", enabled=True, default_model="gpt-4o", organization_id=org_id)
        row.api_key = OWN_KEY
        db.session.add(row)
        db.session.commit()
        row_id = row.id
        db.session.remove()
    return row_id


def _remove_own_key(row_id):
    from app import create_app, db
    from app.models.models import APISettings

    app = create_app("testing")
    with app.app_context():
        APISettings.query.filter(APISettings.id == row_id).delete(synchronize_session=False)
        db.session.commit()
        db.session.remove()


def _ask_and_wait_for_the_failure(page, base, question, label):
    """Type the question, return (seconds until the progress line, seconds until the failure card)."""
    page.route("**/ai-chat/estate-answer", lambda r: r.fulfill(status=200, content_type="application/json", body='{"found": false}'))
    open_chat(page, base)
    box = page.locator("#user-input")
    box.fill(question)
    sent = time.monotonic()
    box.press("Enter")
    note = page.get_by_test_id("chat-waiting-note")
    expect(note).to_be_visible(timeout=PAGE_TIMEOUT)
    assert "Still working" not in note.inner_text()
    expect(note).to_have_text(WAITING, timeout=20000)
    progress = time.monotonic() - sent
    shoot(page, label + "-waiting")
    card = page.locator('#messages-container [role="alert"]').last
    expect(card).to_be_visible(timeout=65000)
    failed = time.monotonic() - sent
    return progress, failed, card


def test_an_organisation_on_the_built_in_ai_sees_progress_then_a_plain_failure_with_no_link_to_keys(
    page, slow_provider_server
):
    org = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    sign_in(page, slow_provider_server, org["emails"]["platform_admin"])
    progress, failed, card = _ask_and_wait_for_the_failure(page, slow_provider_server, "Write two sentences on why single sign-on helps staff.", "builtin")
    assert 9.0 <= progress < 15.0, progress
    assert failed < 65.0, failed
    assert BUSY in card.inner_text()
    assert card.get_by_role("button", name="Retry").is_visible()
    assert page.locator('[data-testid="chat-provider-settings-link"]').count() == 0
    assert "API Settings" not in page.locator("#messages-container").inner_text()
    shoot(page, "builtin-failed")


def test_an_administrator_whose_own_key_failed_is_offered_the_provider_settings_and_it_opens_on_content(
    page, slow_provider_server
):
    org = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    other = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    row_id = _give_own_key(org["org_id"])
    try:
        sign_in(page, slow_provider_server, org["emails"]["platform_admin"])
        progress, failed, card = _ask_and_wait_for_the_failure(page, slow_provider_server, "Write two sentences on why single sign-on helps staff.", "ownkey")
        assert failed < 65.0, failed
        assert BUSY in card.inner_text()
        assert card.get_by_role("button", name="Retry").is_visible()
        link = card.get_by_test_id("chat-provider-settings-link")
        expect(link).to_be_visible()
        shoot(page, "ownkey-failed")
        link.click()
        page.wait_for_url(re.compile(r".*/admin/api-settings"), timeout=PAGE_TIMEOUT)
        expect(page.locator("#main-content")).not_to_be_empty()
        assert len(page.locator("#main-content").inner_text().strip()) > 40
        # The other organisation, on the built-in AI, still has no link: one organisation's key is not another's.
        second = page.context.browser.new_context(viewport={"width": 1440, "height": 900})
        try:
            other_page = second.new_page()
            sign_in(other_page, slow_provider_server, other["emails"]["platform_admin"])
            open_chat(other_page, slow_provider_server)
            assert other_page.evaluate("() => window.chatOwnKeyAdmin") is False
        finally:
            second.close()
    finally:
        _remove_own_key(row_id)


def test_a_cited_answer_shows_no_backticks_or_reference_text_and_its_links_open_on_content(page, live_server):
    org = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    graph = seed_impact_graph(org["org_id"])
    sign_in(page, live_server, org["emails"]["platform_admin"])
    service_id = graph["service"]
    page.goto("%s/intelligence/answers/element/%s" % (live_server, service_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_url(re.compile(r".*/intelligence/answers/[0-9a-f]{32}"), timeout=PAGE_TIMEOUT)
    address = re.search(r"/intelligence/answers/([0-9a-f]{32})", page.url).group(1)

    text = (
        "The file is `docker-compose.yml` and the repository is `` `Archiet-Ltd/archie-ea` ``.\n\n"
        "Answer address (permanent):\n\n`/intelligence/answers/%s`\n\n[ref:%s]\n\n"
        "The service is recorded [ref:%s]." % (address, address, service_id)
    )
    stream = "".join("data: %s\n\n" % json.dumps(e) for e in (
        {"type": "token", "text": text},
        {"type": "done", "response": text, "sources": [], "actions_taken": [], "pending_approvals": []},
    ))
    page.route("**/ai-chat/estate-answer", lambda r: r.fulfill(status=200, content_type="application/json", body='{"found": false}'))
    page.route("**/ai-chat/message/stream", lambda r: r.fulfill(status=200, content_type="text/event-stream", body=stream))
    open_chat(page, live_server)
    page.fill("#user-input", "What does the service depend on?")
    page.press("#user-input", "Enter")
    message = page.locator("#messages-container .message-content").last
    expect(message).to_contain_text("The service is recorded", timeout=PAGE_TIMEOUT)
    shown = message.inner_text()
    assert "`" not in shown and "[ref:" not in shown, shown
    assert "docker-compose.yml" in shown and "Archiet-Ltd/archie-ea" in shown
    links = message.locator("a")
    hrefs = [links.nth(i).get_attribute("href") for i in range(links.count())]
    assert "/intelligence/answers/%s" % address in hrefs, hrefs
    assert "/intelligence/answers/element/%s" % service_id in hrefs, hrefs
    shoot(page, "cited")
    chat_url = page.url
    for href in ("/intelligence/answers/%s" % address, "/intelligence/answers/element/%s" % service_id):
        page.goto(chat_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.route("**/ai-chat/estate-answer", lambda r: r.fulfill(status=200, content_type="application/json", body='{"found": false}'))
        page.route("**/ai-chat/message/stream", lambda r: r.fulfill(status=200, content_type="text/event-stream", body=stream))
        page.fill("#user-input", "What does the service depend on?")
        page.press("#user-input", "Enter")
        last = page.locator("#messages-container .message-content").last
        expect(last).to_contain_text("The service is recorded", timeout=PAGE_TIMEOUT)
        last.locator('a[href="%s"]' % href).first.click()
        page.wait_for_url(re.compile(r".*/intelligence/answers/[0-9a-f]{32}"), timeout=PAGE_TIMEOUT)
        body = page.locator("#main-content").inner_text()
        assert len(body.strip()) > 80 and graph["names"]["service"] in body, body[:200]
