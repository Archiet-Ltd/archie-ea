"""An administrator turns the organisation's two-factor switch on; members of
that organisation are held to it and another organisation's members are not."""

import re

import pytest
from playwright.sync_api import expect

from .conftest import boot_live_server
from . import secswitch_support as sup

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

_STATE = {}


@pytest.fixture(scope="module")
def server(request, ai_protocol_stub, app):
    return boot_live_server(request, ai_protocol_stub, app, extra_env={"FLASK_CONFIG": "testing"})


@pytest.fixture(scope="module")
def world(app):
    return sup.seed(app)


@pytest.fixture
def page(browser, server):
    """Each test, and so each identity, gets its own browser context: no cookie
    or request in flight from one person can reach the next."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(30000)
    context.set_default_navigation_timeout(60000)
    yield context.new_page()
    context.close()


def test_01_before_the_switch_a_member_signs_in_with_the_password_alone(page, server, world):
    sup.sign_in(page, server, world["plain"])
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=60000)


def test_02_the_administrator_turns_two_factor_on_and_it_is_still_on_after_reload(page, server, world):
    sup.sign_in(page, server, world["admin"])
    sup.pass_code(page, world["admin_secret"])
    sup.open_security_tab(page, server)
    switch = page.locator("#two-factor")
    expect(switch).to_have_attribute("aria-checked", "false")
    switch.click()
    assert sup.save(page).status == 200
    page.reload()
    sup.open_security_tab(page, server)
    expect(page.locator("#two-factor")).to_have_attribute("aria-checked", "true")


def test_03_a_member_without_an_authenticator_is_sent_to_set_one_up(page, server, world):
    sup.sign_in(page, server, world["plain"])
    page.wait_for_url(re.compile(r".*/account/mfa-challenge.*"), timeout=60000)
    assert "Set up two-factor authentication" in page.locator("main").first.inner_text()
    expect(page.locator("img[alt*='QR code']")).to_be_visible()


def test_04_a_member_with_an_authenticator_is_asked_for_a_code(page, server, world):
    sup.sign_in(page, server, world["enrolled"])
    page.wait_for_url(re.compile(r".*/account/mfa-challenge.*"), timeout=60000)
    assert "Enter your verification code" in page.locator("main").first.inner_text()
    sup.pass_code(page, world["member_secret"])


def test_05_a_member_of_another_organisation_signs_in_with_the_password_alone(page, server, world):
    sup.sign_in(page, server, world["other"])
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=60000)
    assert "mfa" not in page.url
    # This member, and nobody else, is the person signed in.
    expect(page.locator("#user-menu-btn")).to_have_attribute("aria-label", re.compile(re.escape(world["other"])))
