"""Journey: the rendered sidebar box, in a real browser with real Alpine.

Layered the same way as the backend contract test (test_journey_sidebar_search_link_level.py):
  * app/static/js/sidebar/module_search.js in isolation, against canned responses;
  * the real sidebar template, real Alpine, real /api/sidebar/search endpoint.
"""
import json
from pathlib import Path

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey

STATIC = Path(__file__).resolve().parents[2] / "app" / "static"
MODULE = STATIC / "js" / "sidebar" / "module_search.js"

HARNESS = """<!doctype html><html><body>
<script src="%s"></script>
<script>
window.log = {fetches: [], states: []};
window.pending = [];
window.finder = SidebarModuleSearch.create({
  delay: 20,
  fetchJson: function (q) {
    window.log.fetches.push(q);
    return new Promise(function (resolve, reject) { window.pending.push({q: q, resolve: resolve, reject: reject}); });
  },
  onChange: function (s) { window.log.states.push(JSON.parse(JSON.stringify(s))); }
});
</script></body></html>"""

CANNED = {
    "results": [
        {"type": "application", "id": 1, "name": "Impact Portal", "url": "/applications/1"},
        {"type": "module", "id": "strategic.impact_analysis", "name": "Impact Analysis",
         "url": "/strategic/impact-analysis", "zone": "My work"},
    ],
    "total_count": 2,
}


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        chromium = p.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser, tmp_path_factory):
    html = tmp_path_factory.mktemp("sidebar_search") / "harness.html"
    html.write_text(HARNESS % MODULE.as_uri(), encoding="utf-8")
    pg = browser.new_page()
    pg.goto(html.as_uri())
    yield pg
    pg.close()


def _reset(page):
    page.evaluate("() => { window.log.fetches.length = 0; window.log.states.length = 0; window.pending.length = 0; }")


def _last(page):
    return page.evaluate("() => window.log.states[window.log.states.length - 1]")


def test_a_one_character_query_never_calls_the_server(page):
    _reset(page)
    page.evaluate("() => window.finder.search('i')")
    page.wait_for_timeout(120)
    assert page.evaluate("() => window.log.fetches") == []
    assert _last(page)["results"] == []


def test_only_module_hits_are_returned_with_their_zone(page):
    _reset(page)
    page.evaluate("() => window.finder.search('impact')")
    page.wait_for_function("() => window.pending.length === 1")
    page.evaluate("(body) => window.pending[0].resolve(body)", CANNED)
    page.wait_for_function("() => window.log.states.length && !window.log.states[window.log.states.length - 1].loading")
    assert _last(page)["results"] == [{"name": "Impact Analysis", "url": "/strategic/impact-analysis", "zone": "My work"}]


def test_a_slow_earlier_response_cannot_overwrite_a_newer_one(page):
    _reset(page)
    page.evaluate("() => window.finder.search('capab')")
    page.wait_for_function("() => window.pending.length === 1")
    page.evaluate("() => window.finder.search('impact')")
    page.wait_for_function("() => window.pending.length === 2")
    newer = {"results": [{"type": "module", "id": "x", "name": "Impact Analysis", "url": "/strategic/impact-analysis", "zone": "My work"}]}
    older = {"results": [{"type": "module", "id": "y", "name": "Capability Map", "url": "/capability-map/", "zone": "Library"}]}
    page.evaluate("(b) => window.pending[1].resolve(b)", newer)
    page.wait_for_function("() => window.log.states.some(s => s.results.length === 1 && s.results[0].name === 'Impact Analysis')")
    page.evaluate("(b) => window.pending[0].resolve(b)", older)
    page.wait_for_timeout(100)
    assert _last(page)["results"] == [{"name": "Impact Analysis", "url": "/strategic/impact-analysis", "zone": "My work"}]


def test_a_failed_request_is_reported_as_an_error_not_as_no_results(page):
    _reset(page)
    page.evaluate("() => window.finder.search('impact')")
    page.wait_for_function("() => window.pending.length === 1")
    page.evaluate("() => window.pending[0].reject(new Error('boom'))")
    page.wait_for_function("() => window.log.states[window.log.states.length - 1].error === true")
    state = _last(page)
    assert state["results"] == [] and state["loading"] is False


def test_a_module_result_with_no_zone_carries_an_empty_label(page):
    _reset(page)
    page.evaluate("() => window.finder.search('impact')")
    page.wait_for_function("() => window.pending.length === 1")
    body = {"results": [{"type": "module", "id": "x", "name": "Impact Analysis", "url": "/strategic/impact-analysis"}]}
    page.evaluate("(b) => window.pending[0].resolve(b)", body)
    page.wait_for_function("() => window.log.states.length && !window.log.states[window.log.states.length - 1].loading")
    # Navigation v3: every page has a sidebar entry; a hit with none says nothing rather than
    # pointing at a directory the sidebar no longer links.
    assert _last(page)["results"][0]["zone"] == ""


# ---- the real sidebar, real Alpine, real endpoint -----------------------------------------------------

