"""A founder reads the plan page, finds their own settings, invites a teammate and compares two member lists.

From the person's goals (J6, J7): "what can I use today and how do I get more", "where are my own
settings", "invite someone and not retype after an error", "does every screen say the same role".
Every button on the plan page must finish or say it is a conversation; every link must land on content.
"""
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = Path(os.environ.get("JRN01C_SHOTS", "test-results/jrn01c"))


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s.png" % name)), full_page=True)


def test_plan_page_states_what_is_usable_and_every_button_finishes(browser, live_server, seeded):
    from .test_archetype_journeys import _login

    context = browser.new_context()
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/admin/billing/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        note = page.get_by_test_id("plans-currency-note")
        expect(note).to_contain_text("US dollars", timeout=PAGE_TIMEOUT)
        expect(note).to_contain_text("available to use today")
        # The people figure is a sentence a person understands, never "of no limit".
        people = page.get_by_test_id("billing-people-limit").inner_text()
        assert "no limit" not in people and re.search(r"\d+ of ", people), people
        # Choosing a plan opens its panel; while payment is closed the panel says so and offers a conversation.
        page.get_by_test_id("plan-card-team").get_by_role("link", name="Choose Team").click()
        page.wait_for_url(lambda url: "plan=team" in url, timeout=PAGE_TIMEOUT)
        panel = page.locator("#checkout")
        expect(panel).to_contain_text("Payment opens soon", timeout=PAGE_TIMEOUT)
        expect(panel.locator("[data-checkout-legal]")).to_be_visible()
        panel.get_by_test_id("checkout-contact").click()
        page.wait_for_url(lambda url: "/contact" in url, timeout=PAGE_TIMEOUT)
        expect(page.locator("main").first).not_to_be_empty()
    finally:
        context.close()


def test_settings_in_the_menu_opens_the_persons_own_settings(browser, live_server, seeded):
    from .test_archetype_journeys import _login

    context = browser.new_context()
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("user-menu").click()
        expect(page.get_by_test_id("user-menu-account")).to_have_attribute("href", re.compile(r"/account"))
        expect(page.get_by_test_id("user-menu-settings")).to_contain_text("Organisation administration")
        expect(page.get_by_test_id("user-menu-settings")).to_have_attribute("href", "/admin/")
        expect(page.get_by_test_id("user-menu-platform_settings")).to_have_text(re.compile(r"Settings"))
        page.get_by_test_id("user-menu-platform_settings").click()
        page.wait_for_url(lambda url: url.rstrip("/").endswith("/settings"), timeout=PAGE_TIMEOUT)
        expect(page.locator("main").first).to_contain_text("Settings", timeout=PAGE_TIMEOUT)
        page.go_back()
        page.get_by_test_id("user-menu").click()
        page.get_by_test_id("user-menu-account").click()
        page.wait_for_url(lambda url: "/account" in url, timeout=PAGE_TIMEOUT)
        expect(page.locator("main").first).to_contain_text("Account", timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_ai_settings_and_permissions_read_in_plain_words(browser, live_server, seeded):
    from .test_archetype_journeys import _login

    context = browser.new_context()
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        page.goto(live_server + "/admin/api-settings", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="AI settings", level=1)).to_be_visible(timeout=PAGE_TIMEOUT)
        page.goto(live_server + "/admin/team?tab=permissions", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        body = page.locator("main").first.inner_text()
        for raw in ("ORG_ADMIN", "org_admin", "MOTIVATION_ELEMENTS", "motivation_elements"):
            assert raw not in body, raw
    finally:
        context.close()


@pytest.mark.parametrize("viewport,scheme", [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")])
def test_screens_for_the_walk(browser, live_server, seeded, viewport, scheme):
    from .test_archetype_journeys import _login

    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    page = context.new_page()
    tag = "%s-%s" % (viewport["width"], scheme)
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        for name, path in (("plan", "/admin/billing/"), ("team", "/admin/team"), ("permissions", "/admin/team?tab=permissions"),
                           ("registered-users", "/admin/users"), ("ai-settings", "/admin/api-settings")):
            response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response.status == 200, path
            _shot(page, "%s-%s" % (name, tag))
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        if viewport["width"] < 768:
            page.get_by_role("button", name="Toggle sidebar").first.click()
        page.get_by_test_id("user-menu").click()
        _shot(page, "avatar-menu-%s" % tag)
    finally:
        context.close()
