"""A new startup CTO reads a clean first answer, and the sample loads quickly every time.

The person: a self-serve owner (lens "cto", not a platform administrator, asserted in each
journey) who has just signed up. The goal: load the sample company, ask what breaks if the
payments service fails, and read an answer that names its assistant, says every link in plain
words and never prints a missing value.
"""
import os
import re
import time

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_sign_up_first_screen_journey import QUESTION, SIZES, _skip_to_the_dashboard
from .test_signup_brings_the_company_in_journey import (  # noqa: F401  (people is a fixture)
    assert_is_a_startup_cto_not_an_administrator,
    people,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("ONB_WALK_DIR")


def _shot(page, name, width):
    if WALK:
        page.screenshot(path=os.path.join(WALK, "%s-%d.png" % (name, width)), full_page=True)


def _load_sample(page, base):
    page.goto(base + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    started = time.time()
    page.get_by_test_id("chat-first-sample").click()
    expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)
    return time.time() - started


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_the_first_answer_is_clean_and_in_plain_words(browser, live_server, people, app, name, viewport):
    context = browser.new_context(viewport=viewport)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        email, _ = sign_up(page, live_server, people)
        assert_is_a_startup_cto_not_an_administrator(app, email)
        _skip_to_the_dashboard(page)
        _load_sample(page, live_server)

        # "Remove it" is named for what it says (a visible label must be part of its name)
        remove = page.get_by_role("button", name="Remove it", exact=True)
        assert remove.count() == 1
        assert remove.inner_text().strip() == "Remove it"
        _shot(page, "c-sample-loaded", viewport["width"])

        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        chosen = page.locator("#persona-selector").evaluate("e => e.options[e.selectedIndex].text")
        assert chosen == "CTO / technical founder", chosen

        page.locator("#user-input").fill(QUESTION)
        page.locator("#user-input").press("Enter")
        answer = page.get_by_test_id("estate-answer")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT)
        assert answer.locator("[data-answer-row]").count() >= 10

        # what a person reads first is plain: no technical wording, no missing value
        text = answer.inner_text()
        for technical in ("derivation table", "followed by", "started by A person"):
            assert technical not in text, text
        assert not re.search(r"\bNone\b", text), text
        _shot(page, "c-answer-plain", viewport["width"])
        # the technical detail is one click away, behind "How this was worked out"
        assert answer.locator("[data-why-detail]:visible").count() == 0
        assert answer.locator("[data-why-toggle]").count() >= 1
        for toggle in answer.locator("[data-why-toggle]").all():
            toggle.click()
        _shot(page, "c-answer-opened", viewport["width"])
        opened = answer.inner_text()
        assert "ArchiMate derivation table" in opened or "no stronger rule applies" in opened, opened
        assert not re.search(r"\bNone\b", opened), opened
        # every citation names a source in words
        for citation in answer.locator("[data-citation]").all():
            assert re.search(r"[A-Za-z]{4}", citation.inner_text()), citation.inner_text()
        # the plain sentence is shown and the technical detail sits behind the disclosure
        assert answer.locator("[data-why-sentence]").count() >= 1
        assert answer.locator("[data-why-detail]").count() >= 1

        # the empty-context sentence is a full sentence naming the context in use
        page.locator("#domain-context").wait_for(state="attached")
        context_text = page.locator("#domain-context").inner_text()
        assert "No specific context available" not in context_text, context_text
    finally:
        context.close()


def test_a_second_press_while_the_sample_loads_does_nothing_and_the_person_is_told(browser, live_server, people, app):
    """A person who taps again on a slow phone must not start a second load: the button locks,
    a line says it is loading, and the second press sends nothing."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        email, _ = sign_up(page, live_server, people)
        assert_is_a_startup_cto_not_an_administrator(app, email)
        _skip_to_the_dashboard(page)
        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("chat-first-sample")).to_be_visible()
        # Count the submissions that get past the page's own handler, and stop them leaving the
        # page so the state after the first press can be read.
        page.evaluate("""() => {
            window.__sent = 0;
            document.addEventListener('submit', (e) => {
                if (!e.defaultPrevented) { window.__sent += 1; e.preventDefault(); }
            });
        }""")
        form = page.locator('[data-testid="chat-first-sample-form"]')
        busy = page.locator('[data-testid="chat-first-sample-busy"]')
        expect(busy).to_be_hidden()
        page.locator('[data-testid="chat-first-sample"]').click()
        expect(busy).to_be_visible()
        expect(busy).to_contain_text("Loading the sample company")
        expect(page.locator('[data-testid="chat-first-sample"]')).to_be_disabled()
        form.evaluate("f => f.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))")
        assert page.evaluate("window.__sent") == 1
    finally:
        context.close()


def test_the_sample_loads_in_under_ten_seconds_every_time_on_a_phone_in_the_dark(browser, live_server, people, app):
    """Five new owners in a row, each loading the sample at 390 wide in the dark: every load ends
    on the populated dashboard in under ten seconds (the first, on a cold server, in twenty), and a second press cannot load it twice."""
    timings = []
    for _ in range(5):
        context = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
        context.set_default_timeout(PAGE_TIMEOUT)
        context.add_init_script(
            "try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
            "document.documentElement.classList.add('dark');"
        )
        page = context.new_page()
        try:
            email, _ = sign_up(page, live_server, people)
            assert_is_a_startup_cto_not_an_administrator(app, email)
            _skip_to_the_dashboard(page)
            page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
            button = page.locator('[data-testid="chat-first-sample"]')
            started = time.time()
            button.click()
            expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)
            timings.append(time.time() - started)
            # the populated dashboard: the sample's applications are counted on the first screen
            assert int(page.locator('[data-testid="welcome-applications"] span').first.inner_text().strip()) > 0
        finally:
            context.close()
    print("\n[onb05c] sample load seconds at 390 dark: %s" % ", ".join("%.1f" % t for t in timings))
    # The first load after the server starts also pays for loading its code; every one after it
    # is the load a person meets, and must end inside ten seconds.
    assert timings[0] < 20, timings
    assert max(timings[1:]) < 10, timings
