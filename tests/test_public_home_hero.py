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


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1440", "390"])
def test_the_hero_shows_the_chat_answering_with_its_sources(browser, site_url, viewport):
    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    try:
        page.goto(site_url + "/")
        figure = page.get_by_test_id("home-hero-figure")
        expect(figure).to_be_visible()
        # Labelled as the chat the product has today, never as the retired Ask page.
        expect(figure.locator(".home-frame__url")).to_have_text("AI chat")
        image = page.get_by_test_id("home-hero-proof")
        alt = image.get_attribute("alt")
        assert "AI chat" in alt and "source" in alt.lower(), alt
        expect(figure.locator("figcaption")).to_contain_text("source")
        # The picture loads, and the size the page declares is the size of the file.
        src = image.evaluate("el => el.currentSrc")
        response = page.request.get(src)
        assert response.ok and response.headers["content-type"].startswith("image/")
        assert "chat" in src, src
        loaded = image.evaluate("el => [el.naturalWidth, el.naturalHeight]")
        assert loaded[0] > 0 and loaded[1] > 0, "the picture did not decode"
        if viewport is DESKTOP:
            declared = (int(image.get_attribute("width")), int(image.get_attribute("height")))
            assert _webp_size(response.body()) == (declared[0] * 2, declared[1] * 2)
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
    # A link is marked as coming soon when it sits inside the home page's own
    # "Coming soon" block (whose caption already says so once) or carries
    # data-availability="early_access" directly -- not by repeating the words
    # "coming soon" in every link's own text, which the block's caption already
    # makes clear and repeating is clutter, not a stronger promise.
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
            link = links.nth(index)
            text = " ".join(link.inner_text().split())
            if not any(label.lower() in text.lower() for label in held):
                continue
            marked = link.evaluate(
                "el => el.getAttribute('data-availability') === 'early_access' "
                "|| !!el.closest('[data-testid=\"home-coming-soon\"]')"
            )
            if not marked:
                offered.append(text)
        assert offered == [], f"offered as available but coming soon: {offered}"
    finally:
        context.close()
