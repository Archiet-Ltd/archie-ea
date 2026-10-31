"""A free-plan owner is told which models read their content, before the first request, and can act on it.

The person: a startup CTO who has just signed up on the free plan. The goal: know, before anything is
sent to an AI model, that free third-party models process their content, and find the two ways to
avoid it (their own key, a paid plan) and the page that explains it. Every link on the notice lands on
a page with content, the notice is on the sign-up sources step, the AI provider step and in Settings,
and it is gone once the organisation is on a paid plan.

Starts at /account/register as a new person, at 1440 and at 390 wide. Set ONB_WALK_DIR to save screenshots.
"""
import os

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
SIZES = [("1440", {"width": 1440, "height": 900}), ("390", {"width": 390, "height": 844})]
SAID = "processed by free third-party models"


@pytest.fixture(scope="module")
def live_server(server):
    return server


def _shot(page, name, size):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "airoute-%s-%s.png" % (name, size)), full_page=True)


def _notice(page):
    notice = page.get_by_test_id("ai-route-notice")
    expect(notice).to_be_visible()
    expect(notice).to_contain_text(SAID)
    return notice


def _lands_with_content(page, notice, link_name, needle):
    """Open a link from the notice, assert the page it lands on has content, and come back."""
    came_from = page.url
    notice.get_by_role("link", name=link_name).click()
    page.wait_for_load_state("domcontentloaded")
    assert page.url != came_from, "%s opened the page it was on" % link_name
    body = page.locator("body").inner_text()
    assert needle.lower() in body.lower(), "%s landed on a page without %r: %s" % (link_name, needle, body[:300])
    page.goto(came_from, wait_until="domcontentloaded")


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_a_free_plan_owner_is_told_who_reads_their_content_before_the_first_request(
    browser, live_server, provider, people, app, name, viewport
):
    context = browser.new_context(viewport=viewport)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        email, _started = sign_up(page, live_server, people)

        # Step 2: the notice sits with the permission to read the website, before anything is read.
        notice = _notice(page)
        assert provider.calls == [], "a model was asked before the person had been told"
        _shot(page, "step2", name)
        _lands_with_content(page, notice, "See plans", "Team")
        _lands_with_content(page, _notice(page), "How your content is handled", "free third-party models")

        # Step 4: the same notice; the key link opens the key form on the page.
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        notice = _notice(page)
        expect(page.get_by_test_id("signup-built-in")).to_contain_text("free third-party models")
        expect(page.get_by_test_id("signup-own-key")).not_to_be_visible()
        notice.get_by_role("link", name="Add your own key").click()
        expect(page.get_by_test_id("signup-own-key")).to_be_visible()
        _shot(page, "step4", name)
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")

        # Settings: the notice, with a key link that goes to the form on the same page.
        page.goto(live_server + "/admin/api-settings", wait_until="domcontentloaded")
        notice = _notice(page)
        link = notice.get_by_role("link", name="Add your own key")
        assert link.get_attribute("href") == "#api-key-form"
        link.click()
        expect(page.locator("#api-key-form")).to_be_visible()
        _shot(page, "settings", name)
        _lands_with_content(page, _notice(page), "See plans", "Team")

        # On a paid plan the notice is gone.
        with app.app_context():
            from app import db
            from app.models.organization import Organization
            from app.models.user import User
            from app.services import billing_plans

            user = User.query.filter_by(email=email).one()
            org = db.session.get(Organization, user.organization_id)
            assert billing_plans.set_contract_plan(org, "team", 5)
            db.session.commit()
            db.session.remove()
        page.goto(live_server + "/admin/api-settings", wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name="API Settings").first).to_be_visible()
        expect(page.get_by_test_id("ai-route-notice")).to_have_count(0)
    finally:
        context.close()
