"""Journey: at the first release a link to a later page is stated, never hidden.

Written from the goal "I am mapping my capabilities; show me where the business layer and the business cases are,
and tell me plainly when one of them is not open yet":

* a signed-in CTO opens the capability map's simple view and sees the Business Layer crumb; clicking it lands on a
  page with content;
* the same person opens the canvases page and sees the Business Cases button; clicking it lands on the early-access
  page for business cases, which names what is coming and offers the waiting list;
* the Business Layer crumb is present on every capability lens that carries a trail.

Run against the previous release, where these two links were removed from the page, it is red: that is the "before".
"""

from __future__ import annotations

import os

import pytest

from .conftest import PAGE_TIMEOUT, boot_live_server, _seed_standard_org
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("WAVE_LATER_LINKS_SHOTS")
LENSES = [
    "/capability-map/simple", "/capability-map/hierarchy", "/capability-map/network",
    "/capability-maturity/frameworks-overview",
]


@pytest.fixture(scope="module")
def wave_server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": "1"})


@pytest.fixture(scope="module")
def org(request):
    return _seed_standard_org(request, None)


def shoot(page, name):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for width, height in ((1440, 900), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(300)
        page.screenshot(path=os.path.join(SHOTS, "%s-%d.png" % (name, width)), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})


def _heading(page):
    texts = [h.strip() for h in page.locator("h1, h2, [data-slot='card-title']").all_inner_texts() if h.strip()]
    return texts[0] if texts else ""


def test_business_layer_crumb_is_shown_and_opens_a_page_with_content(browser, wave_server, org):
    page = browser.new_page()
    try:
        _login(page, wave_server, org["emails"]["cto"])
        page.goto(wave_server + "/capability-map/simple", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        crumb = page.locator("nav[aria-label='breadcrumb'] a, nav a").filter(has_text="Business Layer")
        assert crumb.count() >= 1, "the Business Layer crumb is missing from the capability map"
        shoot(page, "capability-simple")
        crumb.first.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.locator('[data-testid="early-access-page"]').count() == 1, "the Business Layer crumb opens no page that says it is coming"
        assert _heading(page) == "Business Layer", "the page the crumb opens does not name the Business Layer in its title"
        first_line = page.locator('[data-testid="early-access-page"] p').nth(1).inner_text()
        assert "Business Layer" in first_line, "the first line does not name the Business Layer: %r" % first_line
        assert "waiting list" in page.locator("body").inner_text().lower()
        shoot(page, "business-layer-early-access")
    finally:
        page.close()


def test_business_cases_button_is_shown_and_states_it_is_coming(browser, wave_server, org):
    page = browser.new_page()
    try:
        _login(page, wave_server, org["emails"]["cto"])
        page.goto(wave_server + "/business-model/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        button = page.get_by_role("link", name="Business Cases")
        assert button.count() >= 1, "the Business Cases button is missing from the canvases page"
        shoot(page, "canvases")
        button.first.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.locator('[data-testid="early-access-page"]').count() == 1, "Business Cases does not state it is coming"
        assert page.url.split("?")[0].endswith("/early-access/pg-bc"), page.url
        assert _heading(page) == "Business Cases", "the page Business Cases opens is headed %r" % _heading(page)
        blurb = page.locator('[data-testid="early-access-page"]').inner_text()
        assert "costs, benefits and options" in blurb, "the page does not say what Business Cases will do: %r" % blurb
        assert "Entelim is building this" not in blurb
        assert "waiting list" in page.locator("body").inner_text().lower()
        shoot(page, "business-cases-early-access")
    finally:
        page.close()


def test_every_capability_lens_carries_the_business_layer_crumb(browser, wave_server, org):
    page = browser.new_page()
    try:
        _login(page, wave_server, org["emails"]["cto"])
        missing = []
        for path in LENSES:
            response = page.goto(wave_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response is not None and response.status < 400, "%s answers with status %s" % (path, response and response.status)
            assert page.locator('[data-testid="early-access-page"]').count() == 0, "%s opens an early-access page" % path
            if page.locator("a").filter(has_text="Business Layer").count() == 0:
                missing.append(path)
        assert not missing, "pages with no Business Layer crumb: %s" % missing
    finally:
        page.close()


def test_capability_search_term_in_a_moved_link_is_kept_and_shown_in_the_search_box(browser, wave_server, org):
    """A person following an old search link for "Pay" lands on the capability list with Pay in the search box."""
    page = browser.new_page()
    try:
        _login(page, wave_server, org["emails"]["cto"])
        page.goto(wave_server + "/capability-maturity/search?q=Pay", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/capability-map/" in page.url and "view=list" in page.url, page.url
        assert "q=Pay" in page.url, "the search term was dropped from the address: %s" % page.url
        box = page.get_by_label("Search capabilities")
        page.wait_for_timeout(800)
        assert box.input_value() == "Pay", "the search box does not hold the term: %r" % box.input_value()
        assert "q=Pay" in page.url, "the page rewrote the address and lost the term: %s" % page.url
    finally:
        page.close()
