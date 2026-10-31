"""A platform administrator saves the ServiceNow connector, reads it back, and the secret is never echoed.

Starts from the goal: "connect our ServiceNow CMDB and be sure it saved". Two organisations: one that has
delegated connector changes (the save is applied and reads back after a reload) and one that has not (the save is
queued for approval and says so). A wrong address and a malformed field mapping are refused in words with the typed
values kept; the status and sync controls answer in sentences. The page must read as the same family as the GitHub
connector page.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
INSTANCE = "https://journey.service-now.com"
CLIENT_ID = "journey-client-id-0001"
SECRET = "journey-servicenow-secret-0001"
FILTER = "operational_status=1^install_status=1"
PAGE = "/admin/connectors/servicenow"


def _delegate(org_id):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.organization import Organization

        org = Organization.query.filter_by(id=org_id).one()
        settings = dict(org.settings or {})
        settings["connector_action_delegation"] = {"create": True, "update": True}
        org.settings = settings
        db.session.commit()
        db.session.remove()


def _signed_in(browser, live_server, delegated):
    org = create_fresh_org("solution_architect", org_admin=True, platform_role="Administrator")
    if delegated:
        _delegate(org["org_id"])
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    page = context.new_page()
    sign_in(page, live_server, org["emails"]["solution_architect"])
    return context, page


@pytest.fixture()
def admin_page(browser, live_server):
    context, page = _signed_in(browser, live_server, delegated=False)
    yield page
    context.close()


@pytest.fixture()
def delegating_admin_page(browser, live_server):
    context, page = _signed_in(browser, live_server, delegated=True)
    yield page
    context.close()


def _open(page, live_server):
    page.goto(live_server + PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", level=1)).to_contain_text("ServiceNow")


def _fill_all(page):
    page.get_by_label("Instance URL").fill(INSTANCE)
    page.get_by_label("Client ID").fill(CLIENT_ID)
    page.get_by_label("Client secret").fill(SECRET)
    page.get_by_label("CI query filter").fill(FILTER)
    page.get_by_label("Field mapping overrides").fill('{"name": "name"}')


def test_every_setting_has_a_label_and_the_page_is_one_family(admin_page, live_server):
    _open(admin_page, live_server)
    page = admin_page
    for label in ("Instance URL", "Client ID", "Client secret", "CI query filter", "Field mapping overrides"):
        expect(page.get_by_label(label, exact=False).first).to_be_visible()
    expect(page.get_by_role("switch", name="Enable connector")).to_be_visible()
    expect(page.locator("[data-testid=sn-form] button[type=submit]")).to_have_count(1)
    expect(page.get_by_test_id("sn-state")).to_have_text("Not set")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_a_saved_connector_reads_back_and_the_secret_is_never_echoed(delegating_admin_page, live_server):
    _open(delegating_admin_page, live_server)
    page = delegating_admin_page
    _fill_all(page)
    page.get_by_role("switch", name="Enable connector").click()
    page.get_by_role("button", name="Save configuration").click()
    expect(page.get_by_test_id("sn-saved")).to_contain_text("ServiceNow connector configuration saved")

    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_test_id("sn-state")).to_have_text("Connected")
    expect(page.get_by_label("Instance URL")).to_have_value(INSTANCE)
    expect(page.get_by_label("Client ID")).to_have_value(CLIENT_ID)
    expect(page.get_by_label("CI query filter")).to_have_value(FILTER)
    expect(page.get_by_label("Field mapping overrides")).to_have_value('{\n  "name": "name"\n}')
    expect(page.get_by_role("switch", name="Enable connector")).to_have_attribute("aria-checked", "true")
    assert SECRET not in page.content()
    expect(page.get_by_label("Client secret")).to_have_value("")
    expect(page.get_by_label("Client secret")).to_have_attribute("placeholder", "Leave blank to keep the existing value")


def test_the_status_control_answers_in_a_sentence(delegating_admin_page, live_server):
    _open(delegating_admin_page, live_server)
    page = delegating_admin_page
    page.get_by_role("button", name="Check status").click()
    expect(page.get_by_test_id("sn-result")).to_contain_text("Not set up yet")
    _fill_all(page)
    page.get_by_role("switch", name="Enable connector").click()
    page.get_by_role("button", name="Save configuration").click()
    expect(page.get_by_test_id("sn-saved")).to_be_visible()
    page.get_by_role("button", name="Check status").click()
    expect(page.get_by_test_id("sn-result")).to_contain_text("Enabled")
    expect(page.get_by_test_id("sn-result")).to_contain_text("never synced")


def test_a_change_that_needs_approval_says_so(admin_page, live_server):
    _open(admin_page, live_server)
    page = admin_page
    _fill_all(page)
    page.get_by_role("button", name="Save configuration").click()
    expect(page.get_by_test_id("sn-saved")).to_contain_text("queued for approval")
    # Nothing is live until it is approved, and the page does not pretend otherwise.
    expect(page.get_by_test_id("sn-state")).to_have_text("Not set")
    assert SECRET not in page.content()


def test_wrong_values_are_refused_in_words_and_the_typed_values_are_kept(admin_page, live_server):
    _open(admin_page, live_server)
    page = admin_page
    page.get_by_label("Instance URL").fill("ftp://journey.service-now.com")
    page.get_by_label("Client ID").fill(CLIENT_ID)
    page.get_by_label("Field mapping overrides").fill("{not json")
    page.get_by_role("button", name="Save configuration").click()
    expect(page.get_by_test_id("sn-error-instance_url")).to_contain_text("https")
    expect(page.get_by_test_id("sn-error-field_mapping")).to_contain_text("JSON object")
    expect(page.get_by_label("Instance URL")).to_have_value("ftp://journey.service-now.com")
    expect(page.get_by_label("Client ID")).to_have_value(CLIENT_ID)
    expect(page.get_by_label("Field mapping overrides")).to_have_value("{not json")
    # Nothing was proposed or stored.
    page.goto(live_server + PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_label("Instance URL")).to_have_value("")


def test_sync_reports_what_happened_in_words(delegating_admin_page, live_server):
    _open(delegating_admin_page, live_server)
    page = delegating_admin_page
    page.get_by_role("button", name="Sync now").click()
    expect(page.get_by_test_id("sn-result")).to_contain_text("Save the connector before syncing")
    _fill_all(page)
    page.get_by_role("switch", name="Enable connector").click()
    page.get_by_role("button", name="Save configuration").click()
    expect(page.get_by_test_id("sn-saved")).to_be_visible()
    page.get_by_role("button", name="Sync now").click()
    # This organisation has not delegated sync, so the run is queued for approval and the answer says that.
    expect(page.get_by_test_id("sn-result")).to_contain_text("queued for approval")
    expect(page.get_by_test_id("sn-result")).not_to_contain_text("undefined")


def test_it_reads_as_the_github_connector_page(admin_page, live_server):
    page = admin_page
    page.goto(live_server + "/admin/connectors/github", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    reference = page.locator("[data-testid=github-connect-form] [data-slot=card]").first.bounding_box()
    _open(page, live_server)
    card = page.locator("[data-testid=sn-form] [data-slot=card]").first.bounding_box()
    assert abs(card["width"] - reference["width"]) <= 1, (card, reference)
    assert abs(card["x"] - reference["x"]) <= 1, (card, reference)