def _persona(app, enterprise_role):
    from datetime import datetime

    from app import db
    from app.models.user import User

    with app.app_context():
        org_id = make_org(db, "SidebarSearchRendered")
        user_id = make_user(db, org_id, "u", enterprise_role, role_name="Architect")
        # This journey tests sidebar search, not first-login onboarding. A fresh user with an
        # empty workspace (no applications/elements/capabilities/vendors seeded) is exactly what
        # trips admin_base.html's first-login onboarding modal (onboarding_completed_at IS NULL
        # and the workspace is empty) -- it renders on top of the real sidebar these tests drive
        # and can intercept the clicks/keystrokes aimed at the search box underneath. Marking the
        # persona as already onboarded represents the returning user this journey is actually
        # about, rather than forcing clicks past a modal that's correctly doing its job.
        User.query.filter_by(id=user_id).update({"onboarding_completed_at": datetime.utcnow()})
        db.session.commit()
        return user_id


def _serve_app_in_browser(pg, client, document, path):
    from urllib.parse import parse_qsl, urlparse

    def handle(route):
        url = urlparse(route.request.url)
        if url.path.startswith("/static/"):
            f = STATIC / url.path[len("/static/"):]
            return route.fulfill(path=str(f)) if f.is_file() else route.fulfill(status=404, body="")
        if url.path == "/api/sidebar/search":
            r = client.get(url.path, query_string=dict(parse_qsl(url.query)))
            return route.fulfill(status=r.status_code, content_type="application/json", body=r.get_data())
        if url.path == path:
            return route.fulfill(status=200, content_type="text/html", body=document)
        return route.fulfill(status=204, body="")

    pg.route("http://app.test/**", handle)


def test_typing_in_the_real_sidebar_shows_a_link_level_result_with_zone(app, client, browser):
    # A CTO's sidebar carries no Stakeholder map entry, so a hit can only have come from the search.
    login(client, _persona(app, "cto"))
    page_path = "/dashboard/overview"
    document = client.get(page_path).get_data(as_text=True)
    assert 'href="/stakeholders/map"' not in document

    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _serve_app_in_browser(pg, client, document, page_path)
        pg.goto("http://app.test" + page_path)
        pg.fill("#sidebar-search", "stakeholder map")
        hit = pg.locator('[data-testid="sidebar-search-results"] a[href="/stakeholders/map"]')
        hit.wait_for(state="visible", timeout=10000)
        name, section = [part.strip() for part in hit.inner_text().strip().split("\n") if part.strip()]
        assert (name, section) == ("Stakeholder map", "Systems")
    finally:
        pg.close()


def test_only_matching_pages_show_not_the_whole_sidebar(app, client, browser):
    """A hit on one page must not pull in unrelated pages."""
    login(client, _persona(app, "solution_architect"))
    page_path = "/dashboard/overview"
    document = client.get(page_path).get_data(as_text=True)
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _serve_app_in_browser(pg, client, document, page_path)
        pg.goto("http://app.test" + page_path)
        pg.fill("#sidebar-search", "vendor")
        pg.wait_for_selector('[data-testid="sidebar-search-page"]')
        pg.wait_for_timeout(300)
        rows = [r.strip().split("\n")[0] for r in pg.locator('[data-testid="sidebar-search-page"]').all_inner_texts()]
        assert "Vendors" in rows, rows
        assert not [r for r in rows if r in ("Capability map", "Compliance", "Changes", "Home")], rows
    finally:
        pg.close()


def test_no_match_offers_to_ask_the_ai_chat_and_the_hint_stays_visible(app, client, browser):
    login(client, _persona(app, "solution_architect"))
    page_path = "/dashboard/overview"
    document = client.get(page_path).get_data(as_text=True)
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _serve_app_in_browser(pg, client, document, page_path)
        pg.goto("http://app.test" + page_path)
        pg.fill("#sidebar-search", "zzzznomatch")
        pg.wait_for_selector('[data-testid="sidebar-search-results"]', state="visible")
        pg.locator('[data-testid="sidebar-search-results"] p', has_text="Searching").wait_for(state="hidden", timeout=5000)
        results = pg.locator('[data-testid="sidebar-search-results"]')
        assert "No page matches. Press Enter to ask the AI chat." in results.inner_text()
        ask = results.locator('[data-testid="sidebar-search-ask"]')
        assert ask.is_visible() and "zzzznomatch" in ask.inner_text()
        assert "q=zzzznomatch" in ask.get_attribute("href")
        assert pg.locator("kbd", has_text="K").first.is_visible()
    finally:
        pg.close()


def test_clearing_the_box_restores_the_sidebar(app, client, browser):
    login(client, _persona(app, "solution_architect"))
    page_path = "/dashboard/overview"
    document = client.get(page_path).get_data(as_text=True)
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _serve_app_in_browser(pg, client, document, page_path)
        pg.goto("http://app.test" + page_path)
        before = pg.locator("#sidebar-nav").inner_text()
        pg.fill("#sidebar-search", "vendor")
        pg.wait_for_selector('[data-testid="sidebar-search-page"]')
        pg.press("#sidebar-search", "Escape")
        pg.wait_for_timeout(200)
        assert pg.input_value("#sidebar-search") == ""
        assert not pg.locator('[data-testid="sidebar-search-results"]').is_visible()
        after = pg.locator("#sidebar-nav").inner_text()
        assert before == after
    finally:
        pg.close()
