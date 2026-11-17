"""A visitor and a signed-in architect move through the public site.

Written from what each needs. A visitor sees Start free and Sign in, and the Roles
page offers one card per role page. An architect who opens the address of the site
lands in their product home; on any public page they see one button, Open Entelim,
and their account menu leads to the same home, never to administration. The role
named below is read from the database in each journey, so a journey that would only
pass as an administrator fails.
"""

import pytest
from playwright.sync_api import expect

from .chat_ask import role_of, sign_in
from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}


def _open(browser, viewport):
    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    return context, context.new_page()


def test_anonymous_visitor_sees_start_free_and_sign_in_and_the_roles_cards(browser, live_server):
    context, page = _open(browser, DESKTOP)
    try:
        page.goto(live_server + "/roles", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        header = page.locator("header")
        expect(header.get_by_role("link", name="Start free").first).to_be_visible()
        expect(header.get_by_role("link", name="Sign in")).to_be_visible()
        assert header.get_by_role("link", name="Open Entelim").count() == 0
        expect(page.get_by_role("heading", level=1)).to_have_text("Entelim for your role")
        cards = page.locator("[data-role-card]")
        assert cards.count() >= 10
        assert "startup" in cards.first.inner_text().lower()
        # nothing is open over the heading
        for menu in page.locator("[data-menu-panel]").all():
            expect(menu).to_be_hidden()
        # the first card leads to a page with content
        cards.first.click()
        expect(page).to_have_url(live_server + "/roles/startup-founder")
        assert len(page.locator("main").inner_text().split()) > 150
    finally:
        context.close()


def test_roles_menu_lists_only_role_pages_and_closes_when_a_link_is_followed(browser, live_server):
    context, page = _open(browser, DESKTOP)
    try:
        page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator("header").get_by_role("button", name="Roles", exact=True).click()
        panel = page.locator("#menu-roles")
        expect(panel).to_be_visible()
        links = panel.locator("a")
        texts = [t.strip() for t in links.all_inner_texts()]
        assert "Startup founders" in texts and "Solution architects" in texts
        assert "All roles" in texts
        # every role name in the menu is a link; no plain-text entry
        assert panel.locator("li").count() == panel.locator("li a").count()
        panel.get_by_role("link", name="Solution architects").click()
        expect(page).to_have_url(live_server + "/roles/solution-architects")
        expect(page.get_by_role("heading", level=1).first).to_be_visible()
        expect(page.locator("#menu-roles")).to_be_hidden()
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1440", "390"])
def test_signed_in_architect_goes_straight_to_the_product_home(browser, live_server, seeded, viewport):
    email = seeded["emails"]["solution_architect"]
    assert role_of(email) == "solution_architect"
    context, page = _open(browser, viewport)
    try:
        sign_in(page, live_server, email)
        home = live_server + "/dashboard/overview"
        # the address of the site and the roles page both lead to the product home
        for path in ("/", "/roles"):
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(page).to_have_url(home)
            assert len(page.locator("main").inner_text().split()) > 20
        # any other public page offers one primary button, Open Entelim
        page.goto(live_server + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        header = page.locator("header")
        assert header.get_by_role("link", name="Start free").count() == 0
        assert header.get_by_role("link", name="Sign in").count() == 0
        button = header.locator("a[data-open-entelim]:visible")
        assert button.count() == 1
        button.click()
        expect(page).to_have_url(home)
    finally:
        context.close()


def test_account_menu_dashboard_link_lands_on_the_product_home_they_may_open(browser, live_server, seeded):
    email = seeded["emails"]["solution_architect"]
    assert role_of(email) == "solution_architect"
    context, page = _open(browser, DESKTOP)
    try:
        sign_in(page, live_server, email)
        page.goto(live_server + "/about", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Account menu").click()
        menu = page.locator("#menu-account")
        expect(menu).to_be_visible()
        assert menu.get_by_role("link", name="Dashboard").get_attribute("href") == "/dashboard/overview"
        menu.get_by_role("link", name="Dashboard").click()
        expect(page).to_have_url(live_server + "/dashboard/overview")
        assert "/admin" not in page.url
        assert len(page.locator("main").inner_text().split()) > 20
    finally:
        context.close()
