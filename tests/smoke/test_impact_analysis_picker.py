"""Impact Analysis, driven as the people who use it: find an element by its name, run the analysis.

What the checks below guard, each one seen in a real browser:

    the picker made a layer choice a precondition of a query, and its layer-scoped listing dropped
    every element stored as application_component, so "Smoke impact source" was found by Ctrl+K and
    not by the page whose whole job is to analyse it
    "Compare Scenarios" and "Create Change Request" were the two saturated buttons; "Analyze Impact",
    the action the page exists for, was pale and disabled
    the Analyze button drew a play glyph and a spinner side by side, because Lucide swaps each <i> for
    an <svg> and the x-show placed on the <i> is lost
    the AI recommendations card started 2 px under the cards above it

What the picker matches: an element's name, the way the global search does. The layer-scoped listing it
replaced also matched words in an element's description; the picker no longer does, on purpose.

How many it lists: at most 50, in name order. A text that matches 50 elements or fewer lists every one of
them; a text that matches more lists the first 50 by name and says so, and the other-layer counts that an
empty result offers read "at least N" when they come from a full list.

The typed text is matched literally: percent, underscore and backslash are escaped before it is sent.

Two people use this page: the solution architect and the enterprise architect fixtures.
"""

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# Playwright's expect() keeps its own 5 s budget and ignores the context's default timeout, so an
# analysis that takes longer than that under a loaded server failed an assertion that was right. Give
# every assertion in this file the page timeout the rest of the tier uses, and put the default back.
EXPECT_DEFAULT_TIMEOUT = 5000


@pytest.fixture(scope="module", autouse=True)
def _assertions_wait_as_long_as_pages_do():
    expect.set_options(timeout=PAGE_TIMEOUT)
    yield
    expect.set_options(timeout=EXPECT_DEFAULT_TIMEOUT)


PAGE ="/strategic/impact-analysis"
PERSONAS = ["solution_architect", "enterprise_architect"]
SEARCH = 'input[aria-label="Type to search..."]'
LAYER = 'select[aria-label="Architecture Layer"]'
ROWS = "div.max-h-48 button"
STATUS = 'div[role="status"]:has-text("No elements match")'

READY = """() => {
  const root = document.querySelector('[x-data="impactAnalysis()"]');
  return !!(window.Alpine && root && root._x_dataStack);
}"""

ANALYZE_SVGS = """() => {
  const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim().startsWith('Analyze Impact'));
  return [...b.querySelectorAll('svg')]
      .filter(s => { const r = s.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
      .map(s => s.getAttribute('class') || '');
}"""

GEOMETRY = """() => {
  const cards = [...document.querySelectorAll('[data-slot="card"]')];
  const gridBottom = Math.max(...cards.map(c => c.getBoundingClientRect().bottom));
  const rec = [...document.querySelectorAll('h3')].find(h => h.textContent.includes('AI-Powered Recommendations'))
      .closest('.rounded-xl').getBoundingClientRect();
  const histBtn = [...document.querySelectorAll('button')].find(b => b.textContent.includes('Recent Analyses'));
  const hist = histBtn ? histBtn.closest('.rounded-xl') : null;
  const histShown = !!hist && getComputedStyle(hist).display !== 'none' && hist.getBoundingClientRect().height > 0;
  return {
    gridToRecs: rec.top - gridBottom,
    historyShown: histShown,
    historyToRecs: histShown ? rec.top - hist.getBoundingClientRect().bottom : null,
  };
}"""


