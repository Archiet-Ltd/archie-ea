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


def _persona_with_data(app, enterprise_role):
    """A persona with seeded data for typo tolerance and label tests."""
    from datetime import datetime

    from app import db
    from app.models.user import User
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_capability import UnifiedCapability
    from app.models.models import ArchiMateElement

    with app.app_context():
        org_id = make_org(db, "SidebarSearchData")
        user_id = make_user(db, org_id, "u", enterprise_role, role_name="Architect")
        User.query.filter_by(id=user_id).update({"onboarding_completed_at": datetime.utcnow()})

        # Seed Finance System application
        app_finance = ApplicationComponent(
            name="Finance System",
            component_type="Application",
            organization_id=org_id,
        )
        db.session.add(app_finance)

        # Seed Inventory Manager capability
        cap_inventory = UnifiedCapability(
            name="Inventory Manager",
            organization_id=org_id,
            source_table="unified_capabilities",
            source_id=None,
        )
        db.session.add(cap_inventory)

        # Seed a Contract ArchiMate element
        elem_contract = ArchiMateElement(
            name="Oracle agreement for Finance System",
            type="Contract",
            layer="business",
            organization_id=org_id,
        )
        db.session.add(elem_contract)

        db.session.commit()
        return user_id, org_id


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


def test_no_match_says_so_without_an_ask_control_and_the_hint_stays_visible(app, client, browser):
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
        assert "Nothing matches. Try another word." in results.inner_text()
        assert results.locator('[data-testid="sidebar-search-ask"]').count() == 0
        assert results.locator('a[href*="q=zzzznomatch"]').count() == 0
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


# ---- sidebar02 fixes: placeholder, typo tolerance, result labels -------------------------------------


def test_placeholder_fully_visible_at_1440_and_390(app, client, browser):
    """The placeholder 'Search' fits at both 1440 and 390 wide without truncation."""
    login(client, _persona(app, "solution_architect"))
    page_path = "/dashboard/overview"
    document = client.get(page_path).get_data(as_text=True)

    for width in (1440, 390):
        pg = browser.new_page(viewport={"width": width, "height": 900})
        try:
            _serve_app_in_browser(pg, client, document, page_path)
            pg.goto("http://app.test" + page_path)
            # At 390px, the sidebar is a drawer - open it first
            if width < 768:
                trigger = pg.locator('[data-testid="sidebar-trigger"]')
                trigger.click()
                pg.wait_for_selector('[data-fb-sidebar-mobile]', state="visible")
            input_el = pg.locator("#sidebar-search")
            placeholder = input_el.get_attribute("placeholder")
            assert placeholder == "Search", f"placeholder at {width}px: {placeholder}"
            # Verify the input is visible and has width
            box = input_el.bounding_box()
            assert box is not None, f"input not visible at {width}px"
            assert box["width"] > 0, f"input has no width at {width}px"
        finally:
            pg.close()


def test_typo_tolerance_finanse_sistem_finds_finance_system(app, client, browser):
    """'finanse sistem' (two words, each 1-2 letters off) finds 'Finance System' application."""
    login(client, _persona_with_data(app, "solution_architect")[0])
    page_path = "/dashboard/overview"
    document = client.get(page_path).get_data(as_text=True)

    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _serve_app_in_browser(pg, client, document, page_path)
        pg.goto("http://app.test" + page_path)
        pg.fill("#sidebar-search", "finanse sistem")
        # Wait for results to appear - use first match
        hit = pg.locator('[data-testid="sidebar-search-record"]').first
        hit.wait_for(state="visible", timeout=10000)
        # Verify at least one result contains "Finance System" with "Application" label
        records = pg.locator('[data-testid="sidebar-search-record"]').all()
        found = False
        for r in records:
            text = r.inner_text()
            if "Finance System" in text and "Application" in text:
                found = True
                break
        assert found, f"No 'Finance System' with 'Application' label found in: {[r.inner_text() for r in records]}"
    finally:
        pg.close()


def test_typo_tolerance_inventry_finds_inventory_manager(app, client, browser):
    """'inventry' (1 letter off) finds 'Inventory Manager' capability."""
    login(client, _persona_with_data(app, "solution_architect")[0])
    page_path = "/dashboard/overview"
    document = client.get(page_path).get_data(as_text=True)

    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _serve_app_in_browser(pg, client, document, page_path)
        pg.goto("http://app.test" + page_path)
        pg.fill("#sidebar-search", "inventry")
        hit = pg.locator('[data-testid="sidebar-search-record"]').first
        hit.wait_for(state="visible", timeout=10000)
        records = pg.locator('[data-testid="sidebar-search-record"]').all()
        found = False
        for r in records:
            text = r.inner_text()
            if "Inventory Manager" in text and "Capability" in text:
                found = True
                break
        assert found, f"No 'Inventory Manager' with 'Capability' label found in: {[r.inner_text() for r in records]}"
    finally:
        pg.close()


