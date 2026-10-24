"""Every question is asked in the AI chat and answered there with cited cards.

As the owner of a fresh workspace (enterprise architect) who has loaded the
sample company: opens the chat from the sidebar's "Ask a question", types what
breaks if the payments service fails, presses Enter and reads a card per
connection with where it was read from, its health and its owner; opens the
answer from its card and lands on the stored answer with the same rows; follows
an old Ask address and lands in the chat with the question waiting; presses
Escape and the box clears; when the answer cannot be worked out reads a plain
error and presses Retry. As the demo company's architect: asks about the most
connected application and reads cards whose count equals the total on the answer.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_try_sample_company_journey import _empty_organisation_user, _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("ASKCHAT_WALK_DIR")
QUESTION = "What breaks if Payments service fails"
DEMO_EMAIL = "demo@lantern-quay.example.com"


def _shot(page, name, width, theme="light"):
    if WALK:
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, theme, width)), full_page=False)


def _role_of(live_server, email):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.user import User

        user = User.query.filter_by(email=email).one()
        role = user.enterprise_role
        db.session.remove()
        return role


def _load_sample(page, base):
    page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Load sample").first.click()
    expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)


def _open_chat_from_sidebar(page, base):
    page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name="Ask a question").first.click()
    page.wait_for_url(re.compile(r"/ai-chat"), timeout=PAGE_TIMEOUT)
    expect(page.locator("#user-input")).to_be_visible(timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("width", [1440, 390])
def test_the_fresh_owner_asks_in_the_chat_and_reads_cited_cards(browser, live_server, seeded, width):
    email, org_id = _empty_organisation_user(live_server)
    assert _role_of(live_server, email) == "enterprise_architect"  # the persona, not an administrator
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, email)
        _load_sample(page, live_server)
        if width < 700:
            # The sidebar is a drawer on a phone: open the chat through the old address's destination.
            page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        else:
            _open_chat_from_sidebar(page, live_server)

        # What to ask first, and the scope and model stated.
        expect(page.get_by_text("How can I help you today?")).to_be_visible()
        expect(page.get_by_test_id("first-question-suggestion")).to_be_visible()
        scope = page.get_by_test_id("chat-scope").inner_text()
        assert "Sample Org" in scope and "Model:" in scope, scope
        _shot(page, "chat-empty", width)

        # The composer stays reachable at any width.
        box = page.locator("#user-input")
        expect(box).to_be_in_viewport()

        # Keyboard: type, Escape clears, type again, Enter sends.
        box.fill("something I decide not to send")
        box.press("Escape")
        expect(box).to_have_value("")
        box.fill(QUESTION)
        box.press("Enter")

        answer = page.get_by_test_id("estate-answer")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT)
        rows = answer.locator("[data-answer-row]")
        count = rows.count()
        assert count >= 10, count
        total = int(re.search(r"(\d+) connections? in your records", answer.inner_text()).group(1))
        assert count == total
        for i in range(count):
            row = rows.nth(i).locator("xpath=ancestor::*[@data-slot='card'][1]")
            assert row.locator("[data-citation]").inner_text().strip() not in ("", "Source not recorded")
        web = answer.locator("[data-slot='card']", has_text="Customer web app").first
        assert web.get_by_label("Health: Healthy").count() == 1
        assert web.get_by_label(re.compile(r"^Owner: ")).get_attribute("aria-label") == "Owner: Startup CTO"
        _shot(page, "chat-answer", width)

        # Every connection opens its element; the answer opens at its permanent address.
        first = rows.first.locator("a").first
        href = first.get_attribute("href")
        assert "/archimate/composer?element=" in href
        page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator("main, #main-content, body").first.inner_text().strip()
        page.go_back(wait_until="domcontentloaded")
        # Ask again to open the stored answer from the newest answer card.
        page.locator("#user-input").fill(QUESTION)
        page.locator("#user-input").press("Enter")
        opened = page.get_by_test_id("estate-answer").last.get_by_test_id("answer-open")
        expect(opened).to_be_visible(timeout=PAGE_TIMEOUT)
        opened.click()
        page.wait_for_url(re.compile(r"/intelligence/answers/[0-9a-f]{32}"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name=re.compile("What breaks if Payments service fails"))).to_be_visible()
        assert page.locator("[data-answer-rows] tbody tr").count() == count
    finally:
        context.close()


def test_the_old_ask_address_lands_in_the_chat_with_the_question(browser, live_server, seeded):
    email, org_id = _empty_organisation_user(live_server)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, email)
        page.goto(live_server + "/intelligence/ask?q=" + QUESTION.replace(" ", "%20"),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/ai-chat" in page.url and "/intelligence/ask" not in page.url
        expect(page.locator("#user-input")).to_have_value(QUESTION)
        _load_sample(page, live_server)
        page.goto(live_server + "/intelligence/ask?q=" + QUESTION.replace(" ", "%20"),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("#user-input").press("Enter")
        expect(page.get_by_test_id("estate-answer")).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_a_failed_answer_reads_as_an_error_and_retry_gets_it(browser, live_server, seeded):
    email, org_id = _empty_organisation_user(live_server)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, email)
        _load_sample(page, live_server)
        failures = {"left": 1}

        def refuse_once(route):
            if failures["left"]:
                failures["left"] -= 1
                route.abort()
            else:
                route.continue_()

        page.route(re.compile(r"/ai-chat/estate-answer"), refuse_once)
        page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("#user-input").fill(QUESTION)
        page.locator("#user-input").press("Enter")
        expect(page.get_by_text("We could not answer that just now.")).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, "chat-error", 1440)
        page.get_by_role("button", name="Retry").click()
        expect(page.get_by_test_id("estate-answer")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("We could not answer that just now.")).to_have_count(0)
    finally:
        context.close()


def test_the_answer_is_kept_in_the_conversation_history(browser, live_server, seeded):
    """The fresh owner asks, reloads, and finds the question and its answer in the conversation
    (and in the Chats list), with a link that opens the stored answer; another person's chat
    list does not hold it."""
    email, org_id = _empty_organisation_user(live_server)
    assert _role_of(live_server, email) == "enterprise_architect"  # the persona, not an administrator
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, email)
        _load_sample(page, live_server)
        page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("#user-input").fill(QUESTION)
        page.locator("#user-input").press("Enter")
        expect(page.get_by_test_id("estate-answer")).to_be_visible(timeout=PAGE_TIMEOUT)
        count = page.get_by_test_id("estate-answer").locator("[data-answer-row]").count()

        # A reload brings the conversation back: the question, the saved answer line, its link.
        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        messages = page.locator("#messages-container")
        expect(messages).to_contain_text(QUESTION, timeout=PAGE_TIMEOUT)
        expect(messages).to_contain_text("%d connections" % count)
        _shot(page, "chat-history", 1440)
        page.get_by_role("tab", name="Chats").click()
        expect(page.locator("#session-list")).to_contain_text("What breaks if Payments service", timeout=PAGE_TIMEOUT)

        # The kept answer opens the stored answer, with the same number of rows.
        messages.get_by_role("link", name="Open this answer").click()
        page.wait_for_url(re.compile(r"/intelligence/answers/[0-9a-f]{32}"), timeout=PAGE_TIMEOUT)
        assert page.locator("[data-answer-rows] tbody tr").count() == count
    finally:
        context.close()

    # Someone else, in another organisation, has no such conversation.
    other_email, _other_org = _empty_organisation_user(live_server)
    other = browser.new_context(viewport={"width": 1440, "height": 900})
    other.set_default_timeout(PAGE_TIMEOUT)
    other_page = other.new_page()
    try:
        other.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(other_page, live_server, other_email)
        other_page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        other_page.get_by_role("tab", name="Chats").click()
        expect(other_page.locator("#session-list")).not_to_contain_text("What breaks if Payments service")
    finally:
        other.close()


@pytest.fixture(scope="module")
def demo_application(live_server, app):
    from sqlalchemy import text

    from app import db
    from app.commands.seed_demo_company import seed_demo_company

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            seed_demo_company()
            row = db.session.execute(text(
                "select e.name from archimate_elements e join organizations o on o.id = e.organization_id "
                "where o.slug = 'lantern-quay' and e.type = 'ApplicationComponent' "
                "order by (select count(*) from archimate_relationships r where r.source_id = e.id) desc, e.id limit 1"
            )).first()
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    return row[0]


@pytest.mark.parametrize("width", [1440, 390])
def test_the_demo_architect_gets_cards_that_agree_with_the_answer_page(browser, live_server, demo_application, width):
    assert _role_of(live_server, DEMO_EMAIL) == "enterprise_architect"
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", DEMO_EMAIL)
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
        page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("#user-input").fill("What breaks if %s fails" % demo_application)
        page.locator("#user-input").press("Enter")
        answer = page.get_by_test_id("estate-answer")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT)
        text = answer.inner_text()
        total = re.search(r"(\d+) connections? in your records", text)
        if total:
            assert answer.locator("[data-answer-row]").count() == int(total.group(1))
        else:
            assert "Nothing is recorded as depending on this yet" in text
        assert "undefined" not in text and "[object" not in text and "None" not in text
        _shot(page, "chat-demo-answer", width)
    finally:
        context.close()


def test_the_chat_answer_reads_in_dark_mode(browser, live_server, seeded):
    email, org_id = _empty_organisation_user(live_server)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        context.add_init_script("try { localStorage.setItem('theme', 'dark') } catch (e) {}")
        _login(page, live_server, email)
        _load_sample(page, live_server)
        page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.evaluate("() => document.documentElement.classList.contains('dark')")
        _shot(page, "chat-empty", 1440, "dark")
        page.locator("#user-input").fill(QUESTION)
        page.locator("#user-input").press("Enter")
        expect(page.get_by_test_id("estate-answer")).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, "chat-answer", 1440, "dark")
        # The composer is on screen under the answer.
        expect(page.locator("#user-input")).to_be_in_viewport()
    finally:
        context.close()
