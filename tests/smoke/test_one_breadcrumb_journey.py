
import pytest
from playwright.sync_api import Page, expect

from tests.smoke.conftest import PASSWORD

@pytest.mark.journey
def test_one_breadcrumb_journey(page: Page, seeded, live_server):
    """
    Tests that there is only one breadcrumb on the page.
    """
    email = seeded["emails"]["platform_admin"]
    page.goto(f"{live_server}/account/login")
    page.fill("input[name='email']", email)
    page.fill("input[name='password']", PASSWORD)
    page.click("button[type='submit']")
    expect(page).to_have_url(f"{live_server}/dashboard/overview", timeout=180000)
    
    page.goto(f"{live_server}/capability-roadmap")

    # Expect a title "to contain" a substring.
    expect(page).to_have_title("Enterprise Capability Roadmap")

    # create a locator
    breadcrumb_nav = page.locator('nav[aria-label="Breadcrumb"], nav[aria-label="breadcrumb"]')

    # Expect an attribute "to be strictly equal" to the value.
    expect(breadcrumb_nav).to_have_count(1)
