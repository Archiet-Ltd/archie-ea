"""An organisation's owner who opens Settings lands on their organisation's settings.

A member without administration rights is told, in plain words, which people in
their own organisation can help, and sees nobody from another organisation.
"""
import os
import pathlib
import tempfile

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("SETTINGS_WALK_DIR") or tempfile.gettempdir()) / "settings-owner-walk"
VIEWS = ((1440, 900, "light"), (390, 844, "dark"))


def _open(browser, base, email, width, height, scheme):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, base, email)
    return context, page


def _shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))), full_page=True)


@pytest.mark.parametrize("width,height,scheme", VIEWS)
def test_the_owner_opening_settings_lands_on_their_organisation_page(browser, live_server, width, height, scheme):
    org = create_fresh_org("solution_architect", org_admin=True)
    context, page = _open(browser, live_server, org["emails"]["solution_architect"], width, height, scheme)
    try:
        page.goto(live_server + "/settings", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.url.rstrip("/").endswith("/admin"), page.url
        body = page.locator("main").first
        expect(body).to_contain_text("Organisation", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("You don't have access to this page")).to_have_count(0)
        assert len(body.inner_text().strip()) > 200
        _shot(page, "owner", width, scheme)
    finally:
        context.close()


@pytest.mark.parametrize("width,height,scheme", VIEWS)
def test_a_member_is_told_which_people_in_their_organisation_can_help(browser, live_server, width, height, scheme):
    mine = create_fresh_org("solution_architect", org_admin=True, extra_roles=("business_architect",))
    other = create_fresh_org("enterprise_architect", org_admin=True)
    context, page = _open(browser, live_server, mine["emails"]["business_architect"], width, height, scheme)
    try:
        page.goto(live_server + "/settings", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="You don't have access to this page")).to_be_visible(timeout=PAGE_TIMEOUT)
        text = page.locator("main").first.inner_text()
        assert "Ask Launch solution_arc to give you access" in text, text
        assert "Launch enterprise_" not in text, text
        _shot(page, "member", width, scheme)
        link = page.get_by_role("link", name="Go to the dashboard")
        href = link.get_attribute("href")
        page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert len(page.locator("main").first.inner_text().strip()) > 100
        assert other["org_id"] != mine["org_id"]
    finally:
        context.close()
