"""The first screen a person meets carries one message.

A founder opens the public home and sees one headline, one sentence, two buttons and a real
answer, and nothing else in the first 900 pixels. A new architect signs in to an empty workspace
and sees one message with its action and a question box, no figures or tabs. A second architect
signs in to a workspace that holds data and sees the question box and then the figures, with no answer on the page; asking a question lands on the chat with that question waiting.
"""
import re

import pytest
from playwright.sync_api import expect

from .chat_ask import ask
from .conftest import PAGE_TIMEOUT
from .test_company_kind_first_screen_journey import _empty_organisation_user
from .test_try_sample_company_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

FOLD = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}


def _page(browser, viewport):
    context = browser.new_context(viewport=viewport)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    return context, context.new_page()


def _assert_persona(email, role="enterprise_architect"):
    """The signed-in user is the persona the journey names, not an administrator or platform role."""
    from app import create_app
    from app.middleware.tenant_decorators import is_platform_admin
    from app.models.user import User
    from app.services.rbac_service import rbac_service

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == role, user.enterprise_role
        assert not is_platform_admin(user)
        assert not rbac_service.is_org_admin(user, user.organization_id)


def _top(page, selector):
    return page.locator(selector).first.evaluate("e => e.getBoundingClientRect().top")


def test_public_home_fold_is_one_message(browser, live_server):
    context, page = _page(browser, FOLD)
    try:
        page.goto(live_server + "/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert page.title() == "Entelim — The company brain: a digital twin of your business"
        hero = page.locator('[data-testid="home-hero"]')
        expect(hero.locator("h1")).to_have_count(1)
        expect(hero.locator("h1")).to_have_text("The company brain for your business.")
        # one sentence, two buttons
        expect(hero.locator("p.home-lead")).to_have_count(1)
        buttons = hero.locator('[data-testid="home-hero-start"], [data-testid="home-hero-pricing"]')
        expect(buttons).to_have_count(2)
        expect(hero.get_by_role("link")).to_have_count(2)
        # the proof is a real answer, loaded, and shows its subject
        proof = page.locator('[data-testid="home-hero-proof"]')
        assert proof.evaluate("e => e.complete && e.naturalWidth > 0")
        assert _top(page, '[data-testid="home-hero-proof"]') < FOLD["height"]
        assert "what breaks if" in proof.get_attribute("alt")
        # nothing else: no licence line, and the next section starts below the fold
        assert "AGPL" not in hero.inner_text()
        assert _top(page, "section.home-section") >= FOLD["height"]
        # the two buttons land on pages with content
        for testid, expected in (("home-hero-pricing", "pricing"), ("home-hero-start", "register")):
            page.locator('[data-testid="%s"]' % testid).click()
            page.wait_for_url("**/*%s*" % expected, timeout=PAGE_TIMEOUT)
            assert len(page.locator("body").inner_text().strip()) > 100
            page.goto(live_server + "/", wait_until="domcontentloaded")
    finally:
        context.close()


def test_empty_workspace_first_screen_is_one_message(browser, live_server, seeded):
    email, org_id = _empty_organisation_user(live_server)
    _assert_persona(email)
    context, page = _page(browser, FOLD)
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        message = page.locator('[data-testid="overview-welcome"]')
        expect(message).to_contain_text("Connect GitHub and your model is built for you")
        action = page.locator('[data-testid="overview-connect"]')
        for target in (message, action):
            expect(target).to_be_in_viewport()
        # nothing else claims a figure until there is data: no counts on the welcome card
        expect(page.locator('[data-testid="welcome-figures"]')).to_have_count(0)
        for testid in ("card-run-cost", "card-relationships", "card-no-owner"):
            assert page.locator('[data-testid="%s-value"]' % testid).count() == 0
        # this architect is not an administrator but brings sources in, so the button opens the
        # GitHub page with its form rather than a refusal
        expect(message).not_to_contain_text("organisation administrator")
        action.click()
        page.wait_for_url("**/admin/connectors/github", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("button", name="Connect and read my repositories")).to_be_visible()

        # the chat is reached from the sidebar
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.get_by_role("navigation", name="Main navigation").get_by_role("link", name="AI chat").click()
        page.wait_for_url("**/ai-chat**", timeout=PAGE_TIMEOUT)

        # the chat's sample button loads a populated workspace, and the overview then shows its figures
        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.locator('[data-testid="chat-first-sample"]').click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="welcome-figures"]')).to_be_visible()
        assert int(page.locator('[data-testid="welcome-applications"] span').first.inner_text()) >= 8

        # the question typed in the chat is answered there, with a card that cites where it was read from
        answer = ask(page, live_server, "what breaks if payments service fails?")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        card = answer.locator("[data-slot='card']").filter(has=page.locator("[data-answer-row]")).first
        expect(card).to_be_visible()
        assert "Sample" in card.locator("[data-citation]").inner_text()
        # the answer opens at its own address, on a page that carries the same answer
        answer.get_by_test_id("answer-open").click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("Payments service")
        expect(page.locator("body")).to_contain_text("Customer web app")
        assert "/ai-chat" not in page.url, page.url
    finally:
        context.close()


