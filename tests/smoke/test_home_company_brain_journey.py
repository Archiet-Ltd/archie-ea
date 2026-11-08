"""A visitor opens the public home and finds the company brain.

The visitor is signed out. The goal: learn in one screen what Entelim is (the company brain, in the
founder's words), see that their own question is one of those it answers, start free and reach a
sign-up page that has content, and never meet a date, quarter or wave promised for a capability on
any public page.
"""
import os
import re
import urllib.request
import xml.etree.ElementTree as ET

import pytest

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

TITLE = "Entelim — The company brain: a digital twin of your business"
HEADLINE = "The company brain for your business."
LEAD = (
    "A living digital twin of your business, with the company brain's enterprise intelligence answering what you ask it. Built for founders, leadership teams and architects. Start free."
)
DESCRIPTION = (
    "Entelim is the company brain: a living digital twin of your business, with the enterprise intelligence "
    "answering what you ask it. Strategy, capabilities, applications, people, spend and risk in one model that keeps "
    "itself current, for founders, leadership teams and architects."
)
QUESTIONS = (
    ("what-breaks", "What breaks if this fails?", "founders, CTOs and technology architects"),
    ("cost", "What does this cost us, and who owns it?", "finance"),
    ("programmes", "Which programmes serve this strategy?", "architects"),
    ("controls", "Which controls are not in place?", "risk and compliance"),
    ("fund", "What should we fund next?", "leadership"),
)
SCREENS = {
    "1440-light": ({"width": 1440, "height": 900}, "light"),
    "390-dark": ({"width": 390, "height": 844}, "dark"),
}


def _shot(page, name):
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if not folder:
        return
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, name + ".png"))


# a date, a quarter or a release wave given for a capability
TIMING = re.compile(r"\bQ[1-4]\s*20\d\d\b|\b(?:wave|stage)\s*\d\s*of\b|\bwave\s*\d\b|\bcoming in\b|\b202[7-9]\b", re.I)


def _text(html):
    html = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _get(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.status, response.read().decode("utf-8", "replace")


@pytest.mark.parametrize("screen", sorted(SCREENS))
def test_visitor_finds_the_company_brain_and_starts_free(browser, live_server, screen):
    viewport, theme = SCREENS[screen]
    context = browser.new_context(viewport=viewport)
    context.set_default_timeout(PAGE_TIMEOUT)
    if theme == "dark":
        context.add_init_script("document.documentElement.classList.add('dark');")
    page = context.new_page()
    try:
        page.goto(live_server + "/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert page.title() == TITLE
        for selector in ('meta[property="og:title"]', 'meta[name="twitter:title"]'):
            assert page.locator(selector).get_attribute("content") == TITLE
        for selector in ('meta[name="description"]', 'meta[property="og:description"]', 'meta[name="twitter:description"]'):
            content = page.locator(selector).get_attribute("content")
            assert content == DESCRIPTION, content

        hero = page.locator('[data-testid="home-hero"]')
        assert hero.locator("h1").inner_text().strip() == HEADLINE
        lead = " ".join(hero.locator("p.home-lead").inner_text().split())
        assert lead == LEAD

        # the five questions, each with who asks it
        for key, question, who in QUESTIONS:
            card = page.locator('[data-testid="home-proof-%s"]' % key)
            assert card.locator("h3").inner_text().strip() == question
            assert who in card.inner_text()
            assert card.locator('[data-testid="home-proof-%s-who"]' % key).inner_text().strip() == "Asked by %s." % who

        # the controls card promises only what the product answers today: no control library exists yet
        controls = page.locator('[data-testid="home-proof-controls"]').inner_text()
        assert "controls on record" not in controls
        for word in ("frameworks", "waivers", "risks"):
            assert word in controls, controls

        # the example image is a real answer, loaded, and its description matches what it shows
        proof = page.locator('[data-testid="home-hero-proof"]')
        assert proof.evaluate("e => e.complete && e.naturalWidth > 0")
        assert "what breaks if the Payments service fails" in proof.get_attribute("alt")

        _shot(page, "home-%s" % screen)
        page.locator('[data-testid="home-proof-fund"]').scroll_into_view_if_needed()
        _shot(page, "home-questions-%s" % screen)

        # the primary call to action lands on a sign-up page that has content
        page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.locator('[data-testid="home-hero-start"]').click()
        page.wait_for_url("**/register**", timeout=PAGE_TIMEOUT)
        assert len(page.locator("body").inner_text().strip()) > 100
        assert page.locator("form input").count() >= 1
        _shot(page, "register-%s" % screen)
    finally:
        context.close()


def test_sign_in_and_pricing_carry_the_new_wording(browser, live_server):
    context = browser.new_context(viewport=SCREENS["1440-light"][0])
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        page.goto(live_server + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        body = " ".join(page.locator("body").inner_text().split())
        assert "Entelim is priced per company" in body
        assert "Enterprise Intelligence Management priced per company" not in body
        page.goto(live_server + "/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        eyebrow = page.get_by_text("The company brain for your business", exact=True)
        assert eyebrow.count() >= 1
        assert eyebrow.first.is_visible() or page.viewport_size["width"] < 1024
        _shot(page, "login-1440-light")
    finally:
        context.close()


def test_no_public_page_gives_a_date_for_a_capability(live_server):
    status, xml = _get(live_server + "/sitemap.xml")
    assert status == 200
    locs = [e.text for e in ET.fromstring(xml).iter() if e.tag.endswith("loc")]
    assert len(locs) > 20, locs
    paths = ["/", "/about", "/features", "/pricing"]
    for loc in locs:
        paths.append("/" + loc.split("://", 1)[-1].split("/", 1)[-1])
    offenders = {}
    for path in dict.fromkeys(paths):
        status, html = _get(live_server + path)
        assert status == 200, path
        hits = TIMING.findall(_text(html))
        if hits:
            offenders[path] = sorted(set(hits))
    assert not offenders, offenders


def test_home_journeys_hold_no_machine_path():
    """Screenshots go where the shared setting says; no home page journey names a directory on one machine."""
    folder = os.path.dirname(os.path.abspath(__file__))
    for name in (
        "test_home_company_brain_journey.py",
        "test_first_screen_one_message_journey.py",
        "test_home_first_screen_journey.py",
        "test_home_proof_inside_fold_journey.py",
    ):
        with open(os.path.join(folder, name), encoding="utf-8") as handle:
            assert not re.search(r"""["']/(home|Users|root)/""", handle.read()), name
