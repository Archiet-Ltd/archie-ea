"""Journey tests for entelim.org's navbar and footer, signed out, in a real browser.

Written from what a visitor needs: a startup CTO lands on the home page, opens the
menus, finds what the product does, follows a link, and is never left on a blank
page. The real application is served over a loopback socket and driven with
Chromium; the pages are the real templates, the real registry and the real
stylesheets. Nothing here asserts a status code or that an element exists without
also asserting what the visitor reads.

Public pages need no organisation data: the navbar and footer are the same for
every signed-out visitor. The signed-in variant is covered in
tests/test_public_site_registry.py.
"""

import pytest
from playwright.sync_api import expect, sync_playwright

DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
MENU_TRIGGERS = ["Product", "Solutions", "Roles", "Compare", "Resources"]

LINKS_JS = """
(selector) => Array.from(document.querySelectorAll(selector)).map(a => [
    a.getAttribute('href'), a.getAttribute('data-label') || (a.textContent || '').replace(/\\s+/g, ' ').trim()])
"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def desktop(browser, site_url):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(site_url + "/")
    yield page
    assert errors == [], f"the page raised script errors: {errors}"
    context.close()


@pytest.fixture
def phone(browser, site_url):
    context = browser.new_context(viewport=PHONE, is_mobile=True, has_touch=True)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(site_url + "/")
    yield page
    assert errors == [], f"the page raised script errors: {errors}"
    context.close()


def _trigger(page, name):
    return page.locator("header").get_by_role("button", name=name, exact=True)


def _panel_hrefs(page, name):
    trigger = _trigger(page, name)
    panel = page.locator("#" + trigger.get_attribute("aria-controls"))
    return [href for href, _ in panel.evaluate("(el) => Array.from(el.querySelectorAll('a')).map(a => [a.getAttribute('href'), a.textContent])")]


def test_every_menu_opens_and_every_item_lands_on_a_page_with_content(desktop, site_url):
    """The visitor opens each menu, then follows every item and every footer link.
    Each lands on the page it names (no redirect), with a heading and real content,
    never a sign-in page, and an anchor link finds its place on the page."""
    page = desktop
    hrefs = {}
    for name in MENU_TRIGGERS:
        _trigger(page, name).click()
        panel = page.locator("#" + _trigger(page, name).get_attribute("aria-controls"))
        expect(panel).to_be_visible()
        for href, label in panel.evaluate(
            "(el) => Array.from(el.querySelectorAll('a')).map(a => [a.getAttribute('href'), a.textContent.replace(/\\s+/g, ' ').trim()])"
        ):
            hrefs.setdefault(href, label)
    for href, label in page.evaluate(LINKS_JS, "header > div > a, header nav > ul > li > a"):
        hrefs.setdefault(href, label)
    for href, label in page.evaluate(LINKS_JS, "footer a"):
        hrefs.setdefault(href, label)
    # Sign in and Start free lead to the account forms by design; they are not content pages.
    hrefs = {h: text for h, text in hrefs.items()
             if h and not h.startswith(("#", "mailto:", "/account/")) and text not in ("Sign in", "Start free")}
    assert len(hrefs) > 60, f"only {len(hrefs)} distinct links found in the menus and footer"

    by_path = {}
    for href, label in hrefs.items():
        path, _, fragment = href.partition("#")
        by_path.setdefault(path, []).append((label, fragment))

    problems = []
    for path, uses in sorted(by_path.items()):
        response = page.goto(site_url + path, wait_until="domcontentloaded")
        label = uses[0][0]
        if response.status != 200:
            problems.append(f"{label!r} -> {path} answers {response.status}")
            continue
        if page.url.split("#")[0] != site_url + path:
            problems.append(f"{label!r} -> {path} redirects to {page.url}")
            continue
        if "/account/login" in page.url:
            problems.append(f"{label!r} -> {path} is a sign-in page")
        heading = page.locator("h1").first
        if heading.count() == 0 or not heading.inner_text().strip():
            problems.append(f"{label!r} -> {path} has no heading")
        words = len(page.locator("main").inner_text().split())
        if words < 25:
            problems.append(f"{label!r} -> {path} has only {words} words of content")
        for use_label, fragment in uses:
            if fragment and page.locator(f"[id='{fragment}']").count() != 1:
                problems.append(f"{use_label!r} -> {path}#{fragment}: nothing on the page has that id")
    assert problems == []


def test_a_visitor_follows_the_product_menu_to_the_twin_map(desktop, site_url):
    page = desktop
    _trigger(page, "Product").click()
    panel = page.locator("#menu-product")
    expect(panel.get_by_text("Ask anything")).to_be_visible()
    expect(panel.get_by_text("Pick a system and see the applications and services that depend on it.")).to_be_visible()
    panel.get_by_role("link", name="Twin map").click()
    expect(page).to_have_url(site_url + "/modules/digital-twin")
    expect(page.locator("h1").first).to_be_visible()
    assert "dependenc" in page.locator("main").inner_text().lower() or "twin" in page.locator("main").inner_text().lower()


def test_navbar_and_footer_carry_the_same_items(desktop):
    page = desktop
    nav = {label for _, label in page.evaluate(LINKS_JS, "header a") if label}
    foot = {label for _, label in page.evaluate(LINKS_JS, "footer a") if label}
    foot |= set(page.eval_on_selector_all("footer h2", "els => els.map(e => e.textContent.trim())"))
    own = {"Sign in", "Talk to us", "Start free", "Pricing"}
    roles = {label for _, label in page.evaluate(LINKS_JS, "#menu-roles li a")}
    missing = sorted(text for text in nav if text not in foot and text not in own and text not in roles)
    assert missing == [], f"in the navbar but not the footer: {missing}"
    # the footer is one set of columns, not a second copy of the menus
    footer_headings = page.eval_on_selector_all("footer h2", "els => els.map(e => e.textContent.trim())")
    assert footer_headings == ["Product", "Solutions", "Roles", "Compare", "Resources", "Company", "Legal", "Coming soon"]


def test_coming_soon_is_grouped_marked_and_leads_to_a_waiting_list(desktop, site_url):
    page = desktop
    _trigger(page, "Product").click()
    soon = page.locator("#menu-product [data-menu-group='coming-soon']")
    expect(soon.get_by_text("Coming soon", exact=True)).to_be_visible()
    expect(soon.get_by_text("Not available yet. Join the waiting list.")).to_be_visible()
    rows = soon.locator("a[data-availability='early_access']")
    assert rows.count() == 2
    for text in ("Scenarios", "Software and tool discovery"):
        expect(soon.get_by_role("link", name=text)).to_be_visible()
    # nothing delivered sits in the Coming soon group, and nothing coming soon sits among delivered ones
    live_labels = page.eval_on_selector_all("#menu-product a[data-availability='live']", "els => els.map(e => e.textContent)")
    assert not any("Coming soon:" in label for label in live_labels)

    soon.get_by_role("link", name="Scenarios").click()
    expect(page).to_have_url(site_url + "/modules/scenario-modelling")
    form = page.get_by_test_id("feature-interest-form")
    expect(form).to_be_visible()
    # the page's own content: the site footer sits inside <main> and carries the logo and the account links
    own = page.evaluate("""() => {
        const inside = (selector) => Array.from(document.querySelectorAll('main ' + selector)).filter(e => !e.closest('footer'));
        return {
            media: inside('img, picture, video, figure').length,
            plans: inside('a').filter(a => /^\\/pricing/.test(a.getAttribute('href')) || /register/.test(a.getAttribute('href'))).length,
        };
    }""")
    assert own == {"media": 0, "plans": 0}, f"a coming soon page shows a picture or sells: {own}"
    form.get_by_label("Email").fill("visitor-nav-journey@example.com")
    form.get_by_label("Used to tell you when this becomes available, and for nothing else.").check()
    form.get_by_test_id("feature-interest-submit").click()
    expect(page.get_by_test_id("feature-interest-thanks")).to_be_visible()


def test_everything_coming_soon_lands_on_the_features_list(desktop, site_url):
    page = desktop
    _trigger(page, "Product").click()
    page.locator("#menu-product").get_by_role("link", name="Everything coming soon").click()
    expect(page).to_have_url(site_url + "/features#coming-soon")
    section = page.locator("#coming-soon")
    expect(section).to_be_visible()
    for text in ("Scenarios", "Software and tool discovery"):
        expect(page.locator("article").get_by_role("link", name=text, exact=True)).to_be_visible()


def test_keyboard_opens_each_menu_moves_through_it_closes_with_escape_and_returns_focus(desktop):
    page = desktop

    def focused():
        return page.evaluate("() => (document.activeElement.getAttribute('aria-label') || document.activeElement.textContent || '').replace(/\\s+/g, ' ').trim()")

    # focus order: skip link, wordmark, then the top-level entries left to right
    page.keyboard.press("Tab")
    assert focused() == "Skip to main content"
    page.keyboard.press("Tab")
    assert "home" in focused().lower()
    seen = []
    for name in MENU_TRIGGERS[:3]:
        page.keyboard.press("Tab")
        assert focused().startswith(name), (name, focused())
        seen.append(name)
        trigger = _trigger(page, name)
        page.keyboard.press("Enter")
        expect(trigger).to_have_attribute("aria-expanded", "true")
        panel = page.locator("#" + trigger.get_attribute("aria-controls"))
        expect(panel).to_be_visible()
        page.keyboard.press("Tab")
        first = focused()
        assert first, "focus did not move into the menu"
        assert panel.evaluate("(el, text) => el.contains(document.activeElement)", first) is True
        page.keyboard.press("ArrowDown")
        second = focused()
        assert second != first
        page.keyboard.press("Escape")
        expect(trigger).to_have_attribute("aria-expanded", "false")
        expect(panel).to_be_hidden()
        assert focused().startswith(name), f"focus did not return to {name}: {focused()!r}"
    # Compare, Pricing and Resources keep the same behaviour
    for name in ("Compare", "Resources"):
        trigger = _trigger(page, name)
        trigger.focus()
        page.keyboard.press("Space")
        expect(trigger).to_have_attribute("aria-expanded", "true")
        page.keyboard.press("Escape")
        expect(trigger).to_have_attribute("aria-expanded", "false")
        assert focused().startswith(name)


def test_tabbing_out_of_an_open_menu_closes_it_and_opening_one_closes_another(desktop):
    page = desktop
    _trigger(page, "Product").click()
    expect(page.locator("#menu-product")).to_be_visible()
    _trigger(page, "Solutions").click()
    expect(page.locator("#menu-product")).to_be_hidden()
    expect(page.locator("#menu-solutions")).to_be_visible()
    page.mouse.click(5, 600)
    expect(page.locator("#menu-solutions")).to_be_hidden()


def test_on_a_phone_the_menu_button_opens_a_sheet_and_every_item_is_reachable(phone, site_url):
    page = phone
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    menu_button = page.get_by_role("button", name="Menu", exact=True)
    expect(menu_button).to_be_visible()
    expect(_trigger(page, "Product")).to_be_hidden()
    expect(page.locator("header a:visible", has_text="Start free")).to_have_count(1)

    menu_button.click()
    expect(menu_button).to_have_attribute("aria-expanded", "true")
    sheet = page.locator("#site-nav-panel")
    expect(sheet).to_be_visible()
    problems = []
    for name in MENU_TRIGGERS:
        trigger = _trigger(page, name)
        trigger.scroll_into_view_if_needed()
        trigger.click()
        expect(trigger).to_have_attribute("aria-expanded", "true")
        panel = page.locator("#" + trigger.get_attribute("aria-controls"))
        expect(panel).to_be_visible()
        for link in panel.locator("a").all():
            link.scroll_into_view_if_needed()
            box = link.bounding_box()
            if box is None or box["x"] < 0 or box["x"] + box["width"] > PHONE["width"] + 1:
                problems.append(f"{name}: {link.inner_text()!r} is outside the {PHONE['width']}px screen")
        assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"], f"{name}: the page scrolls sideways"
        trigger.click()
    assert problems == []

    # the last row of the sheet can be scrolled into view and used
    last = sheet.get_by_role("link", name="Talk to us")
    last.scroll_into_view_if_needed()
    box = last.bounding_box()
    assert box["y"] + box["height"] <= PHONE["height"], "the last row is cut off by the bottom of the screen"

    page.keyboard.press("Escape")
    expect(sheet).to_be_hidden()
    expect(menu_button).to_have_attribute("aria-expanded", "false")
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "Menu"


def test_on_a_phone_a_menu_item_leads_to_its_page(phone, site_url):
    page = phone
    page.get_by_role("button", name="Menu", exact=True).click()
    _trigger(page, "Product").click()
    page.locator("#menu-product").get_by_role("link", name="Risk register").click()
    expect(page).to_have_url(site_url + "/modules/risk-register")
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["width"]
    expect(page.locator("h1").first).to_be_visible()


@pytest.mark.parametrize("path", ["/", "/pricing", "/modules/risk-register"])
def test_no_sideways_scroll_at_390_on_the_pages_a_visitor_lands_on(browser, site_url, path):
    context = browser.new_context(viewport=PHONE, is_mobile=True, has_touch=True)
    page = context.new_page()
    page.goto(site_url + path)
    width = page.evaluate("document.documentElement.scrollWidth")
    context.close()
    assert width <= PHONE["width"], f"{path} is {width}px wide on a {PHONE['width']}px screen"