def test_exact_match_ranks_before_typo_match(app, client, browser):
    """Exact match 'Finance System' ranks before typo match 'finanse sistem'."""
    login(client, _persona_with_data(app, "solution_architect")[0])
    page_path = "/dashboard/overview"
    document = client.get(page_path).get_data(as_text=True)

    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _serve_app_in_browser(pg, client, document, page_path)
        pg.goto("http://app.test" + page_path)
        pg.fill("#sidebar-search", "Finance System")
        # First result should be exact match
        first_hit = pg.locator('[data-testid="sidebar-search-record"]').first
        first_hit.wait_for(state="visible", timeout=10000)
        record_text = first_hit.inner_text()
        assert "Finance System" in record_text
        assert "Application" in record_text
    finally:
        pg.close()


def test_result_labels_show_proper_types(app, client, browser):
    """Record results show proper type labels: Application, Capability, Contract."""
    login(client, _persona_with_data(app, "solution_architect")[0])
    page_path = "/dashboard/overview"
    document = client.get(page_path).get_data(as_text=True)

    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _serve_app_in_browser(pg, client, document, page_path)
        pg.goto("http://app.test" + page_path)

        # Search for each seeded record type separately
        types_found = set()

        # Search for Application
        pg.fill("#sidebar-search", "Finance System")
        pg.wait_for_selector('[data-testid="sidebar-search-record"]', timeout=10000)
        records = pg.locator('[data-testid="sidebar-search-record"]').all()
        for r in records:
            text = r.inner_text()
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            if len(lines) >= 2 and "Finance System" in text:
                types_found.add(lines[-1])
                break

        # Search for Capability
        pg.fill("#sidebar-search", "Inventory Manager")
        pg.wait_for_selector('[data-testid="sidebar-search-record"]', timeout=10000)
        records = pg.locator('[data-testid="sidebar-search-record"]').all()
        for r in records:
            text = r.inner_text()
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            if len(lines) >= 2 and "Inventory Manager" in text:
                types_found.add(lines[-1])
                break

        # Search for Contract
        pg.fill("#sidebar-search", "Oracle agreement")
        pg.wait_for_selector('[data-testid="sidebar-search-record"]', timeout=10000)
        records = pg.locator('[data-testid="sidebar-search-record"]').all()
        for r in records:
            text = r.inner_text()
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            if len(lines) >= 2 and "Oracle agreement" in text:
                types_found.add(lines[-1])
                break

        assert "Application" in types_found, f"Missing 'Application' label, found: {types_found}"
        assert "Capability" in types_found, f"Missing 'Capability' label, found: {types_found}"
        assert "Contract" in types_found, f"Missing 'Contract' label, found: {types_found}"
        # Ensure no generic 'Archimate Element' label appears
        assert "Archimate Element" not in types_found, f"Found generic 'Archimate Element' label: {types_found}"
        assert "ArchiMate Element" not in types_found, f"Found generic 'ArchiMate Element' label: {types_found}"
    finally:
        pg.close()


# ---- large organisation: candidate filter and tenant isolation -------------------------------------


def _persona_with_large_org(app, enterprise_role):
    """Two organisations: org A with 110 applications plus a target that sorts last, org B empty."""
    from datetime import datetime

    from app import db
    from app.models.user import User
    from app.models.application_portfolio import ApplicationComponent

    with app.app_context():
        org_a_id = make_org(db, "SearchLargeOrgA")
        user_a_id = make_user(db, org_a_id, "u", enterprise_role, role_name="Architect")
        User.query.filter_by(id=user_a_id).update({"onboarding_completed_at": datetime.utcnow()})

        # Seed 110 ordinary applications so the candidate set exceeds 100
        for i in range(110):
            db.session.add(ApplicationComponent(
                name=f"Appliance {i:04d}",
                component_type="Application",
                organization_id=org_a_id,
            ))
        # The target sorts alphabetically last so a naive limit without a filter misses it
        db.session.add(ApplicationComponent(
            name="ZZZ Warehouse",
            component_type="Application",
            organization_id=org_a_id,
        ))

        # A second organisation that must never see org A's target
        org_b_id = make_org(db, "SearchLargeOrgB")
        user_b_id = make_user(db, org_b_id, "u", enterprise_role, role_name="Architect")
        User.query.filter_by(id=user_b_id).update({"onboarding_completed_at": datetime.utcnow()})

        db.session.commit()
        return {"org_a_id": org_a_id, "user_a_id": user_a_id,
                "org_b_id": org_b_id, "user_b_id": user_b_id}


