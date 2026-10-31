""""What if we retire Payments service?" reaches the chat's own impact tool.

The what-if branch in multi_domain_chat_service.py is not what the ordinary
/ai-chat compose box reaches -- that box is served by
AgentRunner (app/modules/ai_chat/services/agent_runner.py) through the
/ai-chat/estate-answer route, which nudges the model to call the chat's own
canonical impact tool only when answer_envelope.classify_estate_question()
recognises the question as an estate question. That classifier matched
"if X fails", "if X is retired", "if X is decommissioned" and similar, but not
the active phrasing a person actually types -- "what if we retire X", "what if
we decommission X", "what if we remove X", "what happens if X is removed",
"what if we replace X". classify_estate_question is widened (with its own unit
tests) to recognise these; nothing else on this path changes.

Once the question classifies as an impact question and names a recorded
element, /ai-chat/estate-answer answers it from the model's own recorded
connections (app/modules/ai_chat/services/estate_answer.py) -- the same
answer envelope the Ask and Impact pages use -- and the chat never reaches the
model at all for this question. This journey now asserts that cited answer:
each dependent named, with its own source and as-of date, and never the
uncited "No lookup ran" text or an internal API path.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_try_sample_company_journey import _empty_organisation_user, _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("WHATIF_WALK_DIR")
QUESTION = "What if we retire Payments service?"
DEPENDENTS = ("Customer web app", "Order management system")


class _EchoProvider(BaseHTTPRequestHandler):
    """Echoes the last chat message's content back as the answer, so the
    rendered page shows exactly what the assistant was handed, with no real
    model. Speaks the OpenAI-compatible, non-streaming chat-completions shape
    the NVIDIA path calls through the openai SDK client, the one provider
    path in this codebase whose base URL is read from an environment
    variable (LLMService.base_url_for) rather than hardcoded.
    """

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages") or []
        content = messages[-1].get("content", "") if messages else ""
        payload = json.dumps({
            "id": "echo-1", "created": 0, "model": "echo-model", "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture(scope="module")
def echo_provider_server(request, app):
    peer = ThreadingHTTPServer(("127.0.0.1", 0), _EchoProvider)
    threading.Thread(target=peer.serve_forever, daemon=True).start()
    request.addfinalizer(peer.shutdown)
    base = "http://127.0.0.1:%d" % peer.server_port
    return boot_live_server(request, None, app, extra_env={
        "NVIDIA_API_KEY": "echo-key",
        "NVIDIA_API_BASE": base + "/v1",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    })


def _load_sample_from_chat(page, base):
    page.goto(base + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("chat-first-sample").click()
    expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)


def _ask_what_if(page, live_server, question=QUESTION):
    page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.locator("#user-input").fill(question)
    page.locator("#user-input").press("Enter")
    expect(page.locator("#messages-container")).to_contain_text(
        question, timeout=PAGE_TIMEOUT * 2
    )


def _assert_cited_dependents(page):
    """The question is answered by the chat's own impact tool, from the
    model's recorded connections: every dependent of Payments service named,
    each with its own source and as-of date, never the uncited "No lookup
    ran" text and never an internal API path."""
    answer = page.get_by_test_id("estate-answer")
    expect(answer).to_be_visible(timeout=PAGE_TIMEOUT * 2)
    text = answer.inner_text()
    assert "No lookup ran" not in text, text
    assert "/api/" not in text, text
    # never the frozen fixed-value scenario service's report shape, were it
    # ever (wrongly) reachable from this surface
    assert "RETIREMENT IMPACT ANALYSIS" not in text
    assert "migration cost" not in text.lower()

    rows = answer.locator("[data-answer-row]")
    count = rows.count()
    assert count > 0, text
    names = []
    for i in range(count):
        row = rows.nth(i)
        name = row.locator("a").first.inner_text().strip()
        assert name, "row %d has no name: %s" % (i, text)
        names.append(name)
        card = row.locator("xpath=ancestor::*[@data-slot='card'][1]")
        source = card.locator("[data-citation]").inner_text().strip()
        assert source not in ("", "Source not recorded"), \
            "row %r has no source: %r" % (name, source)
        assert "/api/" not in source
        date = card.locator("[data-provenance-date]")
        if date.count():
            assert date.first.inner_text().strip() != "Date not recorded", \
                "row %r has no as-of date" % name
    for dependent in DEPENDENTS:
        assert dependent in names, "%r not among the cited dependents: %s" % (dependent, names)
    return names


