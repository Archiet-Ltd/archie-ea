"""Journey: /ai-chat opens at the greeting, and a clipped notice never reads as an overlap.

Reported problem: a fragment of an element poked out above the "QUICK PROMPTS" label. It was not a chip. On load,
_syncPersonaFromSelector() calls _applyPersonaChange(value, false) and updatePersonaUI() then wrote a "Persona
switched to: ..." notice into the transcript unconditionally. appendSystemMessage() scrolls the pane to the bottom
before the welcome content above it has finished growing, so the pane opened scrolled past the greeting and the
notice sat about 20px past the pane's bottom edge at 1440x900, 1024x768 and 768x900. The same 20px at every size
is why the cause is behavioural, not a width problem.

The second argument of _applyPersonaChange is "persist the choice": false on load, true for a real user change. The
notice is now written only for a real change.

The real page is rendered for a logged-in persona and driven in a browser; static files come from disk and the
page's own GET requests are answered by the Flask app. Message sends are answered with a canned error so no model
is called.
"""
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey

STATIC = Path(__file__).resolve().parents[2] / "app" / "static"
PANE = "#messages-container"
VIEWPORTS = [(1440, 900), (1024, 768), (768, 900)]


def _document(app, client, persona):
    from app import db

    with app.app_context():
        org = make_org(db, "AiChatOpen")
        user = make_user(db, org, "u", persona, role_name="Architect")
    login(client, user)
    response = client.get("/ai-chat")
    assert response.status_code == 200, "%s cannot open /ai-chat (%s)" % (persona, response.status_code)
    return response.get_data(as_text=True)


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        chromium = p.chromium.launch()
        yield chromium
        chromium.close()


def _open(browser, client, document, width=1440, height=900, query=""):
    pg = browser.new_page(viewport={"width": width, "height": height})

    def handle(route):
        request = route.request
        url = urlparse(request.url)
        if url.path.startswith("/static/"):
            f = STATIC / url.path[len("/static/"):]
            return route.fulfill(path=str(f)) if f.is_file() else route.fulfill(status=404, body="")
        if url.path == "/ai-chat" and request.method == "GET":
            return route.fulfill(status=200, content_type="text/html", body=document)
        if request.method != "GET":
            return route.fulfill(status=500, content_type="application/json",
                                 body=json.dumps({"success": False, "error": "stubbed for the test"}))
        if url.path.startswith(("/api/", "/ai-chat/")):
            r = client.get(url.path, query_string=dict(parse_qsl(url.query)))
            return route.fulfill(status=r.status_code, content_type=r.content_type or "application/json", body=r.get_data())
        return route.fulfill(status=204, body="")

    pg.route("http://app.test/**", handle)
    pg.goto("http://app.test/ai-chat" + query)
    pg.wait_for_selector(PANE, timeout=15000)
    pg.wait_for_timeout(2500)  # let the welcome content, briefing box and icons settle
    return pg


def _pane_state(pg):
    return pg.evaluate("""() => {
        const pane = document.querySelector('%s'); const box = pane.getBoundingClientRect();
        const heading = [...pane.querySelectorAll('h2')].find(h => /How can I help you today/.test(h.textContent));
        const hb = heading ? heading.getBoundingClientRect() : null;
        return {scrollTop: pane.scrollTop, paneTop: box.top, paneBottom: box.bottom,
                headingVisible: !!hb && hb.top >= box.top - 1 && hb.bottom <= box.bottom + 1,
                notices: [...pane.querySelectorAll('div')].filter(d => /Persona switched to/.test(d.textContent) && d.children.length <= 1 && d.textContent.trim().startsWith('Persona switched to')).length};
    }""" % PANE)