def test_large_org_target_found_by_exact_name_and_typo(app, client, browser):
    """In an org with 100+ elements, the target that sorts last is found by exact name and typo."""
    data = _persona_with_large_org(app, "solution_architect")

    # Login as org A user
    from .conftest import login
    login(client, data["user_a_id"])

    page_path = "/dashboard/overview"
    document = client.get(page_path).get_data(as_text=True)

    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _serve_app_in_browser(pg, client, document, page_path)
        pg.goto("http://app.test" + page_path)

        # Exact name search must find the target
        pg.fill("#sidebar-search", "ZZZ Warehouse")
        pg.wait_for_selector('[data-testid="sidebar-search-record"]', timeout=10000)
        records = pg.locator('[data-testid="sidebar-search-record"]').all()
        texts = [r.inner_text() for r in records]
        assert any("ZZZ Warehouse" in t for t in texts), f"Exact match not found in: {texts}"

        # One-letter typo must also find it
        pg.fill("#sidebar-search", "ZZZ Warehouze")
        pg.wait_for_selector('[data-testid="sidebar-search-record"]', timeout=10000)
        records = pg.locator('[data-testid="sidebar-search-record"]').all()
        texts = [r.inner_text() for r in records]
        assert any("ZZZ Warehouse" in t for t in texts), f"Typo match not found in: {texts}"
    finally:
        pg.close()


def test_other_organisation_never_sees_target(app, client, browser):
    """The target from org A is not visible when signed in as org B."""
    data = _persona_with_large_org(app, "solution_architect")

    # Login as org B user
    from .conftest import login
    login(client, data["user_b_id"])

    page_path = "/dashboard/overview"
    document = client.get(page_path).get_data(as_text=True)

    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _serve_app_in_browser(pg, client, document, page_path)
        pg.goto("http://app.test" + page_path)

        pg.fill("#sidebar-search", "ZZZ Warehouse")
        # Wait for the search-results container, not a record
        pg.wait_for_selector('[data-testid="sidebar-search-results"]', timeout=10000)
        pg.wait_for_timeout(500)
        records = pg.locator('[data-testid="sidebar-search-record"]').count()
        assert records == 0, f"Org B should not see ZZ Warehouse, got {records} records"
    finally:
        pg.close()


# ---- fragment-heavy large organisation: shared word parts, exact name guarantee ---------------


def _persona_with_fragment_org(app, enterprise_role):
    """Two organisations: org A with ~500 applications sharing fragments (ion, ent, ser etc.)
    plus a target that sorts alphabetically last, org B empty."""
    from datetime import datetime

    from app import db
    from app.models.user import User
    from app.models.application_portfolio import ApplicationComponent

    with app.app_context():
        org_a_id = make_org(db, "SearchFragmentOrgA")
        user_a_id = make_user(db, org_a_id, "u", enterprise_role, role_name="Architect")
        User.query.filter_by(id=user_a_id).update({"onboarding_completed_at": datetime.utcnow()})

        # Names with shared fragments: ion, ent, ser, cal, ate, tio, men, res, ver, cer
        fragments = ["ion", "ent", "ser", "cal", "ate", "tio", "men", "res", "ver", "cer"]
        for i in range(490):
            a = fragments[i % len(fragments)]
            b = fragments[(i + 1) % len(fragments)]
            c = fragments[(i + 2) % len(fragments)]
            if a in ("ion", "ent"):
                name_parts = f"{a.title()} {c.title()} Manager"
            elif a == "ser":
                name_parts = f"{a.title()}vice {c.title()} System"
            elif a == "cal":
                name_parts = f"{a.title()}ibration {c.title()}"
            elif a == "cer":
                name_parts = f"{a.title()}tificate {c.title()}"
            else:
                name_parts = f"{a.title()}{b.title()} {c.title()} Hub"
            db.session.add(ApplicationComponent(
                name=f"{name_parts} {i:03d}",
                component_type="Application",
                organization_id=org_a_id,
            ))

        # Names specifically matching the brief's examples
        db.session.add(ApplicationComponent(
            name="Inventory Reservation", component_type="Application", organization_id=org_a_id))
        db.session.add(ApplicationComponent(
            name="Calibration certificate", component_type="Application", organization_id=org_a_id))
        db.session.add(ApplicationComponent(
            name="MuleSoft agreement for Integration Bus", component_type="Application",
            organization_id=org_a_id))

        # Target that sorts alphabetically last
        db.session.add(ApplicationComponent(
            name="ZZZebra Inventory Hub",
            component_type="Application",
            organization_id=org_a_id,
        ))

        # A second organisation that must never see org A's target
        org_b_id = make_org(db, "SearchFragmentOrgB")
        user_b_id = make_user(db, org_b_id, "u", enterprise_role, role_name="Architect")
        User.query.filter_by(id=user_b_id).update({"onboarding_completed_at": datetime.utcnow()})

        db.session.commit()
        return {"org_a_id": org_a_id, "user_a_id": user_a_id,
                "org_b_id": org_b_id, "user_b_id": user_b_id}


