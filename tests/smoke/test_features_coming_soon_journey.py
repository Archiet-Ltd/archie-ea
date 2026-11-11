"""A visitor reads Coming soon on the features page and finds a waiting list for every capability not open yet.

Goal: "What can I not use yet, and where do I ask for it?" From the home page's sentence the visitor reaches the
Coming soon list on the features page, opens every item, and each opens a page with a heading and a waiting-list form.
Every waiting list the early-access hub links to is on the list, and so is the one a later release's module address
answers with. Run with the release switch off and with it on.
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

SHOTS = pathlib.Path(os.environ.get("COMING_SOON_WALK_DIR") or tempfile.gettempdir()) / "coming-soon-walk"
SIZES = [(1440, 900, "light"), (390, 844, "dark")]
LATER_MODULE = "/modules/arb"


@pytest.fixture(scope="module")
def wave_server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": "1"})


def _shot(page, name, width, scheme, switch):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-switch-%s-%s-%d.png" % (name, switch, scheme, width))), full_page=True)


def _walk(browser, base, switch):
    listed = []
    for width, height, scheme in SIZES:
        context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
        page = context.new_page()
        try:
            page.goto(base + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.get_by_text("Capabilities that are not open to everyone yet").first.get_by_role(
                "link", name="Coming soon").click()
            page.wait_for_url(lambda url: "/features" in url, timeout=PAGE_TIMEOUT)
            expect(page.locator("#coming-soon")).to_have_text("Coming soon", timeout=PAGE_TIMEOUT)
            assert page.locator("#coming-soon + p + ul li").count() >= 2
            items = page.locator("#coming-soon + p + ul a")
            hrefs = [items.nth(i).get_attribute("href") for i in range(items.count())]
            assert hrefs, "Coming soon links no waiting list"
            assert switch == "off" or len(hrefs) >= 2, "Coming soon lists %s" % hrefs
            assert not TIMETABLE.search(page.inner_text("main")), TIMETABLE.search(page.inner_text("main")).group(0)
            _shot(page, "features-coming-soon", width, scheme, switch)
            for href in hrefs:
                page.goto(base + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                expect(page.get_by_role("heading", level=1)).not_to_be_empty(timeout=PAGE_TIMEOUT)
                assert page.locator('[data-testid="early-access-form"], [data-testid="feature-interest"]').count() >= 1, \
                    "%s has no waiting list" % href
            page.goto(base + hrefs[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            _shot(page, "waiting-list-page", width, scheme, switch)
            listed = hrefs

            hub = page.request.get(base + "/early-access").text()
            hub_links = set(re.findall(r'href="(/early-access/[a-z0-9-]+)"', hub))
            assert (hub_links or switch == "off") and not hub_links - set(hrefs), sorted(hub_links - set(hrefs))
        finally:
            context.close()
    return listed


def test_every_waiting_list_the_hub_links_to_is_on_the_features_page_with_the_switch_off(browser, live_server):
    _walk(browser, live_server, "off")


def test_with_the_switch_on_the_later_module_address_and_every_hub_waiting_list_are_on_the_features_page(
        browser, wave_server):
    listed = _walk(browser, wave_server, "on")
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(wave_server + LATER_MODULE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        form = page.locator('[data-testid="early-access-form"]')
        expect(form).to_have_count(1, timeout=PAGE_TIMEOUT)
        action = form.get_attribute("action")
        assert re.sub(r"/join$", "", action) in listed, "%s answers a waiting list that Coming soon omits" % LATER_MODULE
    finally:
        context.close()
