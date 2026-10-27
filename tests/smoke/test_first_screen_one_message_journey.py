"""The first screen a person meets carries one message.

A founder opens the public home and sees one headline, one sentence, one first step, two buttons and a real
screen of the Overview, and nothing else in the first 900 pixels. A new architect signs in to an empty workspace
and sees one message with its action and a question box, no figures or tabs. A second architect
signs in to a workspace that holds data and sees the question box and then the figures, with no answer on the page; asking a question lands on the chat with that question waiting.
"""
import pytest
from playwright.sync_api import expect

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
        assert page.title() == "Entelim — The company brain"
        hero = page.locator('[data-testid="home-hero"]')
        expect(hero.locator("h1")).to_have_count(1)
        expect(hero.locator("h1")).to_have_text("The company brain.")
        # one sentence, one first step, two buttons
        expect(hero.locator("p.home-lead")).to_have_count(1)
        expect(page.locator('[data-testid="home-hero-first-step"]')).to_contain_text("Start with your website")
        buttons = hero.locator('[data-testid="home-hero-start"], [data-testid="home-hero-pricing"]')
        expect(buttons).to_have_count(2)
        expect(hero.get_by_role("link")).to_have_count(2)
        # the proof is a real capture of the Overview, loaded, and names its subject
        proof = page.locator('[data-testid="home-hero-proof"]')
        assert proof.evaluate("e => e.complete && e.naturalWidth > 0")
        assert _top(page, '[data-testid="home-hero-proof"]') < FOLD["height"]
        assert "overview" in proof.get_attribute("alt").lower()
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
        message = page.locator('[data-testid="first-message"]')
        expect(message).to_contain_text("Connect GitHub or Jira")
        action = page.locator('[data-testid="first-action-sample"]')
        question = page.locator('[data-testid="first-question-input"]')
        for target in (message, action, question):
            expect(target).to_be_in_viewport()
        # nothing else: no figures, tabs or quick links until there is data
        for testid in ("kpi-tile-applications", "layer-tab-overview", "layer-tab-motivation", "architecture-health"):
            expect(page.locator('[data-testid="%s"]' % testid)).to_have_count(0)
        # this architect is not an administrator but brings sources in, so the button is
        # there and opens the GitHub page with its form rather than a refusal
        expect(page.locator('[data-testid="first-action-connect"]')).to_have_count(1)
        expect(message).not_to_contain_text("organisation administrator")
        page.locator('[data-testid="first-action-connect"]').click()
        page.wait_for_url("**/admin/connectors/github", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("button", name="Connect and read my repositories")).to_be_visible()
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)

        # the question lands on the chat with the question waiting
        question.fill("what breaks if payments fails?")
        page.locator('[data-testid="first-question-submit"]').click()
        page.wait_for_url("**/ai-chat**", timeout=PAGE_TIMEOUT)
        expect(page.locator("#user-input")).to_have_value("what breaks if payments fails?")

        # the sample button loads a populated workspace
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.locator('[data-testid="first-action-sample"]').click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        expect(page.locator('[data-testid="first-message"]')).to_contain_text("Ask your company a question")
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [FOLD, PHONE], ids=["1440", "390"])
def test_populated_workspace_opens_on_question_and_shows_no_answer(browser, live_server, seeded, viewport):
    email = seeded["emails"]["enterprise_architect"]
    _assert_persona(email)
    context, page = _page(browser, viewport)
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        message = page.locator('[data-testid="first-message"]')
        expect(message).to_contain_text("Ask your company a question")
        expect(page.locator('[data-testid="first-question-input"]')).to_be_in_viewport()
        # the dashboard never renders an answer: the question is answered in the chat
        expect(page.locator('[data-testid="overview-cascade"]')).to_have_count(0)
        # the figures and the nine tabs come after the message
        message_bottom = message.evaluate("e => e.getBoundingClientRect().bottom")
        assert _top(page, '[data-testid="kpi-tile-applications"]') >= message_bottom
        assert _top(page, '[data-testid="layer-tab-overview"], [data-testid="layer-select-mobile"]') >= message_bottom
        if viewport["width"] == FOLD["width"]:
            # a tile still lands on a list with content
            page.locator('[data-testid="kpi-tile-applications"]').click()
            page.wait_for_url("**/applications/**", timeout=PAGE_TIMEOUT)
            assert len(page.locator("main").first.inner_text().strip()) > 50
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [FOLD, PHONE], ids=["1440", "390"])
def test_empty_workspace_fold_holds_only_the_one_message(browser, live_server, seeded, viewport):
    """Signed in as a new architect: the welcome line and the setup list wait below the fold, and the one
    onboarding question sits inside the message card."""
    email, _ = _empty_organisation_user(live_server)
    _assert_persona(email)
    context, page = _page(browser, viewport)
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="first-message"]')).to_be_in_viewport()
        for testid in ("welcome-banner", "guided-setup"):
            target = page.locator('[data-testid="%s"]' % testid)
            if target.count():
                assert _top(page, '[data-testid="%s"]' % testid) >= viewport["height"], testid
        # the company-kind choice is inside the same card, not a section of its own
        expect(page.locator('[data-testid="first-message"] [data-testid="company-kind-selector"]')).to_be_visible()
    finally:
        context.close()


def test_first_answer_in_the_chat_names_the_owner_and_its_source(browser, live_server, seeded):
    """Signed in as the architect who loaded the sample company: the question typed on the dashboard
    is answered in the chat, and the capability card names the owner and cites the import."""
    email, _ = _empty_organisation_user(live_server)
    _assert_persona(email)
    context, page = _page(browser, FOLD)
    try:
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.locator('[data-testid="first-action-sample"]').click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        page.locator('[data-testid="first-question-input"]').fill("What breaks if Payments service fails")
        page.locator('[data-testid="first-question-submit"]').click()
        page.wait_for_url("**/ai-chat**", timeout=PAGE_TIMEOUT)
        page.locator("#user-input").press("Enter")
        answer = page.get_by_test_id("estate-answer")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        capability = answer.locator("[data-slot='card']").filter(has=page.locator("[data-answer-row]")).filter(has_text="Sell to customers online")
        expect(capability.first).to_contain_text("Startup CTO")
        expect(capability.first).not_to_contain_text("No owner recorded")
        assert "Sample company" in capability.first.locator("[data-citation]").inner_text()
    finally:
        context.close()
