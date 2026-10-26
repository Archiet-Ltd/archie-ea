"""A startup CTO lands on the home page, reads the hero and follows what it offers.

The hero must show the product as it is today: the AI chat giving a cited answer,
labelled as the chat, in a picture whose declared size is the file's own size.
Nothing on the page may offer a coming-soon capability as available, and nothing
may send the visitor to the retired Ask page. Every link the hero and the footer
offer must land on a page with content.
"""
import re
import struct
import threading

import pytest
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from app.services.site_registry import raw_registry

DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}


@pytest.fixture(scope="module")
def site_url(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


def _webp_size(data):
    """Width and height of a WebP file (lossy, lossless or extended)."""
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP", "not a WebP file"
    kind = data[12:16]
    if kind == b"VP8X":
        return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
    if kind == b"VP8L":
        bits = struct.unpack("<I", data[21:25])[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    width, height = struct.unpack("<HH", data[26:30])
    return width & 0x3FFF, height & 0x3FFF


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1440", "390"])
def test_the_hero_shows_the_overview_of_the_first_wave_and_not_the_chat(browser, site_url, viewport, scheme):
    """While the first wave is live the hero shows real screens of what a person can use: the Overview, dated.
    The cited chat answer is early access and appears on the waiting-list page, not here."""
    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    page = context.new_page()
    try:
        page.goto(site_url + "/")
        expect(page.get_by_test_id("home-hero").locator("h1")).to_contain_text("The company brain.")
        figure = page.get_by_test_id("home-hero-figure")
        expect(figure).to_be_visible()
        # The frame is the library's card: a hairline border and no shadow.
        frame = page.get_by_test_id("home-hero-frame")
        assert frame.get_attribute("data-slot") == "card"
        shadow = frame.evaluate("el => getComputedStyle(el).boxShadow")
        assert shadow == "none" or set(re.findall(r"rgba?\([^)]*\)", shadow)) <= {"rgba(0, 0, 0, 0)"}, shadow
        assert frame.evaluate("el => parseFloat(getComputedStyle(el).borderTopWidth)") == 1
        expect(frame).to_contain_text("Overview")
        image = page.get_by_test_id("home-hero-proof")
        alt = image.get_attribute("alt")
        assert "overview" in alt.lower() and "chat" not in alt.lower(), alt
        expect(figure.locator("figcaption")).to_contain_text("captured on")
        src = image.evaluate("el => el.currentSrc")
        response = page.request.get(src)
        assert response.ok and response.headers["content-type"].startswith("image/")
        assert "overview" in src and "chat" not in src, src
        loaded = image.evaluate("el => [el.naturalWidth, el.naturalHeight]")
        assert loaded[0] > 0 and loaded[1] > 0, "the picture did not decode"
        if viewport is DESKTOP:
            declared = (int(image.get_attribute("width")), int(image.get_attribute("height")))
            assert _webp_size(response.body()) == (declared[0] * 2, declared[1] * 2)
        assert "chat-answer" not in page.content()
        # The chat answer is offered as early access, with a waiting list.
        early = page.get_by_test_id("home-early-access")
        expect(early).to_contain_text("Ask in plain language")
        expect(early.get_by_role("link", name="Join the waiting list").first).to_be_visible()
    finally:
        context.close()


def test_the_hero_actions_land_on_pages_with_content(browser, site_url):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        page.goto(site_url + "/")
        page.get_by_test_id("home-hero-start").click()
        expect(page).to_have_url(re.compile(r"/register"))
        expect(page.get_by_role("heading").first).to_be_visible()
        assert len(page.locator("main, form").first.inner_text().strip()) > 40
        page.goto(site_url + "/")
        page.get_by_test_id("home-hero-pricing").click()
        expect(page).to_have_url(re.compile(r"/pricing$"))
        expect(page.get_by_role("heading", level=1)).to_be_visible()
        assert "Startup" in page.inner_text("body")
    finally:
        context.close()


def test_nothing_on_the_home_page_sends_a_visitor_to_the_retired_ask_page(browser, site_url):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        page.goto(site_url + "/")
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(a => a.getAttribute('href'))")
        assert hrefs, "the home page has no links"
        assert [h for h in hrefs if "/intelligence/ask" in h] == []
    finally:
        context.close()


def test_no_coming_soon_capability_is_offered_as_available_on_the_home_page(browser, site_url):
    raw = raw_registry()
    held = {i["label"] for i in raw["product"]["coming_soon"]["items"]}
    held |= {i["label"] for i in raw["solutions"]["coming_soon"]["items"]}
    held |= {"Simulation and Scenario Planning", "Scenario Planning"}
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        page.goto(site_url + "/")
        body = page.locator("main, [role=main]").first if page.locator("main").count() else page.locator("body")
        links = body.locator("a")
        offered = []
        for index in range(links.count()):
            text = " ".join(links.nth(index).inner_text().split())
            if any(label.lower() in text.lower() for label in held) and "coming soon" not in text.lower() and "early access" not in text.lower():
                offered.append(text)
        assert offered == [], f"offered as available but coming soon: {offered}"
    finally:
        context.close()
