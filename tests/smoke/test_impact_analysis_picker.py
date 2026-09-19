"""Impact Analysis, driven as the people who use it: find an element by its name, run the analysis.

Each check below is a defect the design audit measured on this page in a real browser:

    the picker made a layer choice a precondition of a query, and its layer-scoped listing dropped
    every element stored as application_component, so "Smoke impact source" was found by Ctrl+K and
    not by the page whose whole job is to analyse it
    "Compare Scenarios" and "Create Change Request" were the two saturated buttons; "Analyze Impact",
    the action the page exists for, was pale and disabled
    the Analyze button drew a play glyph and a spinner side by side, because Lucide swaps each <i> for
    an <svg> and the x-show placed on the <i> is lost
    the AI recommendations card started 2 px under the cards above it

The two personas are the solution architect and the enterprise architect fixtures.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PAGE = "/strategic/impact-analysis"
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
    tag = "T307%s" % uuid.uuid4().hex[:6]
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
        svgs = page.evaluate(ANALYZE_SVGS)
        assert len(svgs) == 1 and "animate-spin" not in svgs[0], svgs

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
        svgs = page.evaluate(ANALYZE_SVGS)
        assert len(svgs) == 1 and "animate-spin" in svgs[0], "while running: exactly the spinner, got %r" % svgs

        held[0].continue_()
        expect(page.get_by_role("heading", name="Direct Architectural Dependencies")).to_be_visible()
        svgs = page.evaluate(ANALYZE_SVGS)
        assert len(svgs) == 1 and "animate-spin" not in svgs[0], "afterwards: the first glyph again, got %r" % svgs

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
