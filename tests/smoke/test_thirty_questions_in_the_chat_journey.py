"""The thirty startup questions are answered in the AI chat, each with a record to open.

As the owner of a fresh workspace (enterprise architect) who has loaded the sample company,
and as the demo company's architect: opens the chat, types each of the thirty plain questions
a startup asks (what breaks, who owns what, where we pay twice, what we run, what came from
GitHub), presses Enter and reads a cited answer card. Each answer either lists the records it
rests on, every one a link that opens a page naming that record, or says plainly that the
records cannot answer yet and what would let them. As the owner of a workspace with nothing
in it: asks who owns what and reads that explanation, with no record to open.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_ask_in_the_chat_journey import DEMO_EMAIL, _load_sample, _role_of, _shot, demo_application  # noqa: F401
from .test_try_sample_company_journey import _empty_organisation_user, _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# One browser context asks all thirty questions, so allow time for every answer.
ANSWER_TIMEOUT = PAGE_TIMEOUT * 2
# Questions that must come back citing a record: the sample company holds no service named in the
# what-breaks questions, nothing unowned and nothing from GitHub, so those say so plainly instead.
FLOOR = 13


def _thirty():
    from app import create_app

    app = create_app("testing")
    with app.app_context():
        from app.modules.intelligence.services.nl_query_interpreter import evaluation_set

        questions = [c["question"] for c in evaluation_set() if c["expected_entry_id"] is not None]
    assert len(questions) == 30, len(questions)
    return questions


def _ask_all(page, base, questions, width, tag):
    """Ask every question in the chat; return what each answer showed."""
    seen = []
    for position, question in enumerate(questions):
        cards = page.get_by_test_id("estate-answer")
        before = cards.count()
        box = page.locator("#user-input")
        box.fill(question)
        box.press("Enter")
        expect(cards).to_have_count(before + 1, timeout=ANSWER_TIMEOUT)
        card = cards.last
        records = card.locator("[data-catalogue-record] a, [data-answer-row] a")
        linked = [
            (records.nth(i).get_attribute("href"), records.nth(i).inner_text().strip())
            for i in range(records.count())
        ]
        text = card.inner_text()
        assert "undefined" not in text and "[object" not in text and "None" not in text, text
        if linked:
            assert all(href and name for href, name in linked), linked
        else:
            # No record to cite: the card must say so and say what would let it answer.
            # An empty answer says why (a finding such as "every application has an owner", or
            # that the records cannot answer yet) and, when data is missing, what would let it.
            assert re.search(r"cannot answer|Every application|name the service", text, re.I), (question, text)
            if "cannot answer" in text:
                assert re.search(r"Add your|Connect a|Record |Map each|Name a service", text), (question, text)
        seen.append({"question": question, "linked": linked, "text": text})
        if position == 0:
            _shot(page, "%s-first-answer" % tag, width)
    _shot(page, "%s-last-answer" % tag, width)
    return seen


def _open_every_record(browser, base, email, seen):
    """Each distinct record a card linked to opens a page that names it."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, base, email)
        opened = {}
        for answer in seen:
            for href, name in answer["linked"][:2]:
                if "/composer?element=" in href or href in opened:
                    continue
                response = page.goto(base + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                assert response is not None and response.status == 200, (href, response and response.status)
                body = page.locator("main, #main-content, body").first.inner_text()
                assert name.split(" (")[0] in body, (href, name)
                opened[href] = name
        return opened
    finally:
        context.close()


def _report(seen, label):
    cited = sum(1 for a in seen if a["linked"])
    print("%s: %d of %d questions answered with citations" % (label, cited, len(seen)))
    for a in seen:
        if not a["linked"]:
            print("  no citation: %s -> %s" % (a["question"], " ".join(a["text"].split())[:160]))
    return cited


@pytest.mark.parametrize("width", [1440, 390])
def test_the_fresh_owner_asks_the_thirty_questions_in_the_chat(browser, live_server, seeded, width):
    email, org_id = _empty_organisation_user(live_server)
    assert _role_of(live_server, email) == "enterprise_architect"  # the persona, not an administrator
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, email)
        _load_sample(page, live_server)
        page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        seen = _ask_all(page, live_server, _thirty(), width, "thirty-sample")
    finally:
        context.close()
    cited = _report(seen, "sample company, %d wide" % width)
    assert cited >= FLOOR, (cited, [a['question'] for a in seen if not a['linked']])
    if width == 1440:
        assert _open_every_record(browser, live_server, email, seen)


def test_the_demo_architect_asks_the_thirty_questions_in_the_chat(browser, live_server, demo_application):
    assert _role_of(live_server, DEMO_EMAIL) == "enterprise_architect"
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, DEMO_EMAIL)
        page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        seen = _ask_all(page, live_server, _thirty(), 1440, "thirty-demo")
    finally:
        context.close()
    cited = _report(seen, "demo company")
    assert cited >= FLOOR, cited
    assert _open_every_record(browser, live_server, DEMO_EMAIL, seen)


def test_an_empty_workspace_is_told_what_would_let_the_chat_answer(browser, live_server, seeded):
    email, org_id = _empty_organisation_user(live_server)
    assert _role_of(live_server, email) == "enterprise_architect"
    context = browser.new_context(viewport={"width": 390, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, email)
        page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("#user-input").fill("Which applications have no owner recorded?")
        page.locator("#user-input").press("Enter")
        card = page.get_by_test_id("estate-answer")
        expect(card).to_be_visible(timeout=ANSWER_TIMEOUT)
        expect(card).to_contain_text("Your records cannot answer this yet")
        expect(card).to_contain_text("No applications are recorded in this organisation")
        expect(card).to_contain_text("Add your applications to the portfolio")
        assert card.locator("[data-catalogue-record]").count() == 0
        _shot(page, "thirty-empty", 390)
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_the_cited_and_the_plain_answers_read_in_dark_mode(browser, live_server, seeded, width):
    email, org_id = _empty_organisation_user(live_server)
    assert _role_of(live_server, email) == "enterprise_architect"
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        context.add_init_script("try { localStorage.setItem('theme', 'dark') } catch (e) {}")
        _login(page, live_server, email)
        _load_sample(page, live_server)
        page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for question, expected in (
            ("What applications do we run and who owns each one?", "[data-catalogue-record] a"),
            ("What came from GitHub?", "text=Connect a GitHub repository"),
        ):
            cards = page.get_by_test_id("estate-answer")
            before = cards.count()
            page.locator("#user-input").fill(question)
            page.locator("#user-input").press("Enter")
            expect(cards).to_have_count(before + 1, timeout=ANSWER_TIMEOUT)
            expect(cards.last.locator(expected).first).to_be_visible()
        assert page.evaluate("document.documentElement.classList.contains('dark')")
        _shot(page, "thirty-answers", width, theme="dark")
    finally:
        context.close()
