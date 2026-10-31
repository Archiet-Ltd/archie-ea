"""A new owner goes from sign-up through the four-step welcome to a first cited answer.

The person: a startup CTO who has never seen the product. The goal: sign up, find themselves in
an organisation of their own, be offered GitHub and Jira (and a website to read) on the sources
step, carry on past the company and AI provider steps without being blocked, and get to a first
cited answer, either by connecting GitHub or by looking around the sample company the chat offers.
No role wizard or feature tour stands in front of any of it.

Every journey starts at /account/register as a new person, never a fixture user whose
onboarding is finished, at 1440 and at 390 wide. Set ONB_WALK_DIR to save screenshots; the seconds
from sign-up to the first cited answer are printed.
"""
import os
import re
import time

import pytest
from playwright.sync_api import expect

from .chat_ask import ask
from .conftest import PAGE_TIMEOUT
from .test_signup_brings_the_company_in_journey import (  # noqa: F401  (these are fixtures)
    assert_is_a_startup_cto_not_an_administrator,
    people,
    provider,
    server,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def live_server(server):
    """These journeys use the same server as the sign-up journeys in the other file, one at a time.

    Two servers on one database each pick up the other's background reads, and each holds its own
    encryption key, so a GitHub token written by one cannot be read by the other.
    """
    return server


WALK = os.environ.get("ONB_WALK_DIR")
QUESTION = "What breaks if Payments service fails"
SIZES = [("1440", {"width": 1440, "height": 900}), ("390", {"width": 390, "height": 844})]
STEPS = ("Your account", "Your sources", "Your company", "Your AI chat")


def _open(browser, viewport):
    context = browser.new_context(viewport=viewport)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    if os.environ.get("ONB_THEME") == "dark":
        context.add_init_script(
            "try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
            "document.documentElement.classList.add('dark');"
        )
    return context, context.new_page()


def _shot(page, name, size):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%s.png" % (name, size)), full_page=True)


def _skip_to_the_dashboard(page):
    """Carry on past the sources, company and AI provider steps without giving anything."""
    page.get_by_test_id("signup-continue").click()
    page.wait_for_url("**/account/welcome?step=3")
    page.get_by_test_id("signup-continue").click()
    page.wait_for_url("**/account/welcome?step=4")
    page.get_by_role("button", name="Finish").click()
    page.wait_for_url("**/dashboard/**")


def _the_owner_has_an_organisation_of_their_own(app, email):
    """The person's organisation exists, they are its only member, and it carries their name."""
    with app.app_context():
        from app import db
        from app.models.organization import Organization
        from app.models.user import User

        user = User.query.filter_by(email=email).one()
        assert user.organization_id is not None
        org = db.session.get(Organization, user.organization_id)
        members = [u.email for u in User.query.filter_by(organization_id=org.id).all()]
        assert members == [email], members
        name = org.name
        org_id = org.id
        db.session.remove()
    return org_id, name


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_new_owner_is_welcomed_in_four_steps_in_their_own_organisation(browser, live_server, people, app, name, viewport):
    context, page = _open(browser, viewport)
    try:
        email, _started = sign_up(page, live_server, people)
        assert_is_a_startup_cto_not_an_administrator(app, email)
        _org_id, org_name = _the_owner_has_an_organisation_of_their_own(app, email)
        assert "Casey Founder" in org_name, org_name

        # The four steps are named, the second is the one in front of the person, and no role
        # wizard or feature tour stands in the way, however long we wait.
        nav = page.get_by_role("navigation", name="Progress steps")
        for step in STEPS:
            expect(nav).to_contain_text(step)
        expect(nav.get_by_role("button", name=re.compile("Step 2"))).to_have_attribute("aria-current", "step")
        page.wait_for_timeout(1000)
        body = page.locator("body").inner_text()
        for phrase in ("What's your role?", "Key features for you", "You're all set"):
            assert phrase not in body, phrase
        expect(page.locator("[x-show='showOnboarding']")).to_have_count(0)

        # What cannot be inferred is asked for here: the website (with permission), GitHub and Jira.
        expect(page.get_by_role("heading", name="Bring in your company")).to_be_visible()
        website = page.get_by_test_id("signup-website")
        expect(website.get_by_label("Website address")).to_be_visible()
        expect(website.get_by_label("You may read this website")).not_to_be_checked()
        github = page.get_by_test_id("signup-github")
        expect(github.get_by_label("GitHub organisation")).to_be_visible()
        expect(github.get_by_label("Read-only token")).to_be_visible()
        expect(github.get_by_role("button", name="Connect GitHub")).to_be_visible()
        jira = page.get_by_test_id("signup-jira")
        expect(jira.get_by_label("Jira site address")).to_be_visible()
        expect(jira.get_by_label("Jira email")).to_be_visible()
        expect(jira.get_by_label("Jira API token")).to_be_visible()
        expect(jira.get_by_role("button", name="Connect Jira")).to_be_visible()
        # Nothing is asked of the person before they can carry on: Continue is in the first view.
        expect(page.get_by_test_id("signup-continue")).to_be_in_viewport()
        # Nothing is claimed to be connected before anything was given.
        expect(page.get_by_text("is connected")).to_have_count(0)
        _shot(page, "step2-sources", name)

        # The company step names its questions and the AI provider step offers the built-in AI.
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        expect(page.get_by_role("heading", name="About your company")).to_be_visible()
        for label in ("Company size", "Your role", "What do you want first?", "What kind of company are you?", "Your industry"):
            expect(page.get_by_label(label)).to_be_visible()
        _shot(page, "step3-company", name)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        expect(page.get_by_role("heading", name="Your AI chat")).to_be_visible()
        expect(page.get_by_test_id("signup-built-in")).to_be_visible()
        _shot(page, "step4-ai", name)
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")

        # They arrive in their own workspace, and the one thing it asks for lands on a page with content.
        expect(page.get_by_test_id("active-organization-chip")).to_contain_text("Casey Founder", ignore_case=False)
        connect = page.get_by_test_id("overview-connect")
        expect(connect).to_be_visible()
        _shot(page, "dashboard", name)
        connect.click()
        expect(page.get_by_role("heading", name=re.compile("GitHub")).first).to_be_visible()
        assert page.locator("main").inner_text().strip()
    finally:
        context.close()


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_sign_up_to_first_cited_answer(browser, live_server, people, app, name, viewport):
    context, page = _open(browser, viewport)
    started = time.time()
    try:
        email, _ = sign_up(page, live_server, people)
        assert_is_a_startup_cto_not_an_administrator(app, email)
        _skip_to_the_dashboard(page)

        # Nothing was given, so the chat offers the sample company next to Connect GitHub.
        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        steps = page.get_by_test_id("startup-first-steps")
        expect(steps).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(steps.get_by_role("link", name="Connect GitHub")).to_be_visible()
        _shot(page, "chat-offer", name)
        steps.get_by_role("button", name="See a sample company").click()
        expect(page.get_by_test_id("sample-company-indicator")).to_be_visible(timeout=PAGE_TIMEOUT * 2)

        answer = ask(page, live_server, QUESTION)
        rows = answer.locator("[data-answer-row]")
        assert rows.count() >= 10
        for i in range(rows.count()):
            card = rows.nth(i).locator("xpath=ancestor::*[@data-slot='card'][1]")
            assert card.locator("[data-citation]").inner_text().strip() not in ("", "Source not recorded")
        print("\n[sign-up] %s wide: sign-up to first cited answer: %.1f s" % (name, time.time() - started))
        _shot(page, "first-answer", name)
    finally:
        context.close()


def test_two_new_owners_each_see_only_their_own_organisation(browser, live_server, people, app):
    """The first owner loads the sample company; the second, in another organisation, does not see it."""
    context_a, page_a = _open(browser, SIZES[0][1])
    context_b, page_b = _open(browser, SIZES[0][1])
    try:
        email_a, _ = sign_up(page_a, live_server, people)
        email_b, _ = sign_up(page_b, live_server, people)
        org_a, _name_a = _the_owner_has_an_organisation_of_their_own(app, email_a)
        org_b, _name_b = _the_owner_has_an_organisation_of_their_own(app, email_b)
        assert org_a != org_b

        _skip_to_the_dashboard(page_a)
        page_a.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page_a.get_by_test_id("startup-first-steps").get_by_role("button", name="See a sample company").click()
        expect(page_a.get_by_test_id("sample-company-indicator")).to_be_visible(timeout=PAGE_TIMEOUT * 2)

        _skip_to_the_dashboard(page_b)
        page_b.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        # B still has nothing recorded: the offer is still on the page and no sample is loaded.
        expect(page_b.get_by_test_id("startup-first-steps").get_by_role("button", name="See a sample company")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page_b.get_by_test_id("sample-company-indicator")).to_have_count(0)
        page_b.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page_b.get_by_test_id("overview-connect")).to_be_visible()
        expect(page_b.get_by_test_id("welcome-applications")).to_have_count(0)
    finally:
        context_a.close()
        context_b.close()
