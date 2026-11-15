"""A first answer a startup can trust.

On an empty model, the chat's "cannot answer yet" reply offers the next step as
buttons rather than only words. Once the sample company is loaded, every answer
about it names the sample company as its source, never the signed-in person,
and the Overview read 0 model records added by hand before that load. No answer
badge shows a raw ArchiMate CamelCase type name.

Run as a brand-new account (its own organisation, nothing recorded yet), at both
1440 wide in light and 390 wide in dark, the two widths and themes a person
actually meets.
"""

import os
import re

import pytest
from playwright.sync_api import expect

from .chat_ask import open_chat
from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("FIRST_ANSWER_WALK_DIR")

EMPTY_MODEL_QUESTION = "Which applications have no owner recorded?"
NAMED_QUESTION = "What breaks if Payments service fails?"

# The two combinations a person actually meets, per the brief: wide in light,
# narrow in dark.
SCENARIOS = [
    ("1440-light", {"width": 1440, "height": 900}, "light"),
    ("390-dark", {"width": 390, "height": 844}, "dark"),
]

CAMEL_CASE_TYPE = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b")
# Words that are legitimately CamelCase-shaped but are not ArchiMate type names
# leaking onto a badge (product names, acronyms as read elsewhere in the chat).
_CAMEL_ALLOW = {"GitHub", "ArchiMate", "JavaScript"}


def _ask(page, question):
    """Ask *question* in the already-open chat and return the newest answer card."""
    answer = page.get_by_test_id("estate-answer")
    before = answer.count()
    box = page.locator("#user-input")
    box.fill(question)
    box.press("Enter")
    expect(answer).to_have_count(before + 1, timeout=PAGE_TIMEOUT)
    return answer.nth(before)


def _shot(page, name, width):
    if WALK:
        page.screenshot(path=os.path.join(WALK, "%s-%d.png" % (name, width)), full_page=True)


def _assert_no_camel_case_type_badges(page):
    badges_text = page.locator("[data-answer-row] .inline-flex, [data-catalogue-cards]").all_inner_texts()
    for text in badges_text:
        for match in CAMEL_CASE_TYPE.findall(text):
            assert match in _CAMEL_ALLOW, "a type badge reads in CamelCase: %r in %r" % (match, text[:200])


@pytest.mark.parametrize("name,viewport,theme", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_first_answer_offers_next_steps_and_names_the_sample(browser, live_server, name, viewport, theme):
    base = live_server
    org = create_fresh_org("solution_architect")
    email = org["emails"]["solution_architect"]
    person_name = "Launch %s" % "solution_architect"[:12]

    context = browser.new_context(viewport=viewport, color_scheme=theme)
    context.set_default_timeout(PAGE_TIMEOUT)
    if theme == "dark":
        context.add_init_script(
            "try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
            "document.documentElement.classList.add('dark');"
        )
    page = context.new_page()
    try:
        sign_in(page, base, email)

        # 1. On an empty model, the "cannot answer yet" reply offers the next step
        # as buttons, not only words: Connect GitHub, and Load the sample company
        # as the secondary action beside it.
        open_chat(page, base)
        empty_answer = _ask(page, EMPTY_MODEL_QUESTION)
        steps = empty_answer.get_by_test_id("catalogue-empty-steps")
        expect(steps).to_be_visible(timeout=PAGE_TIMEOUT)
        connect_btn = steps.get_by_test_id("chat-answer-connect")
        sample_form = steps.get_by_test_id("chat-answer-sample-form")
        expect(connect_btn).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(sample_form).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, "a-empty-answer-buttons", viewport["width"])

        # 2. Before the sample loads, the Overview counts 0 records added by hand:
        # a brand-new account's own sign-up is not a model record.
        page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        empty_state = page.get_by_test_id("activity-empty")
        total = page.get_by_test_id("activity-total-90d")
        if total.count():
            assert total.first.inner_text().strip() == "0", \
                "a brand-new account already shows model records added: %s" % total.first.inner_text()
        else:
            expect(empty_state).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, "b-overview-before-sample", viewport["width"])

        # 3. Load the sample company from the chat's empty-answer button (not only
        # the welcome screen's). Opening the chat again starts a new conversation,
        # so the question is asked again to get a fresh empty-answer card with its
        # own buttons in this page.
        open_chat(page, base)
        fresh_empty_answer = _ask(page, EMPTY_MODEL_QUESTION)
        sample_button = fresh_empty_answer.get_by_test_id("chat-answer-sample")
        expect(sample_button.first).to_be_visible(timeout=PAGE_TIMEOUT)
        sample_button.first.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)

        open_chat(page, base)
        cited_answer = _ask(page, NAMED_QUESTION)
        answer_text = cited_answer.inner_text()

        # 4. The sample company names itself as the source, never the signed-in
        # person, as owner or as author.
        assert "Sample company" in answer_text, \
            "the answer about sample records does not name the sample company: %r" % answer_text[:300]
        assert person_name not in answer_text, \
            "the signed-in person is named in the answer: %r" % answer_text[:300]
        assert "Drawn by" not in answer_text, \
            "a sample row still reads as hand-drawn instead of naming the sample company: %r" % answer_text[:300]

        # 5. No answer badge is a raw ArchiMate CamelCase type name.
        _assert_no_camel_case_type_badges(page)
        _shot(page, "c-cited-answer-named-source", viewport["width"])
    finally:
        context.close()
