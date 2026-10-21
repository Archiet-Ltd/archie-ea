"""Error aggregation: a real client-side JS error is reported, deduplicated,
and shows up on the platform-admin Errors dashboard -- walked through as a
platform admin would actually use it.

Built 10 Sep 2026 answering the owner's question "how do we know when the
system has silently degraded" -- Archie had zero error-tracking before this
(verified: no sentry_sdk/SENTRY_DSN/glitchtip anywhere in the tree). This is
the self-hosted, in-built answer: every WARNING+ server log record and every
uncaught client-side JS error is deduplicated by fingerprint into
``error_events`` and surfaced at /admin/errors.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD, all_modules_link

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
def test_client_js_error_is_captured_deduplicated_and_visible_to_admin(browser, live_server, seeded):
    marker = "smoke-induced-error-%s" % uuid.uuid4().hex[:8]
    admin_email = seeded["emails"]["platform_admin"]

    # A real end user hits a page and a JS error fires -- not a curl to the
    # sink, an actual uncaught exception in a real browser tab.
    user_email = seeded["emails"]["solution_architect"]
    user_ctx = browser.new_context(ignore_https_errors=True)
    user_page = user_ctx.new_page()
    _login(user_page, live_server, user_email)
    user_page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # Throw the same error from two separate page loads (a fresh reporter's
    # in-memory de-dup cache each time, like two different real sessions
    # hitting the same bug) -- the second must increment occurrence_count to
    # 2 server-side, not create a second row, proving the fingerprint/
    # aggregation logic actually runs rather than just the client-side guard
    # against a single tight error loop.
    for _ in range(2):
        user_page.evaluate("(m) => { setTimeout(() => { throw new Error(m); }, 0); }", marker)
        user_page.wait_for_timeout(500)
        user_page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # The reporter POSTs via fetch with keepalive; give it a moment to land.
    user_page.wait_for_timeout(1500)
    user_ctx.close()

    admin_ctx = browser.new_context(ignore_https_errors=True)
    admin_page = admin_ctx.new_page()
    _login(admin_page, live_server, admin_email)

    # Found from the sidebar, not a typed URL.
    admin_page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    sidebar_link = all_modules_link(admin_page, "Errors")
    expect(sidebar_link).to_be_visible(timeout=PAGE_TIMEOUT)
    sidebar_link.click()
    admin_page.wait_for_url(re.compile(r"/admin/errors"), timeout=PAGE_TIMEOUT)

    row = admin_page.locator("tr", has_text=marker)
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(row).to_contain_text("client")
    # Deduplicated: two identical throws must read as one row with count 2,
    # not two rows -- this is the whole point of fingerprinting.
    expect(row).to_contain_text(re.compile(r"\b2\b"))

    # Resolve it -- the other half of the admin's real workflow, not just viewing.
    resolve_btn = row.get_by_role("button", name=re.compile("Resolve", re.I))
    resolve_btn.click()
    admin_page.wait_for_url(re.compile(r"/admin/errors"), timeout=PAGE_TIMEOUT)
    expect(admin_page.locator("tr", has_text=marker)).to_have_count(0)

    # And it is still there under "Show resolved" -- resolved, not deleted.
    admin_page.goto(live_server + "/admin/errors?resolved=1", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    resolved_row = admin_page.locator("tr", has_text=marker)
    expect(resolved_row).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(resolved_row).to_contain_text("Resolved")


@pytest.mark.smoke
def test_errors_dashboard_is_blocked_for_non_platform_admin(browser, live_server, seeded):
    email = seeded["emails"]["solution_architect"]
    ctx = browser.new_context(ignore_https_errors=True)
    page = ctx.new_page()
    _login(page, live_server, email)
    resp = page.goto(live_server + "/admin/errors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert resp.status == 403
