"""The Twin Map's "Open in ArchiMate Composer" action follows the map's own state."""
import pytest

from tests.smoke.conftest import PASSWORD
from tests.smoke.intelligence_graph import seed_impact_graph

PAGE_TIMEOUT = 30000
LINK = "#twin-open-composer"


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _query(page):
    href = page.locator(LINK).get_attribute("href")
    assert href.startswith("/archimate/composer?"), href
    return dict(part.split("=") for part in href.split("?", 1)[1].split("&"))


@pytest.mark.smoke
def test_action_href_follows_centre_depth_and_toggle(browser, live_server, seeded):
    graph = seed_impact_graph(seeded["ids"]["org"])
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto("%s/intelligence/twin-map" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_function("() => document.querySelector('[x-data=\"twinMapSurface()\"]')._x_dataStack")
        # no centre yet: the action is not offered
        assert not page.locator(LINK).is_visible()

        page.goto("%s/intelligence/twin-map?element=%s" % (live_server, graph["service"]),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector("[data-graph-nodes] button", state="visible")
        assert page.locator(LINK).is_visible()
        assert _query(page) == {"element": str(graph["service"]), "depth": "3", "derived": "1"}

        # slider
        page.locator("#twin-hop-depth").focus()
        with page.expect_response(lambda r: "/impact/" in r.url and "max_depth=" in r.url):
            page.keyboard.press("Home")
        assert _query(page)["depth"] == "1"
        # toggle
        with page.expect_response(lambda r: "/impact/" in r.url and "include_derived=false" in r.url):
            page.locator("#twin-show-derived").uncheck()
        assert _query(page)["derived"] == "0"
        # recentre on another element
        page.get_by_role("button", name="Centre on this").first.click()
        page.wait_for_function(
            "(c) => document.querySelector('#twin-open-composer').getAttribute('href').indexOf('element=' + c + '&') === -1",
            arg=str(graph["service"]))
        other = _query(page)["element"]
        assert other != str(graph["service"])

        # keyboard: reachable by Tab, activates with Enter
        page.locator(LINK).focus()
        assert page.evaluate("() => document.activeElement.id") == "twin-open-composer"
        page.keyboard.press("Enter")
        page.wait_for_url(lambda url: "/archimate/composer" in url, timeout=PAGE_TIMEOUT)
        assert "element=%s" % other in page.url

        # classes and button rule
        page.goto("%s/intelligence/twin-map?element=%s" % (live_server, graph["service"]),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector(LINK, state="visible")
        classes = page.locator(LINK).get_attribute("class")
        assert "border-input" in classes and "bg-background" in classes and "bg-primary" not in classes
        assert page.locator("a.bg-primary, button.bg-primary").count() <= 1
    finally:
        context.close()