@pytest.mark.parametrize("persona", ["solution_architect", "enterprise_architect", "data_architect"])
def test_on_load_the_pane_is_at_the_top_with_the_greeting_and_no_persona_notice(app, browser, client, persona):
    pg = _open(browser, client, _document(app, client, persona))
    try:
        state = _pane_state(pg)
        assert state["scrollTop"] == 0, "the pane opened scrolled %spx past the greeting" % state["scrollTop"]
        assert state["headingVisible"], "the heading 'How can I help you today?' is not visible in the pane"
        assert state["notices"] == 0, "a 'Persona switched to' notice was written on load"
    finally:
        pg.close()


def _deep_link_notice_state(pg, text_pattern):
    """Where the deep-link context notice actually landed, and how far past the pane's
    bottom edge it ends (positive == clipped)."""
    return pg.evaluate("""(pattern) => {
        const pane = document.querySelector('%s'); const box = pane.getBoundingClientRect();
        const re = new RegExp(pattern);
        const n = [...pane.querySelectorAll('div')].find(d => re.test(d.textContent) && d.children.length <= 1);
        if (!n) return null; const r = n.getBoundingClientRect(); return {bottom: r.bottom, paneBottom: box.bottom};
    }""" % PANE, text_pattern)


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_a_deep_link_context_notice_does_not_scroll_the_pane_past_the_greeting(app, browser, client, width, height):
    """A sibling of the bug the persona-notice test above fixes: on a deep link, the same
    "pane opens scrolled past the greeting" symptom occurred, plus a second one only a deep
    link exposes.

    Both are fixed in app.js/render.js/index.html:
      1. Same as the persona-notice bug: appendSystemMessage() scrolled the pane to the bottom
         before the welcome content above it had finished growing. appendSystemMessage() now
         takes an opts.placement === 'top' that inserts the notice above the greeting instead
         of scrolling to it; both deep-link call sites pass it.
      2. Deep-link-only: even at scrollTop 0, the ~1000px-tall #domain-welcome-grid (5 persona
         cards + portfolio briefing + 3+3 domain cards) sat above the notice, which would push
         it hundreds of px below the pane's visible area regardless of scroll position --
         opts.placement === 'top' already avoids this on its own (the notice becomes the pane's
         first child, above the grid, not merely unscrolled-to), but the deep-link handler also
         calls _hideWelcomeSuggestions() as belt-and-braces, collapsing the same
         browse-and-pick cards (index.html's #domain-welcome-suggestions wrapper) that a deep
         link -- already headed somewhere specific -- does not need shown at all."""
    pg = _open(browser, client, _document(app, client, "solution_architect"), width, height,
               query="?element_id=7&context_type=vendor&domain=vendor_intelligence")
    try:
        state = _pane_state(pg)
        assert state["scrollTop"] == 0, (
            "the pane opened scrolled %spx past the greeting with a deep link at %dx%d" % (state["scrollTop"], width, height)
        )
        assert state["headingVisible"], (
            "the heading 'How can I help you today?' is not visible with a deep link at %dx%d" % (width, height)
        )
        notice = _deep_link_notice_state(pg, "Vendor context loaded")
        assert notice, "the deep-link context notice was not written at all"
        assert notice["bottom"] <= notice["paneBottom"] + 1, (
            "the deep-link notice ends %.0fpx below the pane's bottom edge at %dx%d" % (notice["bottom"] - notice["paneBottom"], width, height)
        )
    finally:
        pg.close()


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_an_application_deep_link_context_notice_does_not_scroll_the_pane_past_the_greeting(
    app, browser, client, width, height
):
    """The other deep-link shape (?context=application&id=), same fix: app.js's
    ?context=application&id= branch also calls appendSystemMessage() during the load pass
    and now passes { placement: 'top' } too."""
    pg = _open(browser, client, _document(app, client, "solution_architect"), width, height,
               query="?context=application&id=42")
    try:
        state = _pane_state(pg)
        assert state["scrollTop"] == 0, (
            "the pane opened scrolled %spx past the greeting with an application deep link at %dx%d" % (state["scrollTop"], width, height)
        )
        assert state["headingVisible"], (
            "the heading 'How can I help you today?' is not visible with an application deep link at %dx%d" % (width, height)
        )
        notice = _deep_link_notice_state(pg, "Application context loaded")
        assert notice, "the application deep-link context notice was not written at all"
        assert notice["bottom"] <= notice["paneBottom"] + 1, (
            "the application deep-link notice ends %.0fpx below the pane's bottom edge at %dx%d" % (notice["bottom"] - notice["paneBottom"], width, height)
        )
    finally:
        pg.close()


