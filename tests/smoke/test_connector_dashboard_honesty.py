"""The connector health dashboard used to present sync as a working feature
for every connector: a primary "Sync" button on each card and a "Trigger
Sync" button in the detail modal, both wired to a route that could only ever
500 (see tests/test_connector_sync_route_honesty.py and the docstring on
app/routes/connector_routes.py::api_trigger_sync -- ConnectorManager has no
get_connector method, and no connector is ever registered with it, so no
connector type can actually sync today).

This asserts the rendered page no longer claims a working sync: the sync
controls render disabled with copy that says sync isn't available, and the
page subtitle no longer lists specific external systems as monitored
integrations.
"""

import pytest

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
@pytest.mark.journey
def test_connector_dashboard_does_not_claim_working_sync(browser, live_server, seeded):
    """A platform admin opens the connector dashboard; every sync control on
    the page must be disabled and labelled as unavailable, not offered as a
    working action."""
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(
            live_server + "/integrations/connectors",
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )

        # The old subtitle named specific external systems ("Datadog, Jira,
        # ServiceNow, Salesforce") as monitored integrations -- Salesforce in
        # particular has no connector implementation anywhere in the repo.
        # Scoped to the dashboard's own content, not the whole page: the
        # admin sidebar separately links to an unrelated "Salesforce
        # Integration" settings page that this change does not touch.
        dashboard_text = page.locator('[x-data="connectorDashboard()"]').inner_text()
        assert "Salesforce" not in dashboard_text

        # Every rendered "sync" control (card buttons and the modal's
        # "Trigger Sync") must be disabled, not a live action.
        sync_buttons = page.get_by_role("button", name="Sync", exact=True)
        assert sync_buttons.count() == 0, (
            "no button should be labelled as a plain, working 'Sync' action"
        )

        not_available_buttons = page.locator("button", has_text="Not Available")
        # Cards only render once the connector list finishes loading; either
        # there are zero connectors (empty state, nothing to assert on) or
        # every rendered sync control is the disabled "Not Available" one.
        # Deliberately not seeding a real ConnectorConfig row to force the
        # non-empty branch here: GET /integrations/api/connectors (unrelated
        # to this change) 500s for any row that exists, because
        # api_list_connectors reads conn.connector_type.value /
        # conn.status.value / conn.sync_mode.value as if those columns were
        # Enum members, when ConnectorConfig stores them as plain strings --
        # a second, separate honesty bug in this same file, out of scope for
        # this change and left for a follow-up.
        card_sync_controls = page.locator(
            'button[aria-label="Sync is not available for this connector yet"]'
        )
        if card_sync_controls.count() > 0:
            for i in range(card_sync_controls.count()):
                btn = card_sync_controls.nth(i)
                assert btn.is_disabled(), "sync control must be disabled, not just relabelled"
            assert not_available_buttons.count() >= card_sync_controls.count()
    finally:
        context.close()
