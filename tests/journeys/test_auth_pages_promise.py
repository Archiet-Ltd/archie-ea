"""Journey: a founder who has just read the home page opens Sign in and Create account and meets the same promise.

The goal, in a person's words: "I came from the home page. Is this the same product, and what am I agreeing to?"

Each page is opened at 1440 wide in the light theme and at 390 wide in the dark theme:

* the page carries the home page's own promise, read from the home page at run time rather than copied here;
* no badge names a superseded edition and no " -- " stands in for a dash;
* Create account says what a person agrees to, and both of its links open a page that has content.
"""
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import pytest
from bs4 import BeautifulSoup

pytestmark = pytest.mark.journey

STATIC = Path(__file__).resolve().parents[2] / "app" / "static"
SHOTS = Path(os.environ.get("AUTH_PAGES_SHOT_DIR") or tempfile.gettempdir()) / "auth_pages_promise"
VIEWPORTS = [("1440-light", 1440, 900, "light"), ("390-dark", 390, 844, "dark")]
SENTENCE = "By creating an account you agree to the Terms and the Privacy Policy"


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        chromium = p.chromium.launch()
        yield chromium
        chromium.close()


def _squash(text):
    return re.sub(r"\s+", " ", text).strip()


def _home_promise(client):
    page = client.get("/")
    assert page.status_code == 200
    soup = BeautifulSoup(page.get_data(as_text=True), "html.parser")
    hero = soup.select_one('[data-testid="home-hero"]')
    headline = _squash(hero.select_one("h1").get_text())
    audience = re.search(r"Built for [^.]+\.", _squash(hero.select_one(".home-lead").get_text()))
    assert headline and audience, "the home page's first screen lost its promise"
    return headline, audience.group(0)


def _open(browser, client, path, width, height, scheme):
    document = client.get(path).get_data(as_text=True)
    pg = browser.new_page(viewport={"width": width, "height": height}, color_scheme=scheme)

    def handle(route):
        url = urlparse(route.request.url)
        if url.path.startswith("/static/"):
            f = STATIC / url.path[len("/static/"):]
            return route.fulfill(path=str(f)) if f.is_file() else route.fulfill(status=404, body="")
        if url.path == path:
            return route.fulfill(status=200, content_type="text/html", body=document)
        return route.fulfill(status=204, body="")

    pg.route("http://app.test/**", handle)
    pg.goto("http://app.test" + path)
    return pg


@pytest.mark.parametrize("path", ["/account/login", "/account/register"])
@pytest.mark.parametrize("label,width,height,scheme", VIEWPORTS)
def test_the_page_makes_the_home_pages_promise(app, client, browser, path, label, width, height, scheme):
    headline, audience = _home_promise(client)
    pg = _open(browser, client, path, width, height, scheme)
    try:
        text = _squash(pg.evaluate("document.body.textContent"))
        assert headline in text, "%s does not carry the home page's promise: %r" % (path, headline)
        assert audience in text, "%s does not name who it is for: %r" % (path, audience)
        for stale in ("TOGAF 9.2", "consulting bench", "Architecture, governed"):
            assert stale not in text, stale
        assert " -- " not in text and " --" not in text.replace("--primary", "")
        SHOTS.mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(SHOTS / ("%s-%s.png" % (path.rsplit("/", 1)[1], label))), full_page=True)
    finally:
        pg.close()


@pytest.mark.parametrize("label,width,height,scheme", VIEWPORTS)
def test_create_account_says_what_a_person_agrees_to_and_both_links_open(app, client, browser, label, width, height, scheme):
    pg = _open(browser, client, "/account/register", width, height, scheme)
    try:
        sentence = pg.locator('[data-testid="register-terms"]')
        assert sentence.is_visible()
        assert _squash(sentence.inner_text()).rstrip(".") == SENTENCE
        links = {a.inner_text(): a.get_attribute("href") for a in sentence.locator("a").all()}
        assert links == {"Terms": "/terms", "Privacy Policy": "/privacy"}
        for name, href in links.items():
            landed = client.get(href)
            assert landed.status_code == 200, (name, href)
            body = BeautifulSoup(landed.get_data(as_text=True), "html.parser")
            assert len(_squash(body.get_text())) > 500, "%s opens an empty page" % name
    finally:
        pg.close()
