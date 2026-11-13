"""A visitor reads the features page for what they can use today, and what is coming soon, and every link keeps its word.

Goal: "What can I use today?" Every link under Delivered today opens the module's own page, never a waiting list;
every capability the release does not open yet says Coming soon, and its link scrolls to an entry of the list
that names it. Run with the release switch off and at the first release, at 1440 light and 390 dark.
"""
import os
import pathlib
import re
import tempfile

import pytest
from playwright.sync_api import expect

from tests.test_public_timeline_words import TIMETABLE

from .conftest import PAGE_TIMEOUT, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = pathlib.Path(os.environ.get("FEATURES_DELIVERED_WALK_DIR") or tempfile.gettempdir()) / "features-delivered-walk"
SIZES = [(1440, 900, "light"), (390, 844, "dark")]
WAITING = '[data-testid="early-access-form"], [data-testid="feature-interest"]'


@pytest.fixture(scope="module")
def wave_server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": "1"})


def _walk(browser, base, switch):
    for width, height, scheme in SIZES:
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
        page = context.new_page()
        try:
            page.goto(base + "/features", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            SHOTS.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SHOTS / ("features-switch-%s-%s-%d.png" % (switch, scheme, width))), full_page=True)
            assert not TIMETABLE.search(page.inner_text("main")), TIMETABLE.search(page.inner_text("main")).group(0)

            delivered = page.locator("p", has_text=re.compile(r"^Delivered today by:"))
            hrefs = []
            for i in range(delivered.count()):
                links = delivered.nth(i).locator("a")
                hrefs += [links.nth(j).get_attribute("href") for j in range(links.count())]
            assert hrefs, "nothing is delivered today"
            for href in hrefs:
                page.goto(base + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                expect(page.get_by_role("heading", level=1).first).not_to_be_empty(timeout=PAGE_TIMEOUT)
                assert page.locator(WAITING).count() == 0, "%s is delivered today but opens a waiting list" % href
                assert len(page.inner_text("main").strip()) > 200, "%s opens an empty page" % href

            page.goto(base + "/features", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            names = page.locator('article a[href^="#coming-"]:not([href="#coming-soon"])')
            count = names.count()
            assert count >= 10, "only %d Coming soon links name a capability" % count
            for i in range(count):
                link = names.nth(i)
                anchor = link.get_attribute("href")[1:]
                heading = link.evaluate("el => { let n = el; while (n && n.tagName !== 'H3') n = n.previousElementSibling || n.parentElement; return n ? n.textContent : '' }")
                link.click()
                target = page.locator("#" + anchor)
                expect(target).to_have_count(1, timeout=PAGE_TIMEOUT)
                expect(target).to_be_in_viewport(timeout=PAGE_TIMEOUT)
                assert target.inner_text().startswith(heading.strip()), (heading, target.inner_text()[:80])
                assert len(target.inner_text()) > len(heading) + 40
            page.screenshot(path=str(SHOTS / ("coming-soon-switch-%s-%s-%d.png" % (switch, scheme, width))))
        finally:
            context.close()


def test_every_delivered_link_opens_the_module_and_every_coming_soon_link_lands_on_its_entry_with_the_switch_off(
        browser, live_server):
    _walk(browser, live_server, "off")


def test_at_the_first_release_a_closed_capability_is_coming_soon_not_delivered(browser, wave_server):
    _walk(browser, wave_server, "on")
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(wave_server + "/features", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # Strategy and the organisation chart rest on pages the first release does not open: each is under Coming soon
        expect(page.locator("#coming-strategy-management")).to_have_count(1)
        expect(page.locator("#coming-people-and-organisation-management")).to_have_count(1)
        expect(page.locator("p", has_text=re.compile(r"^Delivered today by:.*Business Model Canvas"))).to_have_count(0)
    finally:
        context.close()