@pytest.fixture(scope="module")
def picker_data(seeded):
    """Rows the checks need beyond the harness seed: both type spellings, a business layer, dependencies.

    Removed again afterwards; the database is shared by every smoke journey in the session.
    """
    from app import create_app, db

    app = create_app("testing")
    tag = "Picker%s" % uuid.uuid4().hex[:6]
    made = []
    with app.app_context():
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

        org = seeded["ids"]["org"]

        def element(name, type_, layer):
            row = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org)
            db.session.add(row)
            db.session.commit()
            made.append(row.id)
            return row.id

        snake = element("%s spelled snake" % tag, "application_component", "application")
        camel = element("%s spelled camel" % tag, "ApplicationComponent", "application")
        element("%s ledger" % tag, "business_process", "business")
        cap_snake = element("%s cap snake" % tag, "business_capability", "business")
        cap_camel = element("%s cap camel" % tag, "BusinessCapability", "business")
        relationships = []
        for target in (cap_snake, cap_camel):
            rel = ArchiMateRelationship(type="serving", source_id=snake, target_id=target, organization_id=org)
            db.session.add(rel)
            db.session.commit()
            relationships.append(rel.id)

        source_name = db.session.get(ArchiMateElement, seeded["ids"]["impact_source_element"]).name
        target_name = db.session.get(ArchiMateElement, seeded["ids"]["impact_target_element"]).name
        radar_name = db.session.get(ArchiMateElement, seeded["ids"]["radar_element"]).name

    yield {
        "tag": tag, "snake_name": "%s spelled snake" % tag, "camel_name": "%s spelled camel" % tag,
        "source_name": source_name, "target_name": target_name, "radar_name": radar_name,
        "snake": snake, "camel": camel,
    }

    with app.app_context():
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

        for rel_id in relationships:
            ArchiMateRelationship.query.filter_by(id=rel_id).delete(synchronize_session=False)
        for element_id in made:
            ArchiMateElement.query.filter_by(id=element_id).delete(synchronize_session=False)
        db.session.commit()


@pytest.fixture(scope="module")
def bulk_data(seeded):
    """More matches than the picker's limit, and look-alike names for the literal-match checks.

    Removed again afterwards; the database is shared by every smoke journey in the session.
    """
    from app import create_app, db

    app = create_app("testing")
    tag = uuid.uuid4().hex[:6]
    backslash = chr(92)
    made = []
    with app.app_context():
        from app.models.archimate_core import ArchiMateElement

        org = seeded["ids"]["org"]
        rows = [
            ArchiMateElement(name="Bulk%s cap %02d" % (tag, n), type="business_process", layer="business",
                             organization_id=org)
            for n in range(55)
        ]
        names = {
            "underscore": "W%s_Billing" % tag,
            "look_alike": "W%sXBilling" % tag,
            "percent": "Rate %s 50%% off" % tag,
            "other_percent": "Rate %s 500 off" % tag,
            "backslash": "Path %s A%sB" % (tag, backslash),
        }
        rows += [ArchiMateElement(name=name, type="application_component", layer="application", organization_id=org)
                 for name in names.values()]
        db.session.add_all(rows)
        db.session.commit()
        made = [row.id for row in rows]

    yield {"cap_prefix": "Bulk%s cap" % tag, "names": names, "tag": tag}

    with app.app_context():
        from app.models.archimate_core import ArchiMateElement

        ArchiMateElement.query.filter(ArchiMateElement.id.in_(made)).delete(synchronize_session=False)
        db.session.commit()


