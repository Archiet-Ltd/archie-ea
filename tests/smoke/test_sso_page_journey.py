"""A platform administrator sets up single sign-on and sees that it saved.

* Every field has a label a person can find it by.
* Saved settings are still there after a reload, the stored client secret is
  acknowledged in words and never written back onto the page.
* A setting the platform refuses is refused in words and the form keeps what
  the administrator typed.
* The test sign-in control is offered once a configuration exists.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SECRET = "journey-client-secret-7731"
SAVE = re.compile("save", re.I)


def _open(browser, live_server, seeded):
    page = browser.new_page()
    _login(page, live_server, seeded["emails"]["platform_admin"])
    page.goto(live_server + "/admin/sso", timeout=PAGE_TIMEOUT)
    return page


def test_saved_single_sign_on_settings_survive_a_reload(browser, live_server, seeded):
    page = _open(browser, live_server, seeded)
    try:
        domain = "journey-%s.example.com" % uuid.uuid4().hex[:8]
        page.get_by_label("Protocol").select_option("oidc")
        page.get_by_label("Email Domain").fill(domain)
        page.get_by_label("IdP Discovery URL").fill("https://example.com/.well-known/openid-configuration")
        page.get_by_label("Client ID").fill("journey-client-id")
        page.get_by_label("Client Secret").fill(SECRET)
        page.get_by_role("button", name=SAVE).first.click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("SSO configuration saved", timeout=PAGE_TIMEOUT)

        page.reload(timeout=PAGE_TIMEOUT)
        assert page.get_by_label("Email Domain").input_value() == domain
        assert page.get_by_label("Client ID").input_value() == "journey-client-id"
        expect(page.locator("main")).to_contain_text("Secret stored")
        assert SECRET not in page.content(), "the client secret was written back onto the page"
        assert page.get_by_label("Client Secret").input_value() == ""
        # The test sign-in control is offered once a configuration exists.
        expect(page.get_by_role("link", name=re.compile("run test sign-in", re.I))).to_be_visible()
    finally:
        page.close()


def test_a_refused_setting_is_refused_in_words_and_kept(browser, live_server, seeded):
    page = _open(browser, live_server, seeded)
    try:
        domain = "kept-%s.example.com" % uuid.uuid4().hex[:8]
        page.get_by_label("Protocol").select_option("saml")
        page.get_by_label("Email Domain").fill(domain)
        page.get_by_label("Enable SSO for this organisation").click()
        page.get_by_role("button", name=SAVE).first.click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("needs an IdP SSO URL and signing certificate", timeout=PAGE_TIMEOUT)
        assert page.get_by_label("Email Domain").input_value() == domain
        assert page.get_by_label("Protocol").input_value() == "saml"
    finally:
        page.close()
