"""The Security card shows only switches that do something."""

import pytest
from playwright.sync_api import expect

from .conftest import boot_live_server
from . import secswitch_support as sup

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def server(request, ai_protocol_stub, app):
    return boot_live_server(request, ai_protocol_stub, app, extra_env={"FLASK_CONFIG": "testing"})


def test_the_security_card_shows_three_switches_and_no_encryption_claim(browser, server, app):
    world = sup.seed(app)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(30000)
    page = context.new_page()
    sup.sign_in(page, server, world["admin"])
    sup.pass_code(page, world["admin_secret"])
    sup.open_security_tab(page, server)
    card = page.get_by_role("tabpanel", name="Security")
    expect(card.get_by_role("switch")).to_have_count(3)
    for label in ("Two-factor sign-in", "Allowed addresses only", "Record page use"):
        expect(card.get_by_role("switch", name=label)).to_be_visible()
    text = card.inner_text()
    assert "Encrypt sensitive data" not in text
    assert page.locator("#data-encryption").count() == 0
    # Nothing the page saves for the removed switch comes back.
    saved = page.evaluate("fetch('/api/system-settings').then(r => r.json())")
    assert "data-encryption" not in saved["settings"]
    context.close()