def test_what_if_retire_is_answered_with_cited_dependents_at_1440_light(
    browser, echo_provider_server
):
    email, _org_id = _empty_organisation_user(echo_provider_server)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, echo_provider_server, email)
        _load_sample_from_chat(page, echo_provider_server)
        _ask_what_if(page, echo_provider_server)
        _assert_cited_dependents(page)
        if WALK:
            os.makedirs(WALK, exist_ok=True)
            page.screenshot(path=os.path.join(WALK, "whatif-1440-light.png"))
    finally:
        context.close()


def test_what_if_retire_is_answered_with_cited_dependents_at_390_dark(
    browser, echo_provider_server
):
    email, _org_id = _empty_organisation_user(echo_provider_server)
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context.add_init_script("try { localStorage.setItem('theme', 'dark') } catch (e) {}")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, echo_provider_server, email)
        _load_sample_from_chat(page, echo_provider_server)
        _ask_what_if(page, echo_provider_server)
        _assert_cited_dependents(page)
        assert page.evaluate("document.documentElement.classList.contains('dark')")
        if WALK:
            os.makedirs(WALK, exist_ok=True)
            page.screenshot(path=os.path.join(WALK, "whatif-390-dark.png"))
    finally:
        context.close()


def test_a_second_organisation_sees_none_of_the_firsts_records(
    browser, echo_provider_server
):
    """A second, empty organisation asking the identical question sees none
    of the first organisation's dependents -- the model's own connections,
    not the question's wording, decide the answer."""
    email_one, _org_one = _empty_organisation_user(echo_provider_server)
    email_two, _org_two = _empty_organisation_user(echo_provider_server)

    context_one = browser.new_context(viewport={"width": 1440, "height": 900})
    context_one.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context_one.set_default_timeout(PAGE_TIMEOUT)
    page_one = context_one.new_page()
    context_two = browser.new_context(viewport={"width": 1440, "height": 900})
    context_two.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context_two.set_default_timeout(PAGE_TIMEOUT)
    page_two = context_two.new_page()
    try:
        _login(page_one, echo_provider_server, email_one)
        _load_sample_from_chat(page_one, echo_provider_server)
        _ask_what_if(page_one, echo_provider_server)
        names_one = _assert_cited_dependents(page_one)

        # The second organisation never loaded the sample company: it has no
        # element named "Payments service" and so nothing recorded to answer
        # this question from.
        _login(page_two, echo_provider_server, email_two)
        _ask_what_if(page_two, echo_provider_server)
        text_two = page_two.locator("#messages-container").inner_text()
        for dependent in DEPENDENTS:
            assert dependent not in text_two, \
                "second organisation's chat shows the first organisation's %r: %s" % (
                    dependent, text_two[:500])
        assert set(names_one) & set(DEPENDENTS) == set(DEPENDENTS)
    finally:
        context_one.close()
        context_two.close()


UNKNOWN_QUESTION = "What breaks if we retire Zephyr Quillborne Ledger?"
NEAR_QUESTION = "What if we retire Payment service?"


def _unknown_answer(page, wait_for):
    container = page.locator("#messages-container")
    expect(container).to_contain_text(wait_for, timeout=PAGE_TIMEOUT * 2)
    return container.inner_text()


def _unknown_context(browser, width, height, dark):
    context = browser.new_context(viewport={"width": width, "height": height})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    if dark:
        context.add_init_script("try { localStorage.setItem('theme', 'dark') } catch (e) {}")
    context.set_default_timeout(PAGE_TIMEOUT)
    return context


