"""Composer focus mode, in a real browser: /archimate/composer?element=<id>&depth=<n>&derived=<0|1>.

The page opens an element's impact neighbourhood as an unsaved view. The canvas must hold
exactly what the impact endpoint answers for the same parameters, the centre must be selected
with neighbour focus applied, worked-out links must be drawn as worked out (and recorded links
must not), and the banner must offer Save as diagram and Discard. A failed fetch must never
look like a view.
"""
import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD
from tests.smoke.intelligence_graph import seed_impact_graph

PAGE_TIMEOUT = 30000
APP = '[x-data^="composerApp"]'


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _open(browser, live_server, seeded, query):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(live_server + "/archimate/composer" + query, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return context, page


def _canvas(page):
    return page.evaluate(
        """(sel) => {
            const app = Alpine.$data(document.querySelector(sel));
            const nodes = app.graph.getElements().filter(c => c.get('elementId') != null);
            const links = app.graph.getLinks();
            return {
                nodeIds: nodes.map(c => String(c.get('elementId'))).sort(),
                linkKeys: links.map(l => String(l.get('edgeKey'))).sort(),
                selected: (app._selectedCells || []).map(c => String(c.get('elementId'))),
                focused: app._focusedNodeId != null,
            };
        }""",
        APP,
    )


def _answer(page, centre, depth, derived):
    """What the impact endpoint itself returns for the same parameters, reduced to the
    connection keys and element ids the Twin Map would draw from it."""
    return page.evaluate(
        """async ([id, depth, derived]) => {
            const flag = derived ? 'true' : 'false';
            const r = await fetch('/api/v1/intelligence/impact/' + id + '?include_derived=' + flag +
                '&include_stale=' + flag + '&max_depth=' + depth + '&with_owner=true',
                {credentials: 'same-origin', headers: {Accept: 'application/json'}});
            const body = await r.json();
            const data = body.data || body;
            const keys = [];
            const ids = new Set([String(id)]);
            (data.rows || []).forEach(row => {
                const rel = row.relation || {};
                const chain = rel.chain_elements || [];
                const derivedRow = rel.kind === 'derived';
                const from = derivedRow ? id : (chain.length >= 2 ? chain[chain.length - 2] : id);
                keys.push(derivedRow
                    ? 'derived-' + (rel.derived_id != null ? rel.derived_id : row.element_id + '-' + rel.depth)
                    : 'explicit-' + row.element_id + '-' + rel.depth);
                ids.add(String(row.element_id));
                ids.add(String(from));
            });
            return {keys: keys.sort(), ids: Array.from(ids).sort()};
        }""",
        [centre, depth, derived],
    )


def _wait_loaded(page):
    page.wait_for_function(
        """(sel) => { const a = Alpine.$data(document.querySelector(sel)); return a.focusView || /Failed/.test(a.statusText || ''); }""",
        arg=APP,
        timeout=PAGE_TIMEOUT,
    )


def _saved_diagram_count():
    from app import create_app
    from app.models.archimate_core import SavedDiagram

    app = create_app("testing")
    with app.app_context():
        return SavedDiagram.query.count()


@pytest.fixture(scope="module")
def graph(seeded):
    return seed_impact_graph(seeded["ids"]["org"])


@pytest.mark.smoke
def test_canvas_matches_the_impact_answer(browser, live_server, seeded, graph):
    centre = graph["service"]
    context, page = _open(browser, live_server, seeded, "?element=%s&depth=1&derived=1" % centre)
    try:
        _wait_loaded(page)
        canvas = _canvas(page)
        answer = _answer(page, centre, 1, True)
        # Every connection in the answer is drawn, and nothing else; the same for elements.
        assert canvas["linkKeys"] == answer["keys"]
        assert canvas["nodeIds"] == answer["ids"]
        assert str(centre) in canvas["nodeIds"]
        # The centre is selected and neighbour focus is applied.
        assert canvas["selected"] == [str(centre)]
        assert canvas["focused"] is True
        assert page.locator("g.highlighted").count() > 0
        # Worked-out links are marked and badged; recorded links are not.
        derived = page.locator('[data-kind="derived"]')
        recorded = page.locator('[data-kind="recorded"]')
        assert derived.count() >= 1
        assert recorded.count() >= 1
        assert derived.first.locator("text", has_text="Worked out").count() >= 1
        assert recorded.first.locator("text", has_text="Worked out").count() == 0
        # The banner names the centre and the hop count (singular for one).
        banner = page.locator("#composer-focus-banner")
        expect(banner).to_be_visible()
        expect(page.locator("#composer-focus-title")).to_have_text(
            "Unsaved view: %s, 1 hop" % graph["names"]["service"]
        )
        expect(page.locator("#composer-focus-save")).to_be_visible()
        expect(page.locator("#composer-focus-discard")).to_be_visible()
        # The editor treats it as unsaved.
        assert page.evaluate("(s) => Alpine.$data(document.querySelector(s)).viewpointDirty", APP) is True
        assert page.evaluate("(s) => Alpine.$data(document.querySelector(s)).currentSavedVpId", APP) is None
    finally:
        context.close()


@pytest.mark.smoke
def test_derived_off_drops_the_worked_out_link(browser, live_server, seeded, graph):
    centre = graph["service"]
    context, page = _open(browser, live_server, seeded, "?element=%s&depth=3&derived=0" % centre)
    try:
        _wait_loaded(page)
        canvas = _canvas(page)
        answer = _answer(page, centre, 3, False)
        assert canvas["linkKeys"] == answer["keys"]
        assert canvas["nodeIds"] == answer["ids"]
        assert page.locator('[data-kind="derived"]').count() == 0
        assert page.locator('[data-kind="recorded"]').count() >= 1
        expect(page.locator("#composer-focus-title")).to_have_text(
            "Unsaved view: %s, 3 hops" % graph["names"]["service"]
        )
        expect(page.locator("#composer-focus-banner")).not_to_contain_text("Worked-out connections are shown")
    finally:
        context.close()


@pytest.mark.smoke
def test_save_creates_one_diagram_of_the_loaded_elements(browser, live_server, seeded, graph):
    centre = graph["service"]
    context, page = _open(browser, live_server, seeded, "?element=%s&depth=1&derived=1" % centre)
    try:
        _wait_loaded(page)
        loaded = sorted(_canvas(page)["nodeIds"])
        before = _saved_diagram_count()
        page.locator("#composer-focus-save").click()
        page.wait_for_url(lambda url: "viewpoint_id=" in url, timeout=PAGE_TIMEOUT)
        assert _saved_diagram_count() == before + 1
        from app import create_app
        from app.models.archimate_core import SavedDiagram, SavedDiagramElement

        app = create_app("testing")
        with app.app_context():
            diagram_id = int(page.url.split("viewpoint_id=")[1].split("&")[0])
            ids = sorted(
                str(r.element_id)
                for r in SavedDiagramElement.query.filter_by(diagram_id=diagram_id).all()
            )
            assert ids == loaded
            assert SavedDiagram.query.get(diagram_id).name.endswith("and neighbours, 1 hop")
    finally:
        context.close()


@pytest.mark.smoke
def test_discard_empties_the_canvas_and_saves_nothing(browser, live_server, seeded, graph):
    centre = graph["service"]
    context, page = _open(browser, live_server, seeded, "?element=%s&depth=1&derived=1" % centre)
    try:
        _wait_loaded(page)
        before = _saved_diagram_count()
        page.locator("#composer-focus-discard").click()
        expect(page.locator("#composer-focus-banner")).to_be_hidden()
        assert _canvas(page)["nodeIds"] == []
        assert "element=" not in page.url
        assert _saved_diagram_count() == before
    finally:
        context.close()


@pytest.mark.smoke
def test_leaving_the_unsaved_view_triggers_the_unsaved_changes_prompt(browser, live_server, seeded, graph):
    centre = graph["service"]
    context, page = _open(browser, live_server, seeded, "?element=%s&depth=1&derived=1" % centre)
    try:
        _wait_loaded(page)
        prevented = page.evaluate(
            "() => { const e = new Event('beforeunload', {cancelable: true}); window.dispatchEvent(e); return e.defaultPrevented; }"
        )
        assert prevented is True
    finally:
        context.close()


@pytest.mark.smoke
def test_a_failed_fetch_is_never_shown_as_a_view(browser, live_server, seeded, graph):
    centre = graph["service"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        page.route(
            "**/api/v1/intelligence/impact/**",
            lambda route: route.fulfill(status=500, content_type="application/json", body='{"error": "boom"}'),
        )
        page.goto(
            live_server + "/archimate/composer?element=%s&depth=1&derived=1" % centre,
            wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
        )
        _wait_loaded(page)
        status = page.evaluate("(s) => Alpine.$data(document.querySelector(s)).statusText", APP)
        assert "Failed" in status
        expect(page.locator("#composer-focus-banner")).to_be_hidden()
        assert "Unsaved view" not in page.locator("body").inner_text()
        assert _canvas(page)["nodeIds"] == []
    finally:
        context.close()
