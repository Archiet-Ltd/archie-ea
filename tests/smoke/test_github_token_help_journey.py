
import uuid
from playwright.sync_api import Page, expect
import pytest
from tests.smoke.conftest import login, PASSWORD

@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_github_token_help_on_signup(page: Page, viewport, theme, live_server):
    page.goto(live_server + "/account/register")

    email = f"test-{uuid.uuid4().hex[:8]}@example.com"
    page.locator("#organization_name").fill("Test Org")
    page.locator("#first_name").fill("Test")
    page.locator("#last_name").fill("User")
    page.locator("#email").fill(email)
    page.locator("#password").fill(PASSWORD)
    page.locator("button[type='submit']").click()

    page.wait_for_url(f"{live_server}/account/welcome?step=1")
    page.goto(f"{live_server}/account/welcome?step=2", wait_until="networkidle")

    page.emulate_media(color_scheme=theme)
    expect(page.locator('[data-testid="signup-github"] a')).to_have_attribute("href", "https://github.com/settings/personal-access-tokens/new")
    expect(page.locator('[data-testid="signup-github"] a')).to_have_attribute("target", "_blank")

    collapsible = page.locator('[data-testid="signup-github"] [data-collapsible-id="github-token-help"]')
    expect(collapsible.locator('[data-collapsible-trigger]')).to_be_visible()
    expect(collapsible.locator('[data-collapsible-content]')).not_to_be_visible()

    collapsible.locator('[data-collapsible-trigger]').press("Enter")
    expect(collapsible.locator('[data-collapsible-content]')).to_be_visible()

    content = collapsible.locator('[data-collapsible-content]')
    expect(content.locator("li", has_text="Contents to Read-only")).to_be_visible()
    expect(content.locator("li", has_text="Metadata to Read-only")).to_be_visible()
    expect(content.locator("li", has_text="Members to Read-only")).to_be_visible()


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_github_token_help_on_connector_page(page: Page, viewport, theme, live_server, seeded):
    login(page, live_server, seeded, archetype="platform_admin")
    page.goto(live_server + "/admin/connectors/github", wait_until="networkidle")
    page.emulate_media(color_scheme=theme)

    expect(page.locator('form[data-testid="github-connect-form"] a')).to_have_attribute("href", "https://github.com/settings/personal-access-tokens/new")
    expect(page.locator('form[data-testid="github-connect-form"] a')).to_have_attribute("target", "_blank")

    collapsible = page.locator('form[data-testid="github-connect-form"] [data-collapsible-id="github-token-help"]')
    expect(collapsible.locator('[data-collapsible-trigger]')).to_be_visible()
    expect(collapsible.locator('[data-collapsible-content]')).not_to_be_visible()

    collapsible.locator('[data-collapsible-trigger]').press("Enter")
    expect(collapsible.locator('[data-collapsible-content]')).to_be_visible()

    content = collapsible.locator('[data-collapsible-content]')
    expect(content.locator("li", has_text="Contents to Read-only")).to_be_visible()
    expect(content.locator("li", has_text="Metadata to Read-only")).to_be_visible()
    expect(content.locator("li", has_text="Members to Read-only")).to_be_visible()
