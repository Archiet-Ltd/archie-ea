"""An architect opens pages a live walk found unreadable, unfocused, noisy or at the wrong address.

In the demo organisation (linked applications, capabilities, vendors, gaps, people and connectors) the architect:

* opens the layered composer and reads shapes at a usable size, with a quality figure that falls
  because of the warnings the toolbar counts beside it;
* tabs through the impact analysis page (headers of its tables), the gaps page, the team page and the
  connectors page and sees a visible focus ring on every control named in the walk, at 1440 and 390 wide;
* opens the capability tree, presses "All" and sees the tree with no policy error;
* opens the industry process frameworks and reads no "None";
* follows the old vendor addresses (data, selection, integration, technical, import) and each lands on
  the page its name promises, never on the vendor list under its own address.

Screenshots go to SMOKE_SCREENSHOT_DIR when it is set.
"""

import os
import pathlib
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import add_administrator, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
VIEWPORTS = (("light", 1440, 900), ("dark", 390, 844))


@pytest.fixture(scope="module")
def demo(live_server, app):
    """Seed the demo company once; return the ids the journeys need."""
    from app import db
    from app.commands.seed_demo_company import seed_demo_company

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            seed_demo_company()
            from app.models.organization import Organization

            org = Organization.query.filter_by(slug="lantern-quay").one()
            found = {"org_id": org.id}
            db.session.remove()
            # Team and Connectors are an administrator's pages: one more person, the demo organisation's admin.
            suffix = uuid.uuid4().hex[:8]
            add_administrator(org.id, suffix)
            found["admin_email"] = "launch.admin.%s@example.com" % suffix
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    return found


def _context(browser, scheme, width, height):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context


def _shot(page, name):
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR")
    if not folder:
        return
    pathlib.Path(folder).mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(pathlib.Path(folder) / ("%s-%d.png" % (name, page.viewport_size["width"]))))


def _goto(page, base, path):
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)


# --- the composer ---------------------------------------------------------------------------------

COMPOSER_STATE = """() => {
    const root = document.querySelector('[x-data="composerApp()"]');
    const d = Alpine.$data(root);
    return {zoom: d.zoomPercent, elements: d.elementCount, warnings: d.validationWarnings,
            score: d.complianceScore, loading: d.viewpointLoading};
}"""


