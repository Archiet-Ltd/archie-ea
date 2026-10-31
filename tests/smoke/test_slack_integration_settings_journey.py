"""A platform administrator saves a Slack integration, sees Connected, and the token is not echoed back.

Starts from the goal: "connect the AI Architect to our Slack and be sure it saved". The journey saves a test
token, reads every setting back after a reload, is refused in words for a wrong token and wrong channel, and
asks the connection test to report in words. The page must also read as the same family as the GitHub
connector page (one card at the same width, one primary Save).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
TOKEN = "xoxb-journey-test-token-0001"
SECRET = "journey-signing-secret-0001"
CHANNELS = "C0123ABCDEF, C0456GHIJKL"
PAGE = "/admin/integrations/slack"


@pytest.fixture()
def admin_page(browser, live_server):
    org = create_fresh_org("solution_architect", org_admin=True, platform_role="Administrator")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["solution_architect"])
    yield page
    context.close()


def _open(page, live_server):
    page.goto(live_server + PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", level=1)).to_contain_text("Slack")


def test_every_setting_has_a_label_and_the_page_is_one_card(admin_page, live_server):
    _open(admin_page, live_server)
    page = admin_page
    for label in ("Bot token", "Signing secret", "Monitored channels"):
        expect(page.get_by_label(label, exact=False).first).to_be_visible()
    for name in ("@entelim mention responses", "Passive portfolio monitoring"):
        expect(page.get_by_role("switch", name=name)).to_be_visible()
    # One primary action, a test control, and the empty state says it is not set.
    expect(page.locator("[data-testid=slack-form] button[type=submit]")).to_have_count(1)
    expect(page.get_by_role("button", name="Test connection")).to_be_visible()
    expect(page.get_by_test_id("slack-state")).to_have_text("Not set")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_saved_settings_read_back_and_the_token_is_never_echoed(admin_page, live_server):
    _open(admin_page, live_server)
    page = admin_page
    page.get_by_label("Bot token").fill(TOKEN)
    page.get_by_label("Signing secret").fill(SECRET)
    page.get_by_label("Monitored channels").fill(CHANNELS)
    page.get_by_role("switch", name="Passive portfolio monitoring").click()
    page.get_by_role("button", name="Save configuration").click()
    expect(page.get_by_test_id("slack-saved")).to_contain_text("Slack configuration saved")
    expect(page.get_by_test_id("slack-state")).to_have_text("Connected")

    # After a reload everything reads back, and neither secret is anywhere in the page.
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_test_id("slack-state")).to_have_text("Connected")
    expect(page.get_by_label("Monitored channels")).to_have_value(CHANNELS)
    expect(page.get_by_role("switch", name="@entelim mention responses")).to_have_attribute("aria-checked", "true")
    expect(page.get_by_role("switch", name="Passive portfolio monitoring")).to_have_attribute("aria-checked", "false")
    html = page.content()
    assert TOKEN not in html and SECRET not in html
    expect(page.get_by_label("Bot token")).to_have_value("")
    expect(page.get_by_label("Bot token")).to_have_attribute("placeholder", "Leave blank to keep the existing value")

    # Saving again with the token left blank keeps the stored token: the state stays Connected.
    page.get_by_label("Monitored channels").fill("C0999ZZZZZZ")
    page.get_by_role("button", name="Save configuration").click()
    expect(page.get_by_test_id("slack-saved")).to_be_visible()
    expect(page.get_by_test_id("slack-state")).to_have_text("Connected")
    expect(page.get_by_label("Monitored channels")).to_have_value("C0999ZZZZZZ")


def test_a_wrong_value_is_refused_in_words_and_the_field_is_kept(admin_page, live_server):
    _open(admin_page, live_server)
    page = admin_page
    page.get_by_label("Bot token").fill("not-a-slack-token")
    page.get_by_label("Monitored channels").fill("general, random")
    page.get_by_role("button", name="Save configuration").click()
    expect(page.get_by_test_id("slack-error-bot_token")).to_contain_text("xoxb-")
    expect(page.get_by_test_id("slack-error-monitored_channels")).to_contain_text("channel IDs")
    expect(page.get_by_label("Monitored channels")).to_have_value("general, random")
    expect(page.get_by_test_id("slack-state")).to_have_text("Not set")
    # Nothing was stored by the refused save.
    page.goto(live_server + PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("slack-state")).to_have_text("Not set")
    expect(page.get_by_label("Monitored channels")).to_have_value("")


def test_the_connection_test_reports_its_result_in_words(admin_page, live_server):
    _open(admin_page, live_server)
    page = admin_page
    page.get_by_role("button", name="Test connection").click()
    # No token typed and none stored: the answer says what to do.
    expect(page.get_by_test_id("slack-test-error")).to_contain_text("token")
    page.get_by_label("Bot token").fill(TOKEN)
    page.get_by_role("button", name="Test connection").click()
    # The token is made up, so Slack (or the network) refuses it: the result is a sentence, not a status code.
    expect(page.get_by_test_id("slack-test-error")).to_contain_text("Slack")
    expect(page.get_by_test_id("slack-test-error")).not_to_contain_text("undefined")


def test_it_reads_as_the_github_connector_page(admin_page, live_server):
    page = admin_page
    page.goto(live_server + "/admin/connectors/github", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    reference = page.locator("[data-testid=github-connect-form] [data-slot=card]").first.bounding_box()
    _open(page, live_server)
    card = page.locator("[data-testid=slack-form] [data-slot=card]").first.bounding_box()
    assert abs(card["width"] - reference["width"]) <= 1, (card, reference)
    assert abs(card["x"] - reference["x"]) <= 1, (card, reference)
