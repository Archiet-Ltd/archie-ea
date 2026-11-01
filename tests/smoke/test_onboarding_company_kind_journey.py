"""Company kind journey: a person chooses it in Account settings, it persists, and each organisation keeps its own.

Journey:
1. Sign in to a new organisation; the first screen does not interrupt with the question
2. Open Account settings, see Startup selected, choose Scale-up
3. Reload and see it kept
4. Change it to Enterprise and see that kept
5. A second organisation signed in afterwards still sees Startup, never the first one's choice
6. Reaching GitHub connect from the first screen is still one click
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_company_kind_first_screen_journey import (
    _empty_organisation_user, _new_page, _selected, _stored_kind,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWPORT = {"width": 1440, "height": 900}


@pytest.mark.journey
def test_onboarding_company_kind_journey(browser, live_server, seeded):
    """A user chooses their company kind, it persists, and is editable in settings."""
    email1, org1 = _empty_organisation_user(live_server)
    email2, org2 = _empty_organisation_user(live_server)

    context, page, errors = _new_page(browser, live_server, email1, VIEWPORT)
    try:
        # the first screen does not stop the person with the question
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("overview-welcome")).to_be_visible()
        expect(page.locator('[data-testid="company-kind-selector"]')).to_have_count(0)

        # Account settings: Startup is the default; choose Scale-up
        page.goto(live_server + "/account/manage", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        card = page.locator('[data-testid="company-kind-card"]')
        expect(card).to_be_visible()
        assert _selected(page, "startup")
        page.locator('[data-testid="company-kind-scaleup"]').click()
        expect(page.locator('[data-testid="company-kind-status"]')).to_contain_text("Saved", timeout=PAGE_TIMEOUT)
        assert _stored_kind(org1) == "scaleup"

        page.reload(wait_until="networkidle")
        assert _selected(page, "scaleup")

        page.locator('[data-testid="company-kind-enterprise"]').click()
        expect(page.locator('[data-testid="company-kind-status"]')).to_contain_text("Enterprise", timeout=PAGE_TIMEOUT)
        page.goto(live_server + "/account/manage", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert _selected(page, "enterprise")
        assert _stored_kind(org1) == "enterprise"
        assert not errors, errors
    finally:
        context.close()

    # the second organisation sees its own answer, not the first one's
    context, page, errors = _new_page(browser, live_server, email2, VIEWPORT)
    try:
        page.goto(live_server + "/account/manage", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator('[data-testid="company-kind-card"]')).to_be_visible()
        assert _selected(page, "startup") and not _selected(page, "enterprise")
        page.locator('[data-testid="company-kind-startup"]').click()
        expect(page.locator('[data-testid="company-kind-status"]')).to_contain_text("Startup", timeout=PAGE_TIMEOUT)
        assert _stored_kind(org2) == "startup"
        assert _stored_kind(org1) == "enterprise"
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.journey
def test_github_connect_still_one_click(browser, live_server, seeded):
    """Reaching GitHub connect from the first screen is one click, and it lands on a page with content."""
    email, _org = _empty_organisation_user(live_server)
    context, page, errors = _new_page(browser, live_server, email, VIEWPORT)
    try:
        page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("overview-welcome")).to_contain_text("Connect GitHub and your model is built for you")
        page.get_by_test_id("overview-connect").click()
        page.wait_for_load_state("domcontentloaded")
        assert "/dashboard/overview" not in page.url
        assert len(page.locator("main").first.inner_text().strip()) > 40
        assert not errors, errors
    finally:
        context.close()