def _open(browser, live_server, seeded, persona, size=(1440, 900), history=None):
    """A signed-in page on Impact Analysis, ready. history: None = real, or a JSON body to answer with."""
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    _login(page, live_server, seeded["emails"][persona])
    if history is not None:
        page.route("**/strategic/api/impact-analysis/history",
                   lambda route: route.fulfill(status=200, content_type="application/json", body=history))
    page.goto(live_server + PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_function(READY, timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)     # Lucide replaces the <i> elements once Alpine has settled
    return ctx, page


def _classes(page, text):
    return page.evaluate(
        """(t) => { const b = [...document.querySelectorAll('button')]
              .find(x => x.textContent.trim().startsWith(t)); return b.className.split(/\\s+/); }""", text)


def _analyze(page):
    return page.locator("button", has_text="Analyze Impact")


def _choose(page, text, name):
    page.fill(SEARCH, text)
    row = page.locator(ROWS).filter(has_text=name)
    expect(row).to_have_count(1)
    row.click()
    expect(_analyze(page)).to_be_enabled()


def _status_text(page):
    return " ".join(page.locator(STATUS).inner_text().split())


SEARCH_URL = "**/archimate/api/elements/search*"
ANALYSIS_URL = "**/strategic/api/impact-analysis"
LIMIT = 50
CAP_NOTE = "Showing the first %d matches. Type more of the name to narrow the list." % LIMIT


def _search_params(url):
    return parse_qs(urlparse(url).query)


def _record_searches(page):
    """Every element-search request the page sends, as its query parameters."""
    sent = []
    page.on("request", lambda req: sent.append(_search_params(req.url)) if "/archimate/api/elements/search" in req.url else None)
    return sent


def _hold_searches(page, wanted):
    """Hold each search request whose parameters satisfy wanted(); every other one goes through."""
    held = []

    def handler(route):
        if wanted(_search_params(route.request.url)):
            held.append(route)
        else:
            route.continue_()

    page.route(SEARCH_URL, handler)
    return held


def _wait_for(page, condition, what, seconds=30):
    for _ in range(seconds * 10):
        if condition():
            return
        page.wait_for_timeout(100)
    raise AssertionError("timed out waiting for " + what)


def _glyphs(page):
    """The visible glyphs on the Analyze button, named: "spinner" or "play" (or the icon's own class)."""
    return ["spinner" if "animate-spin" in cls else ("play" if "lucide-play" in cls else cls)
            for cls in page.evaluate(ANALYZE_SVGS)]


def _release_late(page, route, status=None):
    """Let a held search answer arrive now, after the page has moved on, and give the page time to act on it."""
    if status is None:
        route.continue_()
    else:
        route.fulfill(status=status, content_type="application/json", body='{"error": "unavailable"}')
    page.wait_for_timeout(2000)


def _problems(page):
    """Page errors and Alpine expression errors, collected as they happen."""
    seen = []
    page.on("pageerror", lambda err: seen.append("pageerror: %s" % err))
    page.on("console", lambda msg: seen.append("console: %s" % msg.text)
            if msg.type in ("error", "warning") and ("Expression Error" in msg.text or "TypeError" in msg.text) else None)
    return seen


@pytest.mark.parametrize("persona", PERSONAS)
def test_typing_a_name_with_no_layer_chosen_lists_the_elements_and_runs_the_analysis(
    browser, live_server, seeded, picker_data, persona
):
    ctx, page = _open(browser, live_server, seeded, persona)
    try:
        select = page.locator(LAYER)
        assert select.input_value() == ""
        assert select.locator("option:checked").inner_text().strip() == "All layers"

        page.fill(SEARCH, "Smoke impact")
        for name in (picker_data["source_name"], picker_data["target_name"]):
            row = page.locator(ROWS).filter(has_text=name)
            expect(row).to_have_count(1)
            text = row.inner_text()
            assert "Application Layer" in text, text
            assert "Application Component" in text, text

        expect(_analyze(page)).to_be_disabled()
        page.locator(ROWS).filter(has_text=picker_data["source_name"]).click()
        expect(_analyze(page)).to_be_enabled()
        tokens = _classes(page, "Analyze Impact")
        assert "bg-primary" in tokens and "bg-secondary" not in tokens

        _analyze(page).click()
        expect(page.get_by_role("heading", name="Direct Architectural Dependencies")).to_be_visible()
        expect(page.get_by_text("Impact of MODIFY on " + picker_data["source_name"])).to_be_visible()
    finally:
        ctx.close()


@pytest.mark.parametrize("persona", PERSONAS)
def test_choosing_a_layer_narrows_the_list_and_all_layers_restores_it(
    browser, live_server, seeded, picker_data, persona
):
    ctx, page = _open(browser, live_server, seeded, persona)
    try:
        page.fill(SEARCH, picker_data["tag"])
        rows = page.locator(ROWS)
        expect(rows).to_have_count(5)
        assert any("Application Layer" in t for t in rows.all_inner_texts())

        page.select_option(LAYER, "Business")
        expect(rows).to_have_count(3)
        assert all("Business Layer" in t for t in rows.all_inner_texts())
        assert page.locator(SEARCH).input_value() == picker_data["tag"], "the typed text must be kept"

        page.select_option(LAYER, "")
        expect(rows).to_have_count(5)
        assert any("Application Layer" in t for t in rows.all_inner_texts())
    finally:
        ctx.close()


@pytest.mark.parametrize("persona", PERSONAS)
def test_no_match_in_the_chosen_layer_names_the_layer_that_has_one_and_the_count_switches(
    browser, live_server, seeded, picker_data, persona
):
    ctx, page = _open(browser, live_server, seeded, persona)
    try:
        page.select_option(LAYER, "Application")
        page.fill(SEARCH, "Smoke radar")
        expect(page.locator(STATUS)).to_be_visible()
        assert _status_text(page) == (
            "No elements match 'Smoke radar' in the Application Layer. 1 match in the Technology Layer"
        )

        page.locator(STATUS).get_by_role("button", name="1 match in the Technology Layer").click()
        expect(page.locator(LAYER)).to_have_value("Technology")
        expect(page.locator(ROWS).filter(has_text=picker_data["radar_name"])).to_have_count(1)
        expect(page.locator(STATUS)).to_be_hidden()
    finally:
        ctx.close()


@pytest.mark.parametrize("persona", PERSONAS)
def test_no_match_anywhere_says_so_without_layer_counts(browser, live_server, seeded, picker_data, persona):
    ctx, page = _open(browser, live_server, seeded, persona)
    try:
        page.fill(SEARCH, "qqqq")
        expect(page.locator(STATUS)).to_be_visible()
        assert _status_text(page) == "No elements match 'qqqq' in any layer."
        assert page.locator(STATUS).get_by_role("button").count() == 0
        # A layer chosen with nothing anywhere reads the same: the words were looked for everywhere.
        page.select_option(LAYER, "Application")
        expect(page.locator(STATUS)).to_be_visible()
        assert _status_text(page) == "No elements match 'qqqq' in any layer."
    finally:
        ctx.close()


@pytest.mark.parametrize("persona", PERSONAS)
def test_elements_stored_under_either_type_spelling_are_both_listed(
    browser, live_server, seeded, picker_data, persona
):
    ctx, page = _open(browser, live_server, seeded, persona)
    try:
        page.fill(SEARCH, "%s spelled" % picker_data["tag"])
        rows = page.locator(ROWS)
        expect(rows).to_have_count(2)
        texts = rows.all_inner_texts()
        assert any(picker_data["snake_name"] in t for t in texts), texts
        assert any(picker_data["camel_name"] in t for t in texts), texts
        assert all("Application Component" in t for t in texts), texts
    finally:
        ctx.close()


def test_the_report_counts_dependencies_of_either_type_spelling(browser, live_server, seeded, picker_data):
    ctx, page = _open(browser, live_server, seeded, "solution_architect")
    try:
        _choose(page, picker_data["snake_name"], picker_data["snake_name"])
        _analyze(page).click()
        expect(page.get_by_role("heading", name="Direct Architectural Dependencies")).to_be_visible()
        capabilities = page.evaluate(
            """() => { const s = [...document.querySelectorAll('span.uppercase')].find(x => x.textContent.trim() === 'Capabilities');
                       return s.previousElementSibling.textContent.trim(); }""")
        assert capabilities == "2", "business_capability and BusinessCapability are both capabilities"
    finally:
        ctx.close()


def test_the_analyze_button_shows_one_icon_and_the_page_action_leads(browser, live_server, seeded, picker_data):
    ctx, page = _open(browser, live_server, seeded, "solution_architect")
    try:
        # Before an analysis exists the two header actions are secondary.
        for label in ("Compare Scenarios", "Create Change Request"):
            tokens = _classes(page, label)
            assert "bg-secondary" in tokens and "bg-primary" not in tokens, (label, tokens)

        # At rest: exactly one visible glyph, and it is not the spinner.
        assert _glyphs(page) == ["play"], page.evaluate(ANALYZE_SVGS)

        _choose(page, picker_data["snake_name"], picker_data["snake_name"])
        assert "bg-primary" in _classes(page, "Analyze Impact")

        held = []
        page.route("**/strategic/api/impact-analysis",
                   lambda route: held.append(route) if route.request.method == "POST" else route.continue_())
        _analyze(page).click()
        for _ in range(100):
            if held:
                break
            page.wait_for_timeout(100)
        assert held, "the analysis request was never sent"
        # The button follows the request by a repaint; wait for it to settle rather than sampling once.
        _wait_for(page, lambda: _glyphs(page) == ["spinner"], "the running button to show only the spinner")
        assert _glyphs(page) == ["spinner"], "while running: exactly the spinner, got %r" % page.evaluate(ANALYZE_SVGS)

        held[0].continue_()
        expect(page.get_by_role("heading", name="Direct Architectural Dependencies")).to_be_visible()
        _wait_for(page, lambda: _glyphs(page) == ["play"], "the finished button to show only the first glyph")
        assert _glyphs(page) == ["play"], "afterwards: the first glyph again, got %r" % page.evaluate(ANALYZE_SVGS)

        # With a report to act on, the header actions take the primary style.
        for label in ("Compare Scenarios", "Create Change Request"):
            assert "bg-primary" in _classes(page, label), label
    finally:
        ctx.close()


def test_the_recommendations_card_keeps_the_same_gap_as_the_other_blocks(
    browser, live_server, seeded, picker_data
):
    # No history: the card follows the Select Element / Analysis Report row.
    ctx, page = _open(browser, live_server, seeded, "solution_architect", history="[]")
    try:
        geometry = page.evaluate(GEOMETRY)
        assert geometry["historyShown"] is False
        assert geometry["gridToRecs"] >= 24, geometry
    finally:
        ctx.close()

    # With history: the same gap separates Recent Analyses from the card.
    ctx, page = _open(browser, live_server, seeded, "solution_architect")
    try:
        _choose(page, picker_data["snake_name"], picker_data["snake_name"])
        _analyze(page).click()
        expect(page.get_by_role("heading", name="Direct Architectural Dependencies")).to_be_visible()
        expect(page.locator("button", has_text="Recent Analyses")).to_be_visible()
        geometry = page.evaluate(GEOMETRY)
        assert geometry["historyShown"] is True
        assert geometry["historyToRecs"] >= 24, geometry
        assert geometry["gridToRecs"] >= 24, geometry
    finally:
        ctx.close()


def test_a_text_matching_more_than_the_limit_lists_the_first_ones_by_name_and_says_so(
    browser, live_server, seeded, picker_data, bulk_data
):
    ctx, page = _open(browser, live_server, seeded, "solution_architect")
    try:
        sent = _record_searches(page)
        prefix = bulk_data["cap_prefix"]
        rows = page.locator(ROWS)

        page.fill(SEARCH, prefix)
        expect(rows).to_have_count(LIMIT)
        expect(page.get_by_text(CAP_NOTE)).to_be_visible()
        texts = rows.all_inner_texts()
        assert texts[0].startswith("%s 00" % prefix) and texts[-1].startswith("%s %02d" % (prefix, LIMIT - 1)), (
            "the first %d by name, in order" % LIMIT)
        assert sent[-1]["limit"] == [str(LIMIT)] and "layer" not in sent[-1], sent[-1]

        # Fewer matches than the limit: every one of them, and no note.
        page.fill(SEARCH, prefix + " 0")
        expect(rows).to_have_count(10)
        expect(page.get_by_text(CAP_NOTE)).to_be_hidden()
        page.fill(SEARCH, prefix + " 5")
        expect(rows).to_have_count(5)
        assert rows.first.inner_text().startswith("%s 50" % prefix)
        expect(page.get_by_text(CAP_NOTE)).to_be_hidden()
    finally:
        ctx.close()


def test_counts_from_a_full_list_read_at_least_and_carry_no_plus(browser, live_server, seeded, picker_data, bulk_data):
    ctx, page = _open(browser, live_server, seeded, "solution_architect")
    try:
        prefix = bulk_data["cap_prefix"]
        page.select_option(LAYER, "Application")
        page.fill(SEARCH, prefix)
        expect(page.locator(STATUS)).to_be_visible()
        message = _status_text(page)
        assert message == "No elements match '%s' in the Application Layer. At least %d matches in the Business Layer" % (
            prefix, LIMIT), message
        assert "+" not in message

        page.locator(STATUS).get_by_role("button", name="At least %d matches in the Business Layer" % LIMIT).click()
        expect(page.locator(LAYER)).to_have_value("Business")
        expect(page.locator(ROWS)).to_have_count(LIMIT)
        expect(page.get_by_text(CAP_NOTE)).to_be_visible()
    finally:
        ctx.close()


def test_percent_underscore_and_backslash_in_the_typed_text_match_literally(
    browser, live_server, seeded, picker_data, bulk_data
):
    ctx, page = _open(browser, live_server, seeded, "solution_architect")
    try:
        sent = _record_searches(page)
        names = bulk_data["names"]
        tag = bulk_data["tag"]
        backslash = chr(92)
        rows = page.locator(ROWS)

        def only_this_row(typed, name):
            # The rows of the previous text stay on screen until the answer for this one arrives, so
            # wait for the named row before counting.
            page.fill(SEARCH, typed)
            expect(rows.filter(has_text=name)).to_have_count(1)
            expect(rows).to_have_count(1)

        only_this_row(names["underscore"], names["underscore"])
        assert sent[-1]["q"] == ["W%s%s_Billing" % (tag, backslash)], sent[-1]

        only_this_row("Rate %s 50%%" % tag, names["percent"])
        assert sent[-1]["q"] == ["Rate %s 50%s%%" % (tag, backslash)], sent[-1]

        only_this_row(names["backslash"], names["backslash"])
        assert sent[-1]["q"] == ["Path %s A%s%sB" % (tag, backslash, backslash)], sent[-1]

        # Two underscores, unescaped, would match every name of two or more characters.
        page.fill(SEARCH, "__")
        expect(page.locator(STATUS)).to_be_visible()
        assert _status_text(page) == "No elements match '__' in any layer."
        expect(rows).to_have_count(0)
    finally:
        ctx.close()


def test_a_late_answer_to_an_older_search_never_replaces_the_newer_rows(
    browser, live_server, seeded, picker_data
):
    ctx, page = _open(browser, live_server, seeded, "solution_architect")
    try:
        rows = page.locator(ROWS)
        held = _hold_searches(page, lambda p: p.get("q") == ["Smoke impact source"])
        page.fill(SEARCH, "Smoke impact source")
        _wait_for(page, lambda: len(held) == 1, "the first search to be held")

        page.fill(SEARCH, "Smoke impact target")
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text(picker_data["target_name"])

        _release_late(page, held[0])
        assert rows.count() == 1
        assert picker_data["target_name"] in rows.first.inner_text()
        assert picker_data["source_name"] not in " ".join(rows.all_inner_texts())
    finally:
        ctx.close()


def test_a_late_failure_of_an_older_search_shows_no_error_over_the_newer_rows(
    browser, live_server, seeded, picker_data
):
    ctx, page = _open(browser, live_server, seeded, "solution_architect")
    try:
        rows = page.locator(ROWS)
        held = _hold_searches(page, lambda p: p.get("q") == ["Smoke impact source"])
        page.fill(SEARCH, "Smoke impact source")
        _wait_for(page, lambda: len(held) == 1, "the first search to be held")

        page.fill(SEARCH, "Smoke impact target")
        expect(rows).to_have_count(1)

        _release_late(page, held[0], status=500)
        assert rows.count() == 1, "the newer rows must stay"
        expect(page.locator('p[role="alert"]')).to_be_hidden()
    finally:
        ctx.close()


def test_choosing_a_row_while_a_search_is_still_running_keeps_the_list_closed(
    browser, live_server, seeded, picker_data
):
    ctx, page = _open(browser, live_server, seeded, "solution_architect")
    try:
        rows = page.locator(ROWS)
        page.fill(SEARCH, "Smoke impact")
        expect(rows.filter(has_text=picker_data["target_name"])).to_have_count(1)

        held = _hold_searches(page, lambda p: p.get("q") == ["Smoke impact source"])
        page.fill(SEARCH, "Smoke impact source")
        _wait_for(page, lambda: len(held) == 1, "the newer search to be held")

        # The earlier list is still on screen; the person chooses from it.
        rows.filter(has_text=picker_data["target_name"]).click()
        expect(_analyze(page)).to_be_enabled()
        expect(rows).to_have_count(0)

        _release_late(page, held[0])
        assert rows.count() == 0, "an answer that arrives after a choice must not reopen the list"
        assert page.locator(SEARCH).input_value() == picker_data["target_name"]
        expect(_analyze(page)).to_be_enabled()
    finally:
        ctx.close()


def test_a_late_count_of_other_layers_never_shows_beside_newer_rows(browser, live_server, seeded, picker_data):
    ctx, page = _open(browser, live_server, seeded, "solution_architect")
    try:
        rows = page.locator(ROWS)
        page.select_option(LAYER, "Application")
        # The count of other layers is the request that carries no layer: hold that one.
        held = _hold_searches(page, lambda p: p.get("q") == ["Smoke radar"] and "layer" not in p)
        page.fill(SEARCH, "Smoke radar")
        _wait_for(page, lambda: len(held) == 1, "the count of other layers to be held")

        page.fill(SEARCH, "Smoke impact source")
        expect(rows).to_have_count(1)

        _release_late(page, held[0])
        assert rows.count() == 1
        assert page.locator(STATUS).count() == 0, "a message about an older search must not appear"
    finally:
        ctx.close()


def test_changing_the_layer_to_one_that_excludes_the_chosen_element_clears_the_choice(
    browser, live_server, seeded, picker_data
):
    ctx, page = _open(browser, live_server, seeded, "solution_architect")
    try:
        problems = _problems(page)
        name = picker_data["source_name"]

        # Chosen, then a layer that includes it or none at all: the choice stays.
        _choose(page, name, name)
        page.select_option(LAYER, "Application")
        page.wait_for_timeout(1000)
        expect(_analyze(page)).to_be_enabled()
        page.select_option(LAYER, "")
        page.wait_for_timeout(1000)
        expect(_analyze(page)).to_be_enabled()

        # A layer that excludes it: the choice goes, and the page says nothing is selected.
        page.select_option(LAYER, "Business")
        expect(_analyze(page)).to_be_disabled()
        expect(page.get_by_text("Select an element to begin analysis")).to_be_visible()

        # The same after a report exists: the report about the dropped element goes with it.
        page.select_option(LAYER, "")
        _choose(page, name, name)
        _analyze(page).click()
        expect(page.get_by_role("heading", name="Direct Architectural Dependencies")).to_be_visible()
        page.select_option(LAYER, "Business")
        expect(_analyze(page)).to_be_disabled()
        expect(page.get_by_role("heading", name="Direct Architectural Dependencies")).to_be_hidden()
        expect(page.get_by_text("No Analysis Active")).to_be_visible()
        expect(page.get_by_text("Select an element to begin analysis")).to_be_visible()
        for label in ("Compare Scenarios", "Create Change Request"):
            assert "bg-secondary" in _classes(page, label), label
        assert not problems, problems
    finally:
        ctx.close()


def test_the_analysis_request_carries_the_fields_it_always_carried(browser, live_server, seeded, picker_data):
    ctx, page = _open(browser, live_server, seeded, "solution_architect")
    try:
        posted = []
        page.on("request", lambda req: posted.append(req.post_data_json)
                if req.method == "POST" and req.url.endswith("/strategic/api/impact-analysis") else None)
        name = picker_data["source_name"]
        _choose(page, name, name)

        _analyze(page).click()
        _wait_for(page, lambda: len(posted) == 1, "the first analysis request")
        expect(page.get_by_role("heading", name="Direct Architectural Dependencies")).to_be_visible()
        assert set(posted[0]) == {"element_id", "element_type", "change_type"}, posted[0]
        assert posted[0]["element_type"] == "" and posted[0]["change_type"] == "MODIFY", posted[0]

        # The layer control's own value is what is sent.
        page.select_option(LAYER, "Application")
        expect(_analyze(page)).to_be_enabled()
        _analyze(page).click()
        _wait_for(page, lambda: len(posted) == 2, "the second analysis request")
        assert posted[1]["element_type"] == "Application" and posted[1]["element_id"] == posted[0]["element_id"]
    finally:
        ctx.close()
