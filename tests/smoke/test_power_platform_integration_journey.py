"""Save -> reload -> persist journey for the Power Platform CoE integration
settings page.

Wave 4 gap analysis: `integrations` had zero smoke coverage. This surface is
reachable from platform_admin's Admin zone ("Power Platform",
`admin.power_platform_integration`, app/utils/role_access.py) and persists to
the real `api_settings` table (`app/modules/admin/routes/admin_routes.py`,
`power_platform_save_credentials` — see `_pp_settings_row()` /
`_PP_PROVIDER = "power_platform_coe"`), not a stub.

Route: GET/POST `/admin/integrations/power-platform`,
POST `/admin/integrations/power-platform/save`. The secret field
(`client_secret`) is intentionally never echoed back by the GET route (only
`configured: bool(row.jira_url)` is returned), so persistence is asserted on
the two non-secret fields that ARE round-tripped: tenant_id and env_url.
"""

import time

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD, _delete_api_settings

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
@pytest.mark.journey
def test_power_platform_credentials_save_and_persist(browser, live_server, seeded, request):
    """A platform admin opens the Power Platform integration page, saves
    tenant credentials through the real form and Save button, then reloads
    the page cold and confirms the non-secret fields survived."""
    context = browser.new_context(ignore_https_errors=True)
    page = context.new_page()
    page.on("dialog", lambda d: d.accept())
    try:
        org_id = seeded["ids"]["org"]

        def _remove_power_platform_provider():
            _delete_api_settings(
                provider="power_platform_coe", key_label="default",
                organization_id=org_id)

        request.addfinalizer(_remove_power_platform_provider)

        _login(page, live_server, seeded["emails"]["platform_admin"])

        tenant_id = "smoke-tenant-%d" % int(time.time() * 1000)
        env_url = "https://smoke-%d.crm.dynamics.com" % int(time.time() * 1000)
        page_url = live_server + "/admin/integrations/power-platform"

        page.goto(page_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        page.fill("#tenant_id", tenant_id)
        page.fill("#client_id", "smoke-client-id")
        page.fill("#client_secret", "smoke-secret-value")
        page.fill("#env_url", env_url)

        with page.expect_response(
            lambda r: "/admin/integrations/power-platform/save" in r.url
            and r.request.method == "POST",
            timeout=PAGE_TIMEOUT,
        ) as saved:
            page.locator("#btn-save").click()
        assert saved.value.status < 400, f"save POST failed: {saved.value.status}"
        assert saved.value.json().get("success") is True

        # ---- PERSISTENCE: fresh cold load of the page --------------------------
        page.goto(page_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("#tenant_id")).to_have_value(tenant_id, timeout=PAGE_TIMEOUT)
        expect(page.locator("#env_url")).to_have_value(env_url, timeout=PAGE_TIMEOUT)
    finally:
        context.close()
