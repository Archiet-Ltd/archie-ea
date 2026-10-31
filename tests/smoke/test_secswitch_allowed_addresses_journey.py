"""An administrator limits sign-in to a listed address; other addresses are
refused with a plain sentence and recorded; the switch cannot be saved on with
an empty list; another organisation is unaffected."""

import re

import pytest
from playwright.sync_api import expect

from .conftest import boot_live_server
from . import secswitch_support as sup

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

LISTED = "203.0.113.7"
OUTSIDE = "198.51.100.9"
SENTENCE = "Sign-in from this address is not allowed by your organisation. Ask your administrator to add it."


@pytest.fixture(scope="module")
def server(request, ai_protocol_stub, app):
    return boot_live_server(request, ai_protocol_stub, app, extra_env={"FLASK_CONFIG": "testing", "TRUST_PROXY": "1"})


@pytest.fixture(scope="module")
def world(app):
    return sup.seed(app)


def _context(browser, address):
    context = browser.new_context(viewport={"width": 1440, "height": 900},
                                  extra_http_headers={"X-Forwarded-For": address})
    context.set_default_timeout(30000)
    context.set_default_navigation_timeout(60000)
    return context


def test_01_an_empty_list_cannot_be_switched_on_and_says_why(browser, server, world):
    context = _context(browser, LISTED)
    page = context.new_page()
    sup.sign_in(page, server, world["admin"])
    sup.pass_code(page, world["admin_secret"])
    sup.open_security_tab(page, server)
    page.locator("#ip-whitelist").click()
    assert sup.save(page).status == 400
    expect(page.get_by_test_id("ip-allowlist-error")).to_contain_text("Add at least one address")
    page.reload()
    sup.open_security_tab(page, server)
    expect(page.locator("#ip-whitelist")).to_have_attribute("aria-checked", "false")
    context.close()


def test_02_the_administrator_turns_it_on_with_one_address_and_it_persists(browser, server, world):
    context = _context(browser, LISTED)
    page = context.new_page()
    sup.sign_in(page, server, world["admin"])
    sup.pass_code(page, world["admin_secret"])
    sup.open_security_tab(page, server)
    page.locator("#ip-allowlist").fill("not an address")
    page.locator("#ip-whitelist").click()
    assert sup.save(page).status == 400
    expect(page.get_by_test_id("ip-allowlist-error")).to_contain_text("not valid addresses")
    page.locator("#ip-allowlist").fill(LISTED)
    assert sup.save(page).status == 200
    page.reload()
    sup.open_security_tab(page, server)
    expect(page.locator("#ip-whitelist")).to_have_attribute("aria-checked", "true")
    expect(page.locator("#ip-allowlist")).to_have_value(LISTED)
    context.close()


def test_03_a_sign_in_from_another_address_is_refused_with_the_sentence_and_recorded(browser, server, world, app):
    context = _context(browser, OUTSIDE)
    page = context.new_page()
    sup.sign_in(page, server, world["plain"])
    assert "/dashboard" not in page.url
    assert SENTENCE in page.locator("body").inner_text()
    from app import db

    with app.app_context():
        count = db.session.execute(
            db.text("SELECT count(*) FROM soc2_audit_log WHERE action = 'login_failed' "
                    "AND new_value::text LIKE :e AND new_value::text LIKE :ip"),
            {"e": "%" + world["plain"] + "%", "ip": "%" + OUTSIDE + "%"},
        ).scalar()
    assert count >= 1, "the refused sign-in is recorded like a failed one"
    context.close()


def test_04_a_sign_in_from_the_listed_address_succeeds(browser, server, world):
    context = _context(browser, LISTED)
    page = context.new_page()
    sup.sign_in(page, server, world["plain"])
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=60000)
    context.close()


def test_05_another_organisation_is_not_limited_by_this_organisations_list(browser, server, world):
    context = _context(browser, OUTSIDE)
    page = context.new_page()
    sup.sign_in(page, server, world["other"])
    page.wait_for_url(re.compile(r".*/dashboard.*"), timeout=60000)
    context.close()


def test_06_a_wrong_password_from_an_unlisted_address_gets_the_same_sentence(browser, server, world):
    context = _context(browser, OUTSIDE)
    page = context.new_page()
    page.goto(server + "/account/login", wait_until="domcontentloaded")
    page.fill("#email", world["plain"])
    page.fill("#password", "not-the-password")
    page.locator("#submit").click()
    page.wait_for_load_state("domcontentloaded")
    assert "/dashboard" not in page.url
    assert SENTENCE in page.locator("body").inner_text()
    context.close()
