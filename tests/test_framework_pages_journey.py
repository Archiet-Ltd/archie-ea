"""A visitor finds a framework from the navbar or the footer and starts sign-up with it chosen.

Signed out, in a real browser, against the real application over a loopback socket: the visitor opens the
"Frameworks and canvases" menu, follows SAFe, reads what is there, and uses the sign-up button. The same
route is taken from the footer, from the hub on a phone, and, for a framework the company step offers
today, all the way to the company step with that framework ticked.
"""
import json
import uuid

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.test_account_mail_flows import PASSWORD, mail_off  # noqa: F401

DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
MENU = "Frameworks and canvases"


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


def _faq_markup(page):
    blocks = page.eval_on_selector_all(
        "script[type='application/ld+json']", "els => els.map(e => e.textContent)")
    nodes = [n for b in blocks for n in json.loads(b).get("@graph", [json.loads(b)])]
    return [n for n in nodes if n.get("@type") == "FAQPage"]


def _read_framework_page(page, site_url, slug, name):
    expect(page).to_have_url(site_url + "/frameworks/" + slug)
    expect(page.locator("h1").first).to_contain_text(name)
    title = page.title()
    assert 0 < len(title) <= 60 and name in title, title
    description = page.locator("meta[name='description']").get_attribute("content")
    assert 60 <= len(description) < 155, description
    assert len(page.locator("main").inner_text().split()) > 600
    faq = page.get_by_test_id("page-faq")
    expect(faq).to_be_visible()
    questions = faq.locator("h3").all_inner_texts()
    assert 4 <= len(questions) <= 6
    markup = _faq_markup(page)
    assert len(markup) == 1
    assert [q["name"] for q in markup[0]["mainEntity"]] == questions


def test_from_the_navbar_to_safe_and_into_sign_up_with_safe_chosen(desktop, site_url):
    page = desktop
    page.locator("header").get_by_role("button", name=MENU, exact=True).click()
    panel = page.locator("#menu-frameworks")
    expect(panel).to_be_visible()
    for label in ("ISO 27001", "TOGAF", "Lean Canvas", "Wardley Map", "All frameworks and canvases"):
        expect(panel.get_by_role("link", name=label, exact=True)).to_be_visible()
    panel.get_by_role("link", name="SAFe", exact=True).click()
    _read_framework_page(page, site_url, "safe", "SAFe")
    page.get_by_test_id("page-signup-link").click()
    expect(page).to_have_url(site_url + "/account/register?framework=safe")
    expect(page.get_by_text("Create an account", exact=True).first).to_be_visible()
    expect(page.locator("#email")).to_be_visible()


def test_from_the_footer_to_safe_and_into_sign_up_with_safe_chosen(desktop, site_url):
    page = desktop
    footer = page.locator("footer")
    column = footer.locator("[data-footer-column='frameworks']")
    expect(column.locator("h2")).to_have_text(MENU)
    column.get_by_role("link", name="SAFe", exact=True).click()
    _read_framework_page(page, site_url, "safe", "SAFe")
    page.get_by_test_id("page-signup-link").click()
    expect(page).to_have_url(site_url + "/account/register?framework=safe")


def test_the_hub_links_every_framework_and_canvas_to_a_page_with_content(desktop, site_url):
    page = desktop
    page.locator("footer [data-footer-column='frameworks']").get_by_role(
        "link", name="All frameworks and canvases").click()
    expect(page).to_have_url(site_url + "/frameworks")
    expect(page.locator("h1").first).to_contain_text("Frameworks and canvases")
    hrefs = page.eval_on_selector_all(
        "article a[href^='/frameworks/']", "els => Array.from(new Set(els.map(e => e.getAttribute('href'))))")
    assert len(hrefs) == 22, hrefs
    for href in hrefs:
        page.goto(site_url + href)
        expect(page.locator("h1").first).to_be_visible()
        assert len(page.locator("main").inner_text().split()) > 600, href
        expect(page.get_by_test_id("page-faq")).to_be_visible()
        expect(page.get_by_test_id("page-signup-link")).to_be_visible()


def test_a_canvas_page_sends_the_visitor_to_plain_sign_up(desktop, site_url):
    page = desktop
    page.goto(site_url + "/frameworks/lean-canvas")
    expect(page.locator("h1").first).to_contain_text("Lean Canvas")
    page.get_by_test_id("page-signup-link").click()
    expect(page).to_have_url(site_url + "/account/register")


def test_the_phone_menu_reaches_the_hub_and_no_page_overflows(browser, site_url):
    context = browser.new_context(viewport=PHONE, is_mobile=True, has_touch=True)
    page = context.new_page()
    try:
        page.goto(site_url + "/")
        page.get_by_role("button", name="Menu").click()
        page.locator("header").get_by_role("button", name=MENU, exact=True).click()
        page.locator("#menu-frameworks").get_by_role("link", name="All frameworks and canvases").click()
        expect(page).to_have_url(site_url + "/frameworks")
        for path in ("/frameworks", "/frameworks/safe", "/frameworks/iso-27001", "/frameworks/okr"):
            page.goto(site_url + path)
            width = page.evaluate("document.documentElement.scrollWidth")
            assert width <= PHONE["width"], f"{path} is {width}px wide on a {PHONE['width']}px screen"
    finally:
        context.close()


def test_iso_27001_from_its_page_arrives_ticked_on_the_company_step(browser, site_url, mail_off):  # noqa: F811
    # The sign-up creates a real account and company in the test database (ARB evidence is append-only, so
    # they cannot be deleted); the address is unique to this run and ends in example.com.
    email = "journey-{}@example.com".format(uuid.uuid4().hex[:8])
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        page.goto(site_url + "/frameworks/iso-27001")
        page.get_by_test_id("page-signup-link").click()
        expect(page).to_have_url(site_url + "/account/register?framework=iso27001")
        page.fill("#first_name", "Frame")
        page.fill("#last_name", "Visitor")
        page.fill("#email", email)
        page.fill("#password", PASSWORD)
        page.fill("#password2", PASSWORD)
        page.click("#submit")
        page.wait_for_url(lambda url: "/account/register" not in url, timeout=60000)
        page.goto(site_url + "/account/welcome?step=3")
        expect(page.get_by_test_id("signup-company")).to_be_visible()
        expect(page.locator("#welcome-framework-iso27001")).to_be_checked()
        expect(page.locator("#welcome-framework-gdpr")).not_to_be_checked()
    finally:
        context.close()


SCREEN_OF = {
    "safe": "SAFe portfolio Kanban",
    "togaf": "TOGAF ADM phase view",
    "iso-27001": "ISO 27001 row open",
    "business-model-canvas": "Business Model Canvas canvas",
    "wardley-map": "Wardley Map canvas",
}


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["desktop", "phone"])
@pytest.mark.parametrize("slug", sorted(SCREEN_OF))
def test_a_visitor_sees_the_screen_of_the_framework_they_opened(browser, site_url, slug, viewport):
    """The picture on a framework page is that framework's own screen, loaded, and says so in its caption."""
    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    page.goto(site_url + "/frameworks/" + slug)
    figure = page.get_by_test_id("page-screenshot")
    expect(figure).to_be_visible()
    image = figure.locator("img").first
    assert image.evaluate("el => el.complete && el.naturalWidth > 300")
    assert image.get_attribute("src").endswith(f"/img/frameworks/{slug}.webp")
    alt = (image.get_attribute("alt") or "") + " " + figure.inner_text()
    for word in SCREEN_OF[slug].split()[:2]:
        assert word.lower() in alt.lower(), (slug, alt)
    context.close()
