"""A member who is not an administrator clicks Buy and learns who in their organisation can buy the plan.

Goal: "I picked a plan on the pricing page; how do I get it bought?" A signed-in CTO has no billing authority, so the
page they land on names the organisation's administrator, links to email them, and says why: an administrator buys
and changes plans. The same journey as the administrator opens the plan's checkout step instead.
"""
import os
import pathlib
import tempfile

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("BUY_WALK_DIR") or tempfile.gettempdir()) / "buy-walk"
SIZES = [(1440, 900, "light"), (390, 844, "dark")]


def _shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))), full_page=True)


def test_a_member_choosing_a_plan_is_told_which_administrator_buys_it(browser, live_server, seeded):
    admin_email = seeded["emails"]["platform_admin"]
    for width, height, scheme in SIZES:
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
        page = context.new_page()
        try:
            _login(page, live_server, seeded["emails"]["cto"])
            page.goto(live_server + "/account/register?plan=team&interval=year", wait_until="domcontentloaded",
                      timeout=PAGE_TIMEOUT)
            expect(page.get_by_role("heading", level=1)).to_have_text(
                "An administrator of your organisation buys the plan", timeout=PAGE_TIMEOUT)
            body = page.inner_text("body")
            assert "Plans are bought and changed by an administrator" in body
            assert admin_email in body, "the page does not name the organisation's administrator"
            assert "Your role does not include this page" not in body
            reach = page.get_by_role("link", name="Email Smoke")
            expect(reach).to_have_attribute("href", "mailto:" + admin_email)
            _shot(page, "member-buy", width, scheme)
        finally:
            context.close()


def test_the_administrator_choosing_a_plan_reaches_its_checkout_step(browser, live_server, seeded):
    context = browser.new_context()
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/account/register?plan=team&interval=year", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        page.wait_for_url(lambda url: "/admin/billing" in url and "plan=team" in url, timeout=PAGE_TIMEOUT)
        expect(page.locator("#checkout").get_by_role("heading", name="Buy Team")).to_be_visible(
            timeout=PAGE_TIMEOUT)
        assert "buys the plan" not in page.inner_text("body")
    finally:
        context.close()