def test_fragment_org_exact_and_typo_find_last_sorting_target(app, client, browser):
    """~500 elements sharing fragments: exact name and a one-letter typo of the last sort find it."""
    data = _persona_with_fragment_org(app, "solution_architect")

    from .conftest import login
    login(client, data["user_a_id"])

    page_path = "/dashboard/overview"
    document = client.get(page_path).get_data(as_text=True)

    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _serve_app_in_browser(pg, client, document, page_path)
        pg.goto("http://app.test" + page_path)

        # Exact name search must find the target
        pg.fill("#sidebar-search", "ZZZebra Inventory Hub")
        pg.wait_for_selector('[data-testid="sidebar-search-record"]', timeout=10000)
        records = pg.locator('[data-testid="sidebar-search-record"]').all()
        texts = [r.inner_text() for r in records]
        assert any("ZZZebra Inventory Hub" in t for t in texts), \
            f"Exact match not found in: {texts}"

        # One-letter typo must also find it
        pg.fill("#sidebar-search", "ZZZebra Inventry Hub")
        pg.wait_for_selector('[data-testid="sidebar-search-record"]', timeout=10000)
        records = pg.locator('[data-testid="sidebar-search-record"]').all()
        texts = [r.inner_text() for r in records]
        assert any("ZZZebra Inventory Hub" in t for t in texts), \
            f"Typo match not found in: {texts}"
    finally:
        pg.close()


def test_fragment_org_other_org_sees_none(app, client, browser):
    """The target from org A is not visible when signed in as org B."""
    data = _persona_with_fragment_org(app, "solution_architect")

    from .conftest import login
    login(client, data["user_b_id"])

    page_path = "/dashboard/overview"
    document = client.get(page_path).get_data(as_text=True)

    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _serve_app_in_browser(pg, client, document, page_path)
        pg.goto("http://app.test" + page_path)

        pg.fill("#sidebar-search", "ZZZebra Inventory Hub")
        pg.wait_for_selector('[data-testid="sidebar-search-results"]', timeout=10000)
        pg.wait_for_timeout(500)
        records = pg.locator('[data-testid="sidebar-search-record"]').count()
        assert records == 0, \
            f"Org B should not see ZZZebra Inventory Hub, got {records} records"
    finally:
        pg.close()


# ---- exact name ordering: "Customer" among 150+ names containing "customer" -------------------


def _persona_with_customer_org(app, enterprise_role):
    """Organisation with ~150 elements containing 'customer' plus one named exactly 'Customer'."""
    from datetime import datetime

    from app import db
    from app.models.user import User
    from app.models.application_portfolio import ApplicationComponent

    with app.app_context():
        org_id = make_org(db, "SearchCustomerOrg")
        user_id = make_user(db, org_id, "u", enterprise_role, role_name="Architect")
        User.query.filter_by(id=user_id).update({"onboarding_completed_at": datetime.utcnow()})

        # ~150 elements containing "customer" in their name
        for i in range(150):
            db.session.add(ApplicationComponent(
                name=f"Customer Portal {i:03d}",
                component_type="Application",
                organization_id=org_id,
            ))

        # One named exactly "Customer" — must be the first result
        db.session.add(ApplicationComponent(
            name="Customer",
            component_type="Application",
            organization_id=org_id,
        ))

        db.session.commit()
        return user_id, org_id


def test_customer_exact_name_is_first_result(app, client, browser):
    """~150 elements containing 'customer' plus one named exactly 'Customer': it is the first result."""
    user_id, _org_id = _persona_with_customer_org(app, "solution_architect")

    from .conftest import login
    login(client, user_id)

    page_path = "/dashboard/overview"
    document = client.get(page_path).get_data(as_text=True)

    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _serve_app_in_browser(pg, client, document, page_path)
        pg.goto("http://app.test" + page_path)

        pg.fill("#sidebar-search", "Customer")
        pg.wait_for_selector('[data-testid="sidebar-search-record"]', timeout=10000)
        records = pg.locator('[data-testid="sidebar-search-record"]').all()
        assert len(records) > 0, "No records found for 'Customer'"

        # The first record must be the exact match "Customer"
        first_text = records[0].inner_text()
        assert "Customer" in first_text, f"First record is not 'Customer': {first_text}"
        # Verify it's the exact "Customer" (not "Customer Portal 042" etc.)
        lines = [line.strip() for line in first_text.split("\n") if line.strip()]
        assert lines[0] == "Customer", f"First record name is not exactly 'Customer': {lines}"
    finally:
        pg.close()