SAMPLE_NAMES = ("Payments service", "Customer web app", "Order management system",
                "Cache layer", "Cloud platform", "Data warehouse", "Document management")


@pytest.mark.parametrize("width,height,dark,shot", [
    (1440, 900, False, "whatif-unknown-1440-light.png"),
    (390, 844, True, "whatif-unknown-390-dark.png"),
])
def test_what_if_a_name_close_to_nothing_says_nothing_similar_and_links_to_the_recorded_applications(
    browser, echo_provider_server, width, height, dark, shot
):
    """Nothing recorded shares a word with Zephyr Quillborne Ledger: the answer
    says nothing similar is recorded, lists no name as "closest", never shows
    other systems as its dependants, and its link opens the organisation's own
    recorded applications. A second organisation with no records is offered
    none of the first one's names."""
    email, _org_id = _empty_organisation_user(echo_provider_server)
    email_two, _org_two = _empty_organisation_user(echo_provider_server)
    context = _unknown_context(browser, width, height, dark)
    context_two = _unknown_context(browser, width, height, dark)
    page = context.new_page()
    page_two = context_two.new_page()
    try:
        _login(page, echo_provider_server, email)
        _load_sample_from_chat(page, echo_provider_server)
        _ask_what_if(page, echo_provider_server, UNKNOWN_QUESTION)
        text = _unknown_answer(page, "Nothing similar is recorded")
        lowered = text.lower()
        assert "nothing recorded matches 'zephyr quillborne ledger'" in lowered, text
        assert "closest recorded names" not in lowered, text
        assert "cannot answer this yet" not in lowered, text
        for name in SAMPLE_NAMES:
            assert name not in page.get_by_test_id("estate-answer").inner_text(), text
        assert "depend on it" not in lowered, text
        assert "single points of failure" not in lowered, text
        assert not page.locator("[data-catalogue-record]").count(), text
        if WALK:
            os.makedirs(WALK, exist_ok=True)
            page.screenshot(path=os.path.join(WALK, shot))

        link = page.get_by_role("link", name="See your recorded applications")
        expect(link).to_be_visible()
        link.click()
        page.wait_for_load_state("networkidle")
        expect(page.locator("body")).to_contain_text("Payments service", timeout=PAGE_TIMEOUT)
        assert "/applications" in page.url, page.url
        if WALK:
            page.screenshot(path=os.path.join(WALK, shot.replace("unknown", "browse")))

        _login(page_two, echo_provider_server, email_two)
        _ask_what_if(page_two, echo_provider_server, UNKNOWN_QUESTION)
        text_two = _unknown_answer(page_two, "Nothing recorded matches")
        for name in SAMPLE_NAMES:
            assert name not in text_two, "second organisation is offered %r: %s" % (name, text_two)
    finally:
        context.close()
        context_two.close()


@pytest.mark.parametrize("width,height,dark,shot", [
    (1440, 900, False, "whatif-near-1440-light.png"),
    (390, 844, True, "whatif-near-390-dark.png"),
])
def test_what_if_a_near_name_is_listed_first_and_unrelated_names_are_not(
    browser, echo_provider_server, width, height, dark, shot
):
    email, _org_id = _empty_organisation_user(echo_provider_server)
    context = _unknown_context(browser, width, height, dark)
    page = context.new_page()
    try:
        _login(page, echo_provider_server, email)
        _load_sample_from_chat(page, echo_provider_server)
        _ask_what_if(page, echo_provider_server, NEAR_QUESTION)
        text = _unknown_answer(page, "Closest recorded names")
        closest = text.split("Closest recorded names:", 1)[1].split(". ", 1)[0]
        names = [n.strip().rstrip(".") for n in closest.split(",") if n.strip()]
        assert names[0] == "Payments service", text
        assert len(names) <= 5, text
        assert "Cache layer" not in names and "Data warehouse" not in names, text
        assert "Nothing similar is recorded" not in text, text
        if WALK:
            os.makedirs(WALK, exist_ok=True)
            page.screenshot(path=os.path.join(WALK, shot))
    finally:
        context.close()