def test_composer_opens_readable_and_quality_agrees_with_its_warnings(browser, live_server, demo):
    context = _context(browser, "light", 1440, 900)
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        page.goto(live_server + "/archimate/composer?viewpoint=layered", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        page.wait_for_function(
            "() => { const r = document.querySelector('[x-data=\"composerApp()\"]');"
            " if (!r || !window.Alpine) return false; const d = Alpine.$data(r);"
            " return d.elementCount > 1 && !d.viewpointLoading; }", polling=500, timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(2500)  # the quick check and the quality figure settle after the load
        state = page.evaluate(COMPOSER_STATE)
        assert state["zoom"] >= 50, state
        assert state["warnings"] > 0, "the demo layered view should carry warnings: %s" % state

        shown = page.get_by_role("button", name="Diagram quality score, open details")
        expect(shown).to_be_visible()
        quality = int(re.search(r"Quality (\d+)%", shown.inner_text()).group(1))
        warnings_text = page.locator("text=/\\d+ warnings?/").first.inner_text()
        counted = int(re.search(r"(\d+) warning", warnings_text).group(1))
        assert counted == state["warnings"]
        assert quality < 100, "Quality %d%% beside %d warnings" % (quality, counted)

        # A shape on the canvas is large enough to read.
        widths = page.eval_on_selector_all(
            ".joint-element", "els => els.slice(0, 20).map(e => e.getBoundingClientRect().width)")
        assert widths and max(widths) >= 80, widths
        _shot(page, "composer")
    finally:
        context.close()


# --- composer toolbar and saved views at a phone width ---------------------------------------------

def test_saved_layered_view_opens_readable_and_the_phone_toolbar_shows_quality_and_warnings(
        browser, live_server, demo):
    name = "Layered view %s" % uuid.uuid4().hex[:6]
    context = _context(browser, "dark", 390, 844)
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        page.goto(live_server + "/archimate/composer?viewpoint=layered", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        page.wait_for_function(
            "() => { const r = document.querySelector('[x-data=\"composerApp()\"]');"
            " if (!r || !window.Alpine) return false; const d = Alpine.$data(r);"
            " return d.elementCount > 1 && !d.viewpointLoading; }", polling=500, timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(2500)
        # Quality and warnings are on the toolbar at 390, and the toolbar does not scroll sideways.
        quality = page.get_by_role("button", name="Diagram quality score, open details")
        expect(quality).to_be_visible()
        warnings = page.get_by_test_id("composer-view-actions").get_by_text(re.compile(r"^\d+ warnings?$"))
        expect(warnings).to_be_visible(timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1500)
        opened = page.evaluate(COMPOSER_STATE)
        assert opened["warnings"] > 0, opened
        assert quality.inner_text().strip() == "Quality %s%%" % opened["score"], (quality.inner_text(), opened)
        assert warnings.inner_text().split()[0] == str(opened["warnings"]), (warnings.inner_text(), opened)
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth"), "sideways scroll"
        _shot(page, "composer-toolbar")

        # Save the view, then open it again from the library: it opens as readable as the viewpoint did.
        page.locator("#composer-canvas").click(position={"x": 5, "y": 5})
        page.keyboard.press("Control+s")
        name_box = page.locator('[aria-label="Viewpoint name"]')
        expect(name_box).to_be_visible(timeout=PAGE_TIMEOUT)
        name_box.fill(name)
        with page.expect_response(
                lambda r: "/archimate/api/saved-viewpoints" in r.url and r.request.method in ("POST", "PUT"),
                timeout=PAGE_TIMEOUT) as saved:
            page.get_by_role("button", name=re.compile("^Save$")).click()
        assert saved.value.status < 400, saved.value.text()[:300]
        expect(name_box).to_be_hidden(timeout=PAGE_TIMEOUT)

        page.goto(live_server + "/archimate/diagrams", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.locator("tbody tr", has_text=name)
        expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
        row.get_by_role("link", name="Open").click()
        page.wait_for_url(re.compile(r"/archimate/composer\?.*viewpoint_id=\d+"), timeout=PAGE_TIMEOUT)
        # A saved view of 500 shapes keeps the page busy while it draws: read the state until it settles.
        reopened = None
        for _ in range(60):
            reopened = page.evaluate(COMPOSER_STATE)
            if reopened["elements"] > 1 and not reopened["loading"]:
                break
            page.wait_for_timeout(2000)
        page.wait_for_timeout(2500)
        reopened = page.evaluate(COMPOSER_STATE)
        assert reopened["elements"] == opened["elements"], (opened, reopened)
        assert reopened["zoom"] >= 50, "the saved view opened at %s%%: %s" % (reopened["zoom"], reopened)
        _shot(page, "composer-saved-view")
    finally:
        context.close()


def _wait_composer_settled(page):
    """Read the composer until its shapes are drawn, the load has finished and the figures stop changing.

    A drawing that never gets a warnings count stays at zero, so the wait ends at its limit and the
    caller's comparison reports the difference."""
    previous = None
    state = None
    for _ in range(40):
        state = page.evaluate(COMPOSER_STATE)
        figures = (state["elements"], state["warnings"], state["score"])
        if state["elements"] > 1 and not state["loading"] and state["warnings"] > 0 and figures == previous:
            break
        previous = figures
        page.wait_for_timeout(3000)
    return page.evaluate(COMPOSER_STATE)


def _toolbar_figures(page):
    """The quality figure and the warnings count a person reads on the toolbar."""
    quality = page.get_by_role("button", name="Diagram quality score, open details")
    expect(quality).to_be_visible(timeout=PAGE_TIMEOUT)
    warnings = page.get_by_test_id("composer-view-actions").get_by_text(re.compile(r"^\d+ warnings?$"))
    expect(warnings).to_be_visible(timeout=PAGE_TIMEOUT)
    return (int(re.search(r"Quality (\d+)%", quality.inner_text()).group(1)),
            int(warnings.inner_text().split()[0]))


def test_one_drawing_shows_the_same_quality_and_warnings_as_viewpoint_saved_view_and_snapshot(
        browser, live_server, demo):
    name = "Same figures %s" % uuid.uuid4().hex[:6]
    context = _context(browser, "dark", 390, 844)
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        page.goto(live_server + "/archimate/composer?viewpoint=layered", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        as_viewpoint = _wait_composer_settled(page)
        assert as_viewpoint["warnings"] > 0, as_viewpoint
        viewpoint_figures = _toolbar_figures(page)
        _shot(page, "same-drawing-viewpoint")

        page.locator("#composer-canvas").click(position={"x": 5, "y": 5})
        page.keyboard.press("Control+s")
        name_box = page.locator('[aria-label="Viewpoint name"]')
        expect(name_box).to_be_visible(timeout=PAGE_TIMEOUT)
        name_box.fill(name)
        with page.expect_response(
                lambda r: "/archimate/api/saved-viewpoints" in r.url and r.request.method in ("POST", "PUT"),
                timeout=PAGE_TIMEOUT) as saved:
            page.get_by_role("button", name=re.compile("^Save$")).click()
        assert saved.value.status < 400, saved.value.text()[:300]
        expect(name_box).to_be_hidden(timeout=PAGE_TIMEOUT)

        # A snapshot of the saved view, made from the Save menu a person uses.
        page.get_by_test_id("menu-save").click()
        page.get_by_text("Save a snapshot to compare against later", exact=True).click()
        prompt = page.get_by_role("dialog").get_by_role("textbox")
        expect(prompt).to_be_visible(timeout=PAGE_TIMEOUT)
        prompt.fill("first")
        with page.expect_response(lambda r: r.url.endswith("/snapshots") and r.request.method == "POST",
                                  timeout=PAGE_TIMEOUT) as made:
            page.get_by_role("dialog").get_by_role("button", name="OK", exact=True).click()
        assert made.value.status < 400, made.value.text()[:300]

        page.goto(live_server + "/archimate/diagrams", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.locator("tbody tr", has_text=name)
        expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
        row.get_by_role("link", name="Open").click()
        page.wait_for_url(re.compile(r"/archimate/composer\?.*viewpoint_id=\d+"), timeout=PAGE_TIMEOUT)
        as_saved = _wait_composer_settled(page)
        assert as_saved["elements"] == as_viewpoint["elements"], (as_viewpoint, as_saved)
        saved_figures = _toolbar_figures(page)
        _shot(page, "same-drawing-saved-view")
        assert saved_figures == viewpoint_figures, ("saved view", saved_figures, "viewpoint", viewpoint_figures)

        # Open the snapshot from the version history the Save menu offers.
        page.get_by_test_id("menu-save").click()
        page.get_by_text("Open a snapshot", exact=True).click()
        history = page.get_by_role("dialog", name="Version history snapshots")
        history.get_by_role("button", name=re.compile(r"^first")).click()
        # Redrawing 500 shapes keeps the page busy: read the status line until the snapshot is shown.
        status = ""
        for _ in range(60):
            status = page.evaluate(
                "() => Alpine.$data(document.querySelector('[x-data=\"composerApp()\"]')).statusText")
            if status.startswith("Viewing snapshot: first"):
                break
            page.wait_for_timeout(3000)
        assert status.startswith("Viewing snapshot: first"), status
        as_snapshot = _wait_composer_settled(page)
        snapshot_figures = _toolbar_figures(page)
        _shot(page, "same-drawing-snapshot")
        assert as_snapshot["elements"] == as_viewpoint["elements"], (as_viewpoint, as_snapshot)
        assert snapshot_figures == viewpoint_figures, ("snapshot", snapshot_figures, "viewpoint", viewpoint_figures)
    finally:
        context.close()


# --- the vendor capability matrix --------------------------------------------------------------------

MATRIX = "/dashboards/vendor-capability-matrix"


def _strong_fits(page):
    return page.locator("p", has_text="Strong Fits").locator("xpath=following-sibling::p").inner_text().strip()


def test_matrix_shows_a_dash_before_a_figure_and_explains_an_empty_category_list(browser, live_server, demo):
    context = _context(browser, "light", 1440, 900)
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        _goto(page, live_server, MATRIX)
        # Nothing is computed until a category is picked: a dash, as the three cards beside it show.
        assert _strong_fits(page) == "—", _strong_fits(page)
        _shot(page, "matrix-before-figure")

        # In the demo organisation no vendor product is recorded, so there is no category to pick: the page
        # says why, and its next step lands on a page with content and an action, never an empty catalogue.
        _goto(page, live_server, MATRIX)
        expect(page.get_by_test_id("no-categories")).to_contain_text("No vendor products are recorded", timeout=PAGE_TIMEOUT)
        assert _strong_fits(page) == "—"
        explanation = page.get_by_test_id("no-categories-next").locator("xpath=..").inner_text()
        assert "start by adding the vendor on the Vendors list" in explanation, explanation
        _shot(page, "matrix-no-categories")
        page.get_by_test_id("no-categories-next").click()
        page.wait_for_url(re.compile(r"/applications/vendors$"), timeout=PAGE_TIMEOUT)
        assert page.locator("main h1").first.inner_text().strip() == "Vendors"
        assert page.get_by_test_id("vendor-row").count() > 0
        expect(page.get_by_test_id("add-vendor")).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "catalogue has no reviewed products" not in page.locator("main").inner_text()
        _shot(page, "matrix-next-step-lands")
    finally:
        context.close()


def test_matrix_next_step_in_another_organisation_lands_on_its_own_empty_vendors_list(
        browser, live_server, demo, app):
    """A second, empty organisation reads its own list: none of the demo organisation's vendors, and a way on."""
    vendor_name = _demo_vendor_name(app, demo["org_id"])
    assert vendor_name
    other = create_fresh_org("enterprise_architect")
    context = _context(browser, "dark", 390, 844)
    page = context.new_page()
    try:
        sign_in(page, live_server, other["emails"]["enterprise_architect"])
        _goto(page, live_server, MATRIX)
        expect(page.get_by_test_id("no-categories")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.get_by_test_id("no-categories-next").click()
        page.wait_for_url(re.compile(r"/applications/vendors$"), timeout=PAGE_TIMEOUT)
        text = page.locator("main").inner_text()
        assert page.get_by_test_id("vendor-row").count() == 0
        assert vendor_name not in text
        assert len(text.strip()) > 40, text
    finally:
        context.close()


# --- focus rings ----------------------------------------------------------------------------------

FOCUS_PROBE = """() => {
    const e = document.activeElement;
    if (!e || e === document.body) return null;
    // Read the settled style a person sees, not a frame of the ring's fade-in.
    const settle = () => e.getAnimations().forEach(a => { try { a.finish(); } catch (x) { /* infinite */ } });
    const look = () => { settle(); const c = getComputedStyle(e);
        return [c.outlineStyle, c.outlineWidth, c.outlineColor, c.boxShadow].join('|'); };
    const opaque = col => { const m = col.match(/[\d.]+/g); return !!m && !/^rgba?\(0, 0, 0, 0\)$/.test(col)
        && (m.length < 4 || parseFloat(m[3]) >= 0.5); };
    const ringOf = () => { settle(); const c = getComputedStyle(e);
        const outline = c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) >= 1 && opaque(c.outlineColor);
        const shadow = (c.boxShadow || '').split(/,(?![^(]*\))/).some(part =>
            /0px 0px 0px [1-9]/.test(part.trim().replace(/^\S+\([^)]*\)\s*/, '')) && opaque(part.trim().match(/^(rgba?|oklab|oklch|hsla?|color)\([^)]*\)/) ? part.trim().match(/^\S+\([^)]*\)/)[0] : 'rgb(0,0,0)'));
        return outline || shadow; };
    const focused = look(); const hasRing = ringOf();
    e.blur(); const blurred = look(); e.focus({preventScroll: true});
    const r = e.getBoundingClientRect();
    return {css: focused + " vs " + blurred, tag: e.tagName, label: (e.getAttribute('aria-label') || e.innerText || e.value || '').trim().slice(0, 60),
            ring: !!hasRing && focused !== blurred, inTableHead: !!e.closest('thead'), shown: r.width > 0 && r.height > 0};
}"""


def _tab_stops(page, named, limit=400):
    """Press Tab until every named control has been focused (or ``limit`` presses); return the stops."""
    page.evaluate("() => { if (document.activeElement) document.activeElement.blur(); window.scrollTo(0, 0); }")
    stops, wanted = [], set(named)
    for _ in range(limit):
        page.keyboard.press("Tab")
        stop = page.evaluate(FOCUS_PROBE)
        if stop:
            stops.append(stop)
            wanted.discard(stop["label"])
            if not wanted:
                break
    return stops


def _assert_rings(stops, named, where):
    lacking = [(s["label"] or s["tag"], s["css"]) for s in stops if s["shown"] and not s["ring"]
               and (s["inTableHead"] or s["label"] in named)]
    assert lacking == [], "%s: no visible focus on %s" % (where, lacking)
    reached = {s["label"] for s in stops}
    missing = [n for n in named if n not in reached]
    assert missing == [], "%s: the keyboard never reached %s; it stopped on %s" % (where, missing, [s["label"] for s in stops][:20])


def _save_two_runs(page, base):
    """Pick two elements on the impact page and save each, so its runs table has sortable headers."""
    for name in ("Calibration Ledger", "Customer Portal"):
        _goto(page, base, "/enterprise/analysis/impact-analysis")
        page.locator("#impact-page-picker-input").fill(name)
        option = page.get_by_role("option", name=name, exact=True)
        if option.count() == 0:
            option = page.get_by_role("option").first
        expect(option.first).to_be_visible(timeout=PAGE_TIMEOUT)
        option.first.click()
        page.wait_for_url(lambda url: "element=" in url, timeout=PAGE_TIMEOUT)
        page.get_by_test_id("impact-save-run").click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("impact-run-row").first).to_be_visible(timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("scheme,width,height", VIEWPORTS)
def test_named_controls_show_a_focus_ring_when_tabbed_to(browser, live_server, demo, scheme, width, height):
    context = _context(browser, scheme, width, height)
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        _save_two_runs(page, live_server)

        _goto(page, live_server, "/enterprise/analysis/impact-analysis")
        heads = ["Run", "Element", "Connections", "Recorded by people", "Worked out", "Answer"]
        stops = _tab_stops(page, heads)
        _assert_rings(stops, heads, "impact analysis at %d" % width)
        _shot(page, "focus-impact-analysis")

        # Gaps, Team and Connectors are an administrator's pages; read them at both widths.
        # Leave the page first so its keep-alive request cannot race the second sign-in.
        page.goto("about:blank")
        context.clear_cookies()
        sign_in(page, live_server, demo["admin_email"])
        for path, named in (("/enterprise/implementation/gap-analysis", ["New gap", "Gap", "Status"]),
                            ("/admin/team", ["Send invitation"]),
                            ("/admin/connectors", ["Save connector"])):
            _goto(page, live_server, path)
            _assert_rings(_tab_stops(page, named), named, "%s at %d" % (path, width))
        # The Work package header is plain text, not a control: it has no focus to show, and no control
        # of the gaps table may be skipped on the way to it.
        _goto(page, live_server, "/enterprise/implementation/gap-analysis")
        expect(page.locator("thead").get_by_text("Work package", exact=True).first).to_be_visible()
    finally:
        context.close()


# --- capability tree and industry frameworks ------------------------------------------------------

@pytest.mark.parametrize("scheme,width,height", VIEWPORTS)
def test_capability_tree_all_raises_no_policy_error(browser, live_server, demo, scheme, width, height):
    context = _context(browser, scheme, width, height)
    page = context.new_page()
    problems = []
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(str(e)))
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        _goto(page, live_server, "/capability-map/tree/capability")
        page.get_by_role("button", name="All", exact=True).click()
        page.wait_for_timeout(800)
        assert page.locator("#tree-container").inner_text().strip()
        assert problems == [], problems
        _shot(page, "capability-tree")
    finally:
        context.close()


@pytest.mark.parametrize("scheme,width,height", VIEWPORTS)
def test_industry_frameworks_print_no_none(browser, live_server, demo, scheme, width, height):
    context = _context(browser, scheme, width, height)
    page = context.new_page()
    try:
        sign_in(page, live_server, DEMO_EMAIL)
        _goto(page, live_server, "/industry-apqc/")
        body = page.locator("main").inner_text()
        assert "Industry specific" in body
        assert not re.search(r"\bNone\b", body), body[:600]
        _shot(page, "industry-apqc")
        first = page.locator("main a[href*='/industry-apqc/']").first
        expect(first).to_be_visible()
        first.click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        detail = page.locator("main").inner_text()
        assert detail.strip() and not re.search(r"\bNone\b", detail), detail[:600]
        _shot(page, "industry-apqc-detail")
    finally:
        context.close()


# --- old vendor addresses -------------------------------------------------------------------------

VENDORS = "/applications/vendors"
ANALYSIS = "/dashboard/vendor-analysis"

# address -> (final path, final query, page heading, content this organisation must show)
VENDOR_ADDRESSES = {
    "/vendors/data": (VENDORS, "", "Vendors", "vendors"),
    "/vendors/data/duplicates": (VENDORS, "", "Vendors", "vendors"),
    "/vendors/data/import": (VENDORS, "", "Vendors", "vendors"),
    "/vendors/data/quality": (VENDORS, "", "Vendors", "vendors"),
    "/vendors/data/reconciliation": (VENDORS, "", "Vendors", "vendors"),
    "/vendors/import": (VENDORS, "", "Vendors", "vendors"),
    "/vendors/integration": (VENDORS, "tab=mapping", "Vendors", "mapping"),
    "/vendors/integration/mapping": (VENDORS, "tab=mapping", "Vendors", "mapping"),
    "/vendors/integration/patterns": (VENDORS, "tab=mapping", "Vendors", "mapping"),
    "/vendors/selection": (ANALYSIS, "", "", "analysis"),
    "/vendors/selection/analysis": (ANALYSIS, "", "", "analysis"),
    "/vendors/selection/discovery": ("/applications/vendors/catalogue", "", "", "catalogue"),
    "/vendors/selection/requirements": (ANALYSIS + "/new", "", "", "form"),
    "/vendors/technical": ("/dashboards/vendor-capability-matrix", "", "", "matrix"),
    "/vendors/technical/comparison": (ANALYSIS, "", "", "analysis"),
    "/vendors/technical/scenarios": (ANALYSIS, "", "", "analysis"),
}


GUARDED = {"/vendors/import"}


def _demo_vendor_name(app, org_id):
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    with app.app_context():
        row = db.session.execute(
            db.select(ApplicationComponent.vendor_name)
            .where(ApplicationComponent.organization_id == org_id, ApplicationComponent.vendor_name.isnot(None))
            .limit(1)).first()
        db.session.remove()
    return row[0] if row else None


def test_old_vendor_addresses_land_on_the_page_their_name_promises(browser, live_server, demo, app):
    vendor_name = _demo_vendor_name(app, demo["org_id"])
    assert vendor_name, "the demo organisation should name a vendor on a system"
    context = _context(browser, "light", 1440, 900)
    page = context.new_page()
    try:
        sign_in(page, live_server, demo["admin_email"])
        for address, (want_path, want_query, heading, kind) in VENDOR_ADDRESSES.items():
            first = context.request.get(live_server + address, max_redirects=0)
            if address in GUARDED:
                # Only a platform administrator may import; anyone else reads a refusal, never the list.
                assert first.status in (301, 302, 403), (address, first.status)
                if first.status == 403:
                    _goto(page, live_server, address)
                    assert page.get_by_test_id("add-vendor").count() == 0, address
                    assert "access" in page.locator("main").inner_text().lower(), address
                    continue
            else:
                assert first.status in (301, 302), (address, first.status)
            _goto(page, live_server, address)
            landed = page.url[len(live_server):]
            path, _, query = landed.partition("?")
            assert (path.rstrip("/"), query) == (want_path, want_query), (address, landed)
            headings = [h.strip() for h in page.locator("main h1").all_inner_texts()]
            title = headings[0] if headings else ""
            assert title and title.lower() != "all vendors", (address, title)
            if heading:
                assert title == heading, (address, headings, page.url)
            text = page.locator("main").inner_text()
            assert "All Vendors" not in text and "Duplicate Detection" not in text, (address, title)
            if kind == "vendors":
                assert page.get_by_test_id("vendor-row").count() > 0, address
                assert vendor_name in text, (address, vendor_name)
            elif kind == "mapping":
                assert page.get_by_test_id("mapping-answer").is_visible(), address
                # Under the Vendors heading the person reads what this tab is for and the list it holds.
                assert "Pick or type the vendor for each system" in text, (address, text[:300])
                assert "Systems with no vendor recorded" in page.get_by_test_id("mapping-answer").inner_text(), address
                rows = page.get_by_test_id("mapping-row").count()
                assert rows > 0 or re.search(r"Every one of your [1-9]\d* systems", text), (address, text[:300])
            else:
                assert len(text.strip()) > 80, (address, title)
            _shot(page, "vendor-" + address.strip("/").replace("/", "-"))
    finally:
        context.close()