@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_a_real_persona_change_adds_a_notice_that_ends_inside_the_pane(app, browser, client, width, height):
    pg = _open(browser, client, _document(app, client, "solution_architect"), width, height)
    try:
        current = pg.eval_on_selector("#persona-selector", "s => s.value")
        other = pg.eval_on_selector(
            "#persona-selector", "(s, cur) => [...s.options].map(o => o.value).find(v => v && v !== cur)", current)
        if not other:
            pytest.skip("only one persona is configured; there is no second persona to switch to")
        pg.select_option("#persona-selector", other)
        pg.wait_for_timeout(800)
        info = pg.evaluate("""() => {
            const pane = document.querySelector('%s'); const box = pane.getBoundingClientRect();
            const notice = [...pane.querySelectorAll('div.justify-center')]
                .filter(d => d.textContent.trim().startsWith('Persona switched to')).pop();
            if (!notice) return null;
            const r = notice.getBoundingClientRect(); return {bottom: r.bottom, paneBottom: box.bottom};
        }""" % PANE)
        assert info, "a real persona change did not add the notice"
        assert info["bottom"] <= info["paneBottom"] + 1, (
            "the notice ends %.0fpx below the pane's bottom edge at %dx%d" % (info["bottom"] - info["paneBottom"], width, height)
        )
    finally:
        pg.close()


def test_after_sending_a_message_the_pane_scrolls_to_the_newest_message(app, browser, client):
    pg = _open(browser, client, _document(app, client, "solution_architect"))
    try:
        pg.fill("#user-input", "Which capabilities have no owner?")
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(1500)
        info = pg.evaluate("""() => { const p = document.querySelector('%s');
            return {gap: p.scrollHeight - p.clientHeight - p.scrollTop, scrollable: p.scrollHeight > p.clientHeight,
                    sent: p.textContent.includes('Which capabilities have no owner?')}; }""" % PANE)
        assert info["sent"], "the sent message is not in the transcript"
        assert not info["scrollable"] or info["gap"] <= 2, "the pane is %spx short of the newest message" % info["gap"]
    finally:
        pg.close()


@pytest.mark.parametrize("width,height", [(1440, 900), (768, 900)])
def test_the_quick_prompts_strip_has_a_top_edge_and_nothing_sits_on_its_label(app, browser, client, width, height):
    pg = _open(browser, client, _document(app, client, "solution_architect"), width, height)
    try:
        info = pg.evaluate("""() => {
            const label = [...document.querySelectorAll('div')].find(d => d.children.length === 0
                && d.textContent.trim().toLowerCase() === 'quick prompts');
            if (!label) return null;
            const r = label.getBoundingClientRect();
            // What is really drawn at the label's position: pane content that has scrolled out of view is
            // clipped by the pane and never covers it, so ask the browser rather than comparing raw rectangles.
            const pane = document.querySelector('%s');
            const cy = r.top + r.height / 2;
            const overlapping = [0.1, 0.5, 0.9].filter(f => {
                const hit = document.elementFromPoint(r.left + r.width * f, cy);
                return hit && pane.contains(hit);
            }).length;
            return {edge: parseFloat(getComputedStyle(label).borderTopWidth), overlapping: overlapping};
        }""" % PANE)
        assert info, "the Quick prompts label was not found"
        assert info["edge"] > 0, "the quick-prompts strip has no visible top edge"
        assert info["overlapping"] == 0, "pane content is drawn over the Quick prompts label at %d of 3 sample points" % info["overlapping"]
    finally:
        pg.close()