def test_sample_answer_names_each_system_once_and_the_owner_the_sample_records(browser, live_server, seeded):
    """The architect who loaded the sample asks what breaks if the Payments service fails. The
    sentence names each affected system once, the Customer web app card names the owner the sample
    records for it, and that owner agrees with the application's own record."""
    email, _ = _empty_organisation_user(live_server)
    _assert_persona(email)
    context, page = _page(browser, FOLD)
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.locator('[data-testid="chat-first-sample"]').click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        answer = ask(page, live_server, "What breaks if Payments service fails")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        sentence = answer.get_by_test_id("answer-sentence").inner_text()
        names = [n.strip(" .") for n in re.split(r", | and ", sentence.split("affected directly", 1)[1].replace("through them", "").replace("through it", "")) if n.strip(" .")]
        assert len(names) >= 3 and len(names) == len(set(names)), "a system is named twice: %s" % sentence
        card = answer.locator("[data-slot='card']").filter(has=page.locator("[data-answer-row]")).filter(has_text="Customer web app").first
        expect(card).to_contain_text("Sam (sample owner)")
        expect(card).not_to_contain_text("No owner recorded")
        # the application's own screen records the same owner
        page.goto(live_server + "/applications/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("Sam (sample owner)")
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [FOLD, PHONE], ids=["1440", "390"])
def test_populated_workspace_opens_on_welcome_and_figures_and_links_land(browser, live_server, seeded, viewport):
    email = seeded["emails"]["enterprise_architect"]
    _assert_persona(email)
    context, page = _page(browser, viewport)
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        message = page.locator('[data-testid="overview-welcome"]')
        expect(message).to_be_in_viewport()
        expect(message).to_contain_text("Here is how your model is doing.")
        # the dashboard never renders an answer: the question is answered in the chat
        expect(page.locator('[data-testid="overview-cascade"]')).to_have_count(0)
        # the figure cards come after the welcome card, none of them an unexplained dash or zero
        message_bottom = message.evaluate("e => e.getBoundingClientRect().bottom")
        if viewport["width"] == PHONE["width"]:
            assert _top(page, '[data-testid="card-relationships"]') >= message_bottom - 1
        else:
            expect(page.locator('[data-testid="card-relationships"]')).to_be_in_viewport()
        # the welcome card's applications figure still lands on a list with content
        page.locator('[data-testid="welcome-applications"]').click()
        page.wait_for_url("**/applications/**", timeout=PAGE_TIMEOUT)
        assert len(page.locator("main").first.inner_text().strip()) > 50
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [FOLD, PHONE], ids=["1440", "390"])
def test_empty_workspace_fold_holds_only_the_one_message(browser, live_server, seeded, viewport):
    """Signed in as a new architect: the welcome card with its one action sits in the first screen and
    holds no figure."""
    email, _ = _empty_organisation_user(live_server)
    _assert_persona(email)
    context, page = _page(browser, viewport)
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="overview-welcome"]')).to_be_in_viewport()
        expect(page.locator('[data-testid="overview-connect"]')).to_be_in_viewport()
        expect(page.locator('[data-testid="overview-welcome"] [data-testid="welcome-figures"]')).to_have_count(0)
    finally:
        context.close()


def test_first_answer_in_the_chat_names_the_owner_and_its_source(browser, live_server, seeded):
    """Signed in as the architect who loaded the sample company: the question typed in the chat
    is answered there, and the capability card says no owner is recorded and cites the import."""
    email, _ = _empty_organisation_user(live_server)
    _assert_persona(email)
    context, page = _page(browser, FOLD)
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.locator('[data-testid="chat-first-sample"]').click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        answer = ask(page, live_server, "What breaks if Payments service fails")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        capability = answer.locator("[data-slot='card']").filter(has=page.locator("[data-answer-row]")).filter(has_text="Sell to customers online")
        # the sample names no real person, so a capability says plainly that no owner is recorded
        # rather than showing the person who loaded it
        expect(capability.first).to_contain_text("No owner recorded")
        expect(capability.first).not_to_contain_text("Startup CTO")
        assert "Sample" in capability.first.locator("[data-citation]").inner_text()
    finally:
        context.close()
