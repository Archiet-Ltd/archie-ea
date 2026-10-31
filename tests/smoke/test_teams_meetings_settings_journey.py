"""A platform administrator saves the Teams meeting settings, reloads and reads them back, and is told in words what is missing.

Starts from the goal: "have Entelim read our Teams meeting transcripts". The journey saves the notification address
and the two behaviours, reloads and reads them back, is refused in words for an address that is not https with the
typed values kept, and finds the subscribe control disabled with the reason beside it while the Microsoft 365
connector is not set up. The page must read as the same family as the GitHub connector page.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
NOTIFY = "https://entelim.example.com/api/webhooks/teams/notifications"
PAGE = "/admin/integrations/teams-meetings"


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
    expect(page.get_by_role("heading", level=1)).to_contain_text("Teams")


def test_every_setting_has_a_label_and_the_page_is_one_card(admin_page, live_server):
    _open(admin_page, live_server)
    page = admin_page
    expect(page.get_by_label("Notification URL")).to_be_visible()
    for name in ("Transcript analysis", "Create governance signals"):
        expect(page.get_by_role("switch", name=name)).to_be_visible()
    expect(page.locator("[data-testid=tm-form] button[type=submit]")).to_have_count(1)
    expect(page.get_by_test_id("tm-state")).to_have_text("Not subscribed")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_saved_settings_read_back_after_a_reload(admin_page, live_server):
    _open(admin_page, live_server)
    page = admin_page
    page.get_by_label("Notification URL").fill(NOTIFY)
    page.get_by_role("switch", name="Create governance signals").click()
    page.get_by_role("button", name="Save settings").click()
    expect(page.get_by_test_id("tm-saved")).to_contain_text("Teams Meetings configuration saved")

    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_label("Notification URL")).to_have_value(NOTIFY)
    expect(page.get_by_role("switch", name="Transcript analysis")).to_have_attribute("aria-checked", "true")
    expect(page.get_by_role("switch", name="Create governance signals")).to_have_attribute("aria-checked", "false")


def test_an_address_that_is_not_https_is_refused_in_words_and_kept(admin_page, live_server):
    _open(admin_page, live_server)
    page = admin_page
    page.get_by_label("Notification URL").fill("http://entelim.example.com/notify")
    page.get_by_role("button", name="Save settings").click()
    expect(page.get_by_test_id("tm-error-notification_url")).to_contain_text("https://")
    expect(page.get_by_label("Notification URL")).to_have_value("http://entelim.example.com/notify")
    page.goto(live_server + PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_label("Notification URL")).to_have_value("")


def test_subscribe_is_disabled_with_the_reason_while_microsoft_365_is_not_set_up(admin_page, live_server):
    _open(admin_page, live_server)
    page = admin_page
    expect(page.get_by_role("button", name="Subscribe to call records")).to_be_disabled()
    notice = page.get_by_test_id("tm-m365-missing")
    expect(notice).to_contain_text("Microsoft 365 connector")
    link = notice.get_by_role("link", name="Set up Microsoft 365")
    link.click()
    page.wait_for_load_state("domcontentloaded")
    # The link lands on the connectors page, which has content.
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    assert "/admin/connectors" in page.url


def test_it_reads_as_the_github_connector_page(admin_page, live_server):
    page = admin_page
    page.goto(live_server + "/admin/connectors/github", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    reference = page.locator("[data-testid=github-connect-form] [data-slot=card]").first.bounding_box()
    _open(page, live_server)
    card = page.locator("[data-testid=tm-form] [data-slot=card]").first.bounding_box()
    assert abs(card["width"] - reference["width"]) <= 1, (card, reference)
    assert abs(card["x"] - reference["x"]) <= 1, (card, reference)
