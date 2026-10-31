"""A visitor on the public site while only the first wave is released.

The site must not offer single sign-on, or buying, changing and cancelling a plan, that the product does
not yet deliver: the visitor sees plain wording that these are coming, keeps every plan and price, and
the one button each paid plan offers (Start free) lands on the registration form.
"""
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("SMOKE_WALK_DIR")


@pytest.fixture(scope="module")
def wave_one_server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": "1"})


def _shot(page, name, width):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(WALK) / f"{name}-{width}.png"), full_page=True)


@pytest.mark.parametrize("width", [1440, 390])
def test_visitor_sees_what_is_coming_and_start_free_lands_on_registration(browser, wave_one_server, width):
    base = str(wave_one_server)
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    try:
        page.goto(base + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _shot(page, "pricing", width)
        team = page.get_by_test_id("plan-card-team")
        expect(team).to_contain_text("USD 290", timeout=PAGE_TIMEOUT)
        expect(team).to_contain_text("Plans open for purchase soon")
        expect(team).not_to_contain_text("Single sign-on")
        body = page.locator("body")
        expect(body).not_to_contain_text("Buy Team")
        expect(page.get_by_test_id("plan-card-enterprise")).not_to_contain_text("SAML")
        expect(page.get_by_role("row", name=re.compile("Single sign-on"))).to_contain_text("Coming")

        team.get_by_test_id("buy-team").click()
        page.wait_for_load_state("domcontentloaded")
        assert "/register" in page.url or "/signup" in page.url or "/account" in page.url, page.url
        expect(page.locator("form input[type=email], form input[name=email]").first).to_be_visible(timeout=PAGE_TIMEOUT)

        page.goto(base + "/features", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _shot(page, "features", width)
        expect(page.locator("main")).to_contain_text("Single sign-on is coming")
        expect(page.locator("main")).not_to_contain_text("SAML or OpenID Connect")
        expect(page.locator("main")).not_to_contain_text("buy, change and cancel")

        page.goto(base + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _shot(page, "home", width)
        expect(page.locator("main")).to_contain_text("Single sign-on is coming")
        expect(page.locator("main")).not_to_contain_text("SAML,")
    finally:
        context.close()
