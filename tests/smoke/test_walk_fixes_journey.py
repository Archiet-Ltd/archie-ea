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
from .fresh_org import add_administrator, sign_in

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
            " return d.elementCount > 1 && !d.viewpointLoading; }", timeout=PAGE_TIMEOUT)
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

        if width <= 500:
            context.clear_cookies()
            sign_in(page, live_server, demo["admin_email"])
            for path, named in (("/enterprise/implementation/gap-analysis", ["New gap"]),
                                ("/admin/team", ["Send invitation"]),
                                ("/admin/connectors", ["Save connector"])):
                _goto(page, live_server, path)
                _assert_rings(_tab_stops(page, named), named, "%s at %d" % (path, width))
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

VENDOR_ADDRESSES = [
    "/vendors/data", "/vendors/data/import", "/vendors/data/quality", "/vendors/data/reconciliation",
    "/vendors/import", "/vendors/integration", "/vendors/integration/patterns",
    "/vendors/selection", "/vendors/selection/analysis", "/vendors/selection/discovery",
    "/vendors/selection/requirements", "/vendors/technical", "/vendors/technical/comparison",
    "/vendors/technical/scenarios",
]


GUARDED = {"/vendors/import"}


def test_old_vendor_addresses_land_on_the_page_their_name_promises(browser, live_server, demo):
    context = _context(browser, "light", 1440, 900)
    page = context.new_page()
    try:
        sign_in(page, live_server, demo["admin_email"])
        for address in VENDOR_ADDRESSES:
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
            landed = page.url[len(live_server):].split("?")[0]
            assert landed.rstrip("/") != address, (address, landed)
            assert landed.rstrip("/") not in ("/vendors", "/applications/vendors", "/dashboard/vendors") \
                or address.endswith("import"), (address, landed)
            text = page.locator("main").inner_text().strip()
            assert text, (address, landed)
            _shot(page, "vendor-" + address.strip("/").replace("/", "-"))
    finally:
        context.close()
