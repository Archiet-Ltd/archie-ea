"""A visitor from a search result in Portuguese, Spanish or French lands on a page in their language.

Journey: the site carries one page in English with a translation in each of the three languages. A reader opens
each language's page, at 1440 wide in the light theme and at 390 wide in the dark theme, and finds the page declaring
its own language, naming every other language it is available in, and pointing at its own address; every link
on the page, and every language it names, opens a page with content.
"""
import os
import re
import shutil
import threading
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect
from werkzeug.serving import make_server

from app.services import public_pages
from app.services.origin import canonical_origin

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PAGES = {
    "en": ("/about", None),
    "pt-BR": ("/pt-br/about", "Sobre o produto"),
    "es": ("/es/about", "Sobre el producto"),
    "fr": ("/fr/about", "A propos du produit"),
}
VIEWS = {
    "1440": ({"width": 1440, "height": 900}, "light"),
    "390": ({"width": 390, "height": 844}, "dark"),
}


def _shot(page, name):
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.screenshot(path=os.path.join(folder, name + ".png"))


@pytest.fixture(scope="module")
def translated_site(app, tmp_path_factory):
    """The real public content plus one fixture translation of /about into each language, served by the real app."""
    root = tmp_path_factory.mktemp("content")
    shutil.copytree(public_pages.CONTENT_ROOT, root / "pages")
    site = root / "pages" / "site"
    for code, (_path, title) in PAGES.items():
        if code == "en":
            continue
        (site / f"about.{code.lower()}.md").write_text(
            f"---\nlang: {code}\ntranslation_of: about\nwave: 1\n---\n\n# {title}\n\n{title}: texto de ejemplo.\n",
            encoding="utf-8",
        )
    original = public_pages.CONTENT_ROOT
    public_pages.CONTENT_ROOT = root / "pages"
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)
        public_pages.CONTENT_ROOT = original


@pytest.mark.parametrize("size", sorted(VIEWS))
@pytest.mark.parametrize("code", sorted(PAGES))
def test_each_language_page_declares_itself_and_every_link_lands(browser, translated_site, code, size):
    viewport, scheme = VIEWS[size]
    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    page = context.new_page()
    local = translated_site
    origin = canonical_origin()
    path, title = PAGES[code]
    try:
        response = page.goto(local + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200, path
        if title:
            expect(page.locator("h1").first).to_have_text(title)
        else:
            assert page.locator("h1").first.inner_text().strip(), "the English page has no heading"
        _shot(page, f"language-{code}-{size}")

        assert page.locator("html").get_attribute("lang") == code
        assert page.locator('link[rel="canonical"]').get_attribute("href") == origin + path

        alternates = {
            link.get_attribute("hreflang"): link.get_attribute("href")
            for link in page.locator('link[rel="alternate"][hreflang]').all()
        }
        assert alternates == {
            "en": origin + "/about",
            "pt-BR": origin + "/pt-br/about",
            "es": origin + "/es/about",
            "fr": origin + "/fr/about",
            "x-default": origin + "/about",
        }

        # every language the page names opens a page in that language with its own heading
        for other, address in alternates.items():
            if other == "x-default":
                continue
            reply = page.request.get(local + urlsplit(address).path)
            assert reply.status == 200, (other, address, reply.status)
            assert f'<html lang="{other}"' in reply.text(), (other, address)
            if PAGES[other][1]:
                assert PAGES[other][1] in reply.text(), (other, address)

        # the reader sees the other languages by their own names and each one opens that language's page
        names = {"en": "English", "pt-BR": "Português", "es": "Español", "fr": "Français"}
        language_list = page.locator('[data-testid="language-list"]')
        expect(language_list).to_be_visible()
        for other, name in names.items():
            if other == code:
                expect(language_list.locator("[aria-current]")).to_have_text(name)
                continue
            link = language_list.get_by_role("link", name=name)
            expect(link).to_have_attribute("hreflang", other)
            assert link.get_attribute("href") == PAGES[other][0], (code, other)
        _shot(page, f"language-list-{code}-{size}")

        links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
        internal = sorted({href.split("#")[0] for href in links if href.startswith("/") and not href.startswith("//")} - {""})
        assert internal, "the page links nowhere"
        for href in internal:
            reply = page.request.get(local + href)
            assert reply.status == 200, (code, href, reply.status)
            text = reply.text()
            heading = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S)
            title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S)
            has_heading = bool(heading and re.sub(r"<[^>]+>", "", heading.group(1)).strip())
            has_title = bool(title and title.group(1).strip())
            assert has_heading or has_title, (code, href, "the page has neither a heading nor a title")
    finally:
        context.close()
