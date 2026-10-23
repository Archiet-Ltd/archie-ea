"""An organisation administrator proving a sign-in domain, in a real browser: save the single
sign-on settings with a domain, see the record to publish and that the domain is not yet
verified, press Verify before publishing and be told what is missing; reload and see the same.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def test_the_administrator_sees_the_record_to_publish_and_is_told_it_is_missing(page, live_server, seeded):
    domain = "proof-%s.example" % uuid.uuid4().hex[:8]
    _login(page, live_server, seeded["emails"]["platform_admin"])
    page.goto(live_server + "/admin/sso", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email_domain", domain)
    page.fill("#client_id", "journey-client")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name="Save Configuration").click()

    row = page.locator('[data-testid="sso-domain-row"]', has_text=domain)
    expect(row).to_be_visible()
    expect(row.locator('[data-testid="sso-domain-status"]')).to_have_text("Not verified")
    expect(row).to_contain_text("_entelim-challenge." + domain)

    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        row.get_by_role("button", name="Verify").click()
    expect(page.get_by_text("No TXT record with the expected value was found").first).to_be_visible()

    page.goto(live_server + "/admin/sso", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    again = page.locator('[data-testid="sso-domain-row"]', has_text=domain)
    expect(again.locator('[data-testid="sso-domain-status"]')).to_have_text("Not verified")
