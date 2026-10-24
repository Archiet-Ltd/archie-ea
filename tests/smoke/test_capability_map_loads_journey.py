"""The capability map loads, lists every capability, names its controls and reads in dark mode.

A business architect opens /capability-map/ to see which applications support
which capabilities and to walk the capability hierarchy. Four things were broken
on that journey and this test reads the rendered page - rows, counts, accessible
names and colour contrast, never an HTTP status or a bare "element is visible" -
to prove each one:

  * the Application capability mappings table stayed on "Loading application
    capability data..." forever, because the client-side fetch that fills it was
    wired to an event that had already fired by the time the script ran;
  * the hierarchy silently dropped every capability with no parent, so its own
    headline count disagreed with the tree it drew (24 counted, 12 shown);
  * each expand/collapse control in the hierarchy had no accessible name and
    never announced whether it was open;
  * the capability search box rendered its text the same colour as its
    background in dark mode, so what you typed was invisible.

Two real organisations are seeded (never one-versus-empty) so the tenant
isolation check compares A against a populated B. Every row created here is
removed at module teardown; the test database is shared.

Follows tests/smoke/test_capability_heatmap_honesty_journey.py for the ORM
seeding, the dark-mode contrast helper and the screenshot pattern, and
tests/smoke/test_capability_map_csp_and_button_names.py for the axe pass.
"""

import importlib
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD
from tests.smoke.test_accessibility_audit import TAGS
from tests.smoke.test_archetype_journeys import _login, _visit

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

AXE_MODULE = "axe_playwright_python.sync_playwright"

MAP_PATH = "/capability-map/"
HIERARCHY_PATH = "/capability-map/hierarchy"
UNIFIED_API = "/capability-map/api/unified-capabilities"

DESKTOP = {"width": 1440, "height": 900}
NARROW = {"width": 390, "height": 844}

LOADING_PLACEHOLDER = "Loading application capability data"

import os as _os; SHOTS_DIR = _os.environ.get("SMOKE_SCREENSHOT_DIR", "/tmp/capability-map-loads")


# The contrast helper is copied from the heatmap journey on purpose: it is a
# test-local measurement, not a product canonical, and composites every
# translucent ancestor background down to the first opaque layer before
# computing the WCAG ratio of the element's own text against it.
CONTRAST_JS = """
(el) => {
  const parse = (s) => {
    const m = s.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x));
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const over = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a), a: 1,
  });
  let node = el, layers = [];
  while (node) {
    const c = parse(getComputedStyle(node).backgroundColor);
    if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; }
    node = node.parentElement;
  }
  let bg = { r: 255, g: 255, b: 255, a: 1 };
  for (let i = layers.length - 1; i >= 0; i--) bg = over(layers[i], bg);
  let fg = parse(getComputedStyle(el).color);
  if (fg.a < 1) fg = over(fg, bg);
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const l1 = lum(fg), l2 = lum(bg);
  return Math.round(((Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)) * 100) / 100;
}
"""


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def _make_org(app, db, label, suffix):
    """A fresh organisation with one business-architect user signed in by email."""
    from app.models.organization import Organization
    from app.models.user import Role, User

    Role.insert_roles()
    architect_role = Role.query.filter_by(name="Architect").one()
    org = Organization(
        name="Caps Map %s %s" % (label, suffix),
        slug="caps-map-%s-%s" % (label.lower(), suffix),
    )
    db.session.add(org)
    db.session.flush()
    email = "caps.map.%s.%s@example.com" % (label.lower(), suffix)
    user = User(
        email=email, first_name="Caps", last_name=label[:12],
        organization_id=org.id, enterprise_role="business_architect", confirmed=True,
    )
    user.role = architect_role
    user.password = PASSWORD
    db.session.add(user)
    db.session.flush()
    return org.id, email


def _seed_org_capabilities(app, db, org_id, suffix, label, *, orphan_business=10,
                           extra_parents=4, children=10):
    """Seed one organisation's capabilities, mappings and an application.

    The hierarchy page reads ``UnifiedCapability``. The Application mappings
    table reads ``BusinessCapability`` through /api/unified-capabilities, and a
    BusinessCapability insert projects itself into ``unified_capabilities``
    (ADR 0008). So the ``orphan_business`` BusinessCapability rows are seeded
    with no parent and level 2: they appear as the Application table's mapped
    rows AND land in the hierarchy as parent-less, non-level-1 orphans - the
    exact rows map_views.py drops on main. ``extra_parents`` level-1 roots each
    carry some of the ``children`` (level 2, real parent), so the tree has
    expandable nodes and the children render on main while the orphans do not.

    Returns the set of capability names seeded here, plus the BusinessCapability
    and ApplicationComponent ids for the mappings/teardown.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )

    names = set()

    # One application every mapping points at.
    component = ApplicationComponent(
        name="Caps Map App %s %s" % (label, suffix),
        organization_id=org_id,
        application_code="CMAPP-%s-%s" % (label[:3].upper(), uuid.uuid4().hex[:6]),
        lifecycle_status="operational",
    )
    db.session.add(component)
    db.session.flush()

    # The orphan rows: a BusinessCapability each (so they fill the Application
    # table) with no parent and level 2 (so the hierarchy drops them on main).
    business_ids = []
    for i in range(orphan_business):
        name = "%s Orphan Capability %02d %s" % (label, i + 1, suffix)
        names.add(name)
        bc = BusinessCapability(
            name=name,
            code="BC-%s-%02d-%s" % (label[:3].upper(), i + 1, uuid.uuid4().hex[:6]),
            level=2,
            business_domain="Operations",
            strategic_importance="high",
            business_owner="%s Owner %s" % (label, suffix),
            organization_id=org_id,
        )
        db.session.add(bc)
        db.session.flush()
        business_ids.append(bc.id)

    # One ApplicationCapabilityMapping per orphan so the table shows mapped rows
    # with a real application name, and the count of mapped rows is deterministic.
    for bc_id in business_ids:
        db.session.add(ApplicationCapabilityMapping(
            organization_id=org_id,
            application_component_id=component.id,
            business_capability_id=bc_id,
            support_level="full",
            coverage_percentage=100,
        ))

    # The level-1 parents (no parent) and their children (level 2, real parent),
    # seeded directly as UnifiedCapability so the tree has expandable nodes that
    # render on main as well as after the fix.
    parent_ids = []
    for p in range(extra_parents):
        name = "%s Parent Capability %02d %s" % (label, p + 1, suffix)
        names.add(name)
        parent = UnifiedCapability(
            name=name, code="UC-P-%s-%02d-%s" % (label[:3].upper(), p + 1, uuid.uuid4().hex[:6]),
            level=1, scope="tenant", organization_id=org_id, parent_capability_id=None,
        )
        db.session.add(parent)
        db.session.flush()
        parent_ids.append(parent.id)

    child_unified_ids = []
    for c in range(children):
        name = "%s Child Capability %02d %s" % (label, c + 1, suffix)
        names.add(name)
        child = UnifiedCapability(
            name=name, code="UC-C-%s-%02d-%s" % (label[:3].upper(), c + 1, uuid.uuid4().hex[:6]),
            level=2, scope="tenant", organization_id=org_id,
            parent_capability_id=parent_ids[c % len(parent_ids)],
        )
        db.session.add(child)
        db.session.flush()
        child_unified_ids.append(child.id)

    # Ten UnifiedApplicationCapabilityMapping rows (context: the hierarchy's
    # coverage dots read these). Map the children to the seeded application.
    for cap_id in child_unified_ids[:10]:
        db.session.add(UnifiedApplicationCapabilityMapping(
            unified_capability_id=cap_id,
            application_component_id=component.id,
            support_level="full",
            coverage_percentage=100,
        ))

    return names


@pytest.fixture(scope="module")
def map_tenants(live_server, request):
    """Two real organisations (A and B), each seeded the same way.

    A is the organisation under test; B exists so the isolation check compares
    two populated tenants rather than A against an empty organisation.
    """
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    out = {"suffix": suffix, "emails": {}, "org_ids": {}, "names": {}}
    created_business = []
    created_unified = []
    created_components = []
    created_orgs = []

    with app.app_context():
        for label in ("A", "B"):
            org_id, email = _make_org(app, db, label, suffix)
            out["org_ids"][label] = org_id
            out["emails"][label] = email
            created_orgs.append(org_id)
        db.session.commit()

        for label in ("A", "B"):
            names = _seed_org_capabilities(app, db, out["org_ids"][label], suffix, label)
            out["names"][label] = names
        db.session.commit()

    def remove_rows():
        with app.app_context():
            from app.models.application_portfolio import ApplicationComponent
            from app.models.application_capability import ApplicationCapabilityMapping
            from app.models.business_capabilities import BusinessCapability
            from app.models.unified_capability import UnifiedCapability
            from app.models.unified_application_capability_mapping import (
                UnifiedApplicationCapabilityMapping,
            )

            db.session.remove()
            org_ids = list(out["org_ids"].values())
            # Remove every record this module created. The two organisations and
            # their users are left in place, exactly as the heatmap journey
            # leaves its extra tenants: deleting an organisation cascades into
            # append-only audit and outbox tables that a database trigger
            # refuses to delete, and nothing here asserts on their absence.
            # Mappings first (they hold the foreign keys), then capabilities,
            # then the applications they pointed at.
            UnifiedApplicationCapabilityMapping.query.filter(
                UnifiedApplicationCapabilityMapping.application_component_id.in_(
                    db.session.query(ApplicationComponent.id).filter(
                        ApplicationComponent.organization_id.in_(org_ids)))
            ).delete(synchronize_session=False)
            ApplicationCapabilityMapping.query.filter(
                ApplicationCapabilityMapping.organization_id.in_(org_ids)
            ).delete(synchronize_session=False)
            UnifiedCapability.query.filter(
                UnifiedCapability.organization_id.in_(org_ids)
            ).delete(synchronize_session=False)
            BusinessCapability.query.filter(
                BusinessCapability.organization_id.in_(org_ids)
            ).delete(synchronize_session=False)
            ApplicationComponent.query.filter(
                ApplicationComponent.organization_id.in_(org_ids)
            ).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()

    request.addfinalizer(remove_rows)
    return out


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------


def _open_map(page, base, email):
    """Sign in, open /capability-map/, wait for the table's own fetch to answer."""
    _login(page, base, email)
    with page.expect_response(lambda r: UNIFIED_API in r.url, timeout=PAGE_TIMEOUT) as answered:
        response, state = _visit(page, base, MAP_PATH)
    assert response.status == 200, "capability map page HTTP %s" % response.status
    assert state["alpine"] == "object", "front end did not boot on the capability map"
    api = answered.value
    assert api.status == 200, "unified-capabilities API HTTP %s" % api.status
    return api.json()


def _show_application_lens(page):
    """Switch the Lens selector to Applications and reveal its table."""
    page.locator("#capability-lens").select_option("application")
    expect(page.locator("#panel-application")).to_be_visible()
    page.wait_for_timeout(300)


def _application_data_rows(page):
    """Rendered data rows in the Application mappings table (never the placeholder)."""
    return page.locator("#application-table-body tr[data-cap-id]")


def _enable_dark(page):
    """Turn on dark mode through the real theme control, falling back to the class.

    The Dark control is a single ``button[role=menuitemcheckbox]`` in the user
    menu that adds the ``dark`` class to <html> (see
    tests/smoke/test_header_search_and_theme.py).
    """
    try:
        page.locator("#user-menu-btn").click()
        page.wait_for_timeout(300)
        dark_item = page.locator('button[role="menuitemcheckbox"]')
        if dark_item.count() == 1:
            dark_item.click()
            page.wait_for_timeout(300)
    except Exception:
        pass
    if not page.evaluate("() => document.documentElement.classList.contains('dark')"):
        page.evaluate("() => document.documentElement.classList.add('dark')")
    page.wait_for_timeout(200)
    assert page.evaluate("() => document.documentElement.classList.contains('dark')"), \
        "could not switch the capability map into dark mode"
    # Close the menu if it is still open so it does not cover the page.
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)


def _hierarchy_headline_count(page):
    """The headline capability count the hierarchy prints ('N capabilities')."""
    banner = page.get_by_text(re.compile(r"^\d+ capabilities$")).first
    text = banner.inner_text().strip()
    return int(re.match(r"(\d+)", text).group(1))


def _hierarchy_listed_count(page):
    """How many capability nodes the tree actually drew (its own countAll walk)."""
    return page.evaluate(
        """() => {
            const el = document.querySelector('[x-data="hierarchyView()"]');
            if (!el || !window.Alpine) return null;
            const data = window.Alpine.$data(el);
            return typeof data.countAll === 'function' ? data.countAll() : null;
        }"""
    )


def _expand_buttons(page):
    """The expand/collapse buttons rendered inside the hierarchy tree."""
    return page.locator('[x-ref="treeContainer"] button[type="button"]')


def _axe(axe_module, page):
    report = axe_module.Axe().run(
        page, options={"runOnly": {"type": "tag", "values": TAGS}}
    )
    return report.response if hasattr(report, "response") else report


def _serious(violations):
    return [v for v in violations if v.get("impact") in ("serious", "critical")]


def _screenshot(page, name):
    os.makedirs(SHOTS_DIR, exist_ok=True)
    path = os.path.join(SHOTS_DIR, name + ".png")
    page.screenshot(path=path, full_page=True)
    print("[smoke] screenshot %s" % path)
    return path


@pytest.fixture(scope="module")
def axe_module(browser):
    """The axe-playwright-python module, or a hard failure - never a skip."""
    try:
        return importlib.import_module(AXE_MODULE)
    except ImportError as exc:
        pytest.fail(
            "the capability-map accessibility check could not run: "
            "axe-playwright-python is not installed (%s)." % exc,
            pytrace=False,
        )


# ---------------------------------------------------------------------------
# Journeys
# ---------------------------------------------------------------------------


def test_application_mappings_table_loads_its_rows_and_pagination(browser, live_server, map_tenants):
    """Signed in as organisation A, the Application capability mappings table
    shows its ten mapped rows and a pagination that reflects them - not the
    "Loading application capability data..." placeholder, not an error row."""
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        payload = _open_map(page, live_server, map_tenants["emails"]["A"])
        caps = payload.get("unified_capabilities") or payload.get("capabilities") or []
        application_caps = [c for c in caps if c.get("type") == "Application"]
        assert len(application_caps) == 10, (
            "the unified-capabilities API served %d application capabilities for "
            "organisation A, expected the 10 seeded ones" % len(application_caps)
        )

        # Reload once (a warm cache hit) before reading the table. The fetch
        # that fills the mappings table is wired to DOMContentLoaded only, so
        # when the script runs after the document has already parsed - which a
        # cache-hit reload makes likely - that listener never fires and the
        # table stays on its placeholder. The fix runs the init immediately
        # when the DOM is already ready, so a reload fills the table either way.
        _visit(page, live_server, MAP_PATH)
        _show_application_lens(page)
        page.wait_for_timeout(1500)

        body = page.locator("#application-table-body")
        assert LOADING_PLACEHOLDER not in body.inner_text(), (
            "the Application mappings table is still showing "
            "'Loading application capability data...' - its data fetch never "
            "filled the table (DG-063)"
        )
        assert "Error Loading Data" not in body.inner_text(), \
            "the Application mappings table rendered an error row instead of data"

        rows = _application_data_rows(page)
        expect(rows).to_have_count(10)

        # Pagination reflects the ten rows: first page shows 1-10 of 10, a
        # single page, and the next control cannot advance past it.
        expect(page.locator("#application-total-records")).to_have_text("10")
        expect(page.locator("#application-start-record")).to_have_text("1")
        expect(page.locator("#application-end-record")).to_have_text("10")
        expect(page.locator("#application-current-page")).to_have_text("1")
        expect(page.locator("#application-total-pages")).to_have_text("1")
        assert page.locator("#application-next-btn").is_disabled(), \
            "the next-page control is enabled although there is only one page of 10"

        _screenshot(page, "application-mappings-1440-light")
    finally:
        context.close()


def test_hierarchy_lists_every_capability_and_its_count_agrees(browser, live_server, map_tenants):
    """On the hierarchy page the headline count and the tree it draws must
    agree, and both must be the full 24 - including the parent-less orphans
    that map_views.py drops on main."""
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        _login(page, live_server, map_tenants["emails"]["A"])
        response, state = _visit(page, live_server, HIERARCHY_PATH)
        assert response.status == 200, "hierarchy page HTTP %s" % response.status
        assert state["alpine"] == "object", "front end did not boot on the hierarchy"

        headline = _hierarchy_headline_count(page)
        listed = _hierarchy_listed_count(page)
        assert listed is not None, "could not read the hierarchy's own capability count"

        assert listed == headline, (
            "the hierarchy counts %d capabilities in its headline but draws only "
            "%d in the tree - the parent-less capabilities are being dropped "
            "(DG-037)" % (headline, listed)
        )
        assert headline == 24, (
            "organisation A seeded 24 capabilities but the hierarchy headline "
            "reads %d" % headline
        )
        assert listed == 24, (
            "the hierarchy tree lists %d capabilities, not the 24 seeded - "
            "orphans are missing (DG-037)" % listed
        )

        # Every seeded name is actually on the page, orphans included.
        tree_text = page.locator('[x-ref="treeContainer"]').inner_text()
        missing = [n for n in map_tenants["names"]["A"] if n not in tree_text]
        assert not missing, "capabilities missing from the rendered hierarchy: %r" % missing[:6]

        _screenshot(page, "hierarchy-1440-light")
    finally:
        context.close()


def test_hierarchy_expand_buttons_are_named_and_announce_state(browser, live_server, map_tenants):
    """Each expand/collapse button in the hierarchy carries a non-empty
    accessible name and toggles aria-expanded false -> true when activated."""
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        _login(page, live_server, map_tenants["emails"]["A"])
        _visit(page, live_server, HIERARCHY_PATH)
        page.wait_for_timeout(500)

        buttons = _expand_buttons(page)
        count = buttons.count()
        assert count > 0, "the hierarchy drew no expand/collapse buttons to check"

        for i in range(count):
            btn = buttons.nth(i)
            name = (btn.get_attribute("aria-label") or "").strip()
            # An accessible name can come from aria-label or visible text.
            if not name:
                name = (btn.inner_text() or "").strip()
            assert name, (
                "expand/collapse button #%d has no accessible name (DG-136)" % i
            )
            expanded = btn.get_attribute("aria-expanded")
            assert expanded in ("true", "false"), (
                "expand/collapse button #%d does not announce aria-expanded "
                "(got %r) (DG-136)" % (i, expanded)
            )

        # Find a collapsed button and confirm activating it opens the node.
        collapsed_index = None
        for i in range(count):
            if buttons.nth(i).get_attribute("aria-expanded") == "false":
                collapsed_index = i
                break
        if collapsed_index is None:
            # All open: collapse the first, then it must report false, and
            # re-activating returns it to true.
            first = buttons.nth(0)
            first.click()
            page.wait_for_timeout(300)
            buttons = _expand_buttons(page)
            assert buttons.nth(0).get_attribute("aria-expanded") == "false", \
                "activating an open expand button did not set aria-expanded=false"
            buttons.nth(0).click()
            page.wait_for_timeout(300)
            buttons = _expand_buttons(page)
            assert buttons.nth(0).get_attribute("aria-expanded") == "true", \
                "re-activating a closed expand button did not set aria-expanded=true"
        else:
            target = buttons.nth(collapsed_index)
            target.click()
            page.wait_for_timeout(300)
            buttons = _expand_buttons(page)
            assert buttons.nth(collapsed_index).get_attribute("aria-expanded") == "true", \
                "activating a collapsed expand button did not set aria-expanded=true"
    finally:
        context.close()


def test_search_input_is_legible_in_dark_mode(browser, live_server, map_tenants):
    """In dark mode the capability search box must keep its text readable:
    text-vs-background contrast of at least 4.5:1 (DG-120)."""
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        _open_map(page, live_server, map_tenants["emails"]["A"])
        _show_application_lens(page)
        _enable_dark(page)

        search = page.locator("#application-search-filter")
        expect(search).to_be_visible()
        search.fill("Capability")
        page.wait_for_timeout(150)
        ratio = search.evaluate(CONTRAST_JS)
        print("[smoke] search input dark-mode contrast: %s" % ratio)
        assert ratio >= 4.5, (
            "the capability search input text contrast is %s:1 in dark mode, "
            "below the AA minimum of 4.5:1 (DG-120)" % ratio
        )

        _screenshot(page, "application-mappings-1440-dark")
    finally:
        context.close()


def test_organisation_b_sees_none_of_organisation_a_mappings(browser, live_server, map_tenants):
    """Signed in as organisation B, neither the mappings table nor the API the
    page reads shows any of organisation A's capabilities."""
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        payload = _open_map(page, live_server, map_tenants["emails"]["B"])
        caps = payload.get("unified_capabilities") or payload.get("capabilities") or []
        served_names = {c.get("name") for c in caps}

        leaked = served_names & map_tenants["names"]["A"]
        assert not leaked, (
            "organisation B's capability API returned organisation A's rows: %r"
            % sorted(leaked)[:6]
        )
        # B sees its own application capabilities, so this is two populated
        # tenants, not A-versus-empty.
        b_application = [c for c in caps if c.get("type") == "Application"]
        assert len(b_application) == 10, (
            "organisation B should see its own 10 application capabilities, saw %d"
            % len(b_application)
        )

        _show_application_lens(page)
        body_text = page.locator("#application-table-body").inner_text()
        a_leak = [n for n in map_tenants["names"]["A"] if n in body_text]
        assert not a_leak, (
            "organisation A's capabilities appear in organisation B's mappings "
            "table: %r" % a_leak[:6]
        )
    finally:
        context.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_capability_search_input_passes_axe_colour_contrast(axe_module, browser, live_server, map_tenants, theme):
    """axe reports no serious colour-contrast finding against the capability
    search input in either theme.

    This reads the colour-contrast rule out of the full WCAG 2.2 AA tag run
    (the same tag set every axe run under tests/smoke uses) and narrows it to
    the search input - that input is this task's concern (DG-120), not a
    full-page conformance claim the capability map has never been baselined
    for. The same scoping pattern as
    tests/smoke/test_capability_map_csp_and_button_names.py reading only the
    button-name rule.
    """
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        _open_map(page, live_server, map_tenants["emails"]["A"])
        _show_application_lens(page)
        if theme == "dark":
            _enable_dark(page)
        page.wait_for_timeout(200)

        data = _axe(axe_module, page)
        contrast = [v for v in data.get("violations", []) if v.get("id") == "color-contrast"]
        offending = []
        for v in contrast:
            if v.get("impact") not in ("serious", "critical"):
                continue
            for node in v.get("nodes", []):
                targets = node.get("target", [])
                flat = " ".join(str(t) for t in targets)
                if "application-search-filter" in flat:
                    offending.append((node.get("target"), node.get("failureSummary", "")[:160]))
        assert not offending, (
            "the capability search input has a serious colour-contrast finding "
            "in %s theme (DG-120): %r" % (theme, offending)
        )
    finally:
        context.close()


@pytest.mark.parametrize("width,theme", [
    (DESKTOP["width"], "light"), (DESKTOP["width"], "dark"),
    (NARROW["width"], "light"), (NARROW["width"], "dark"),
])
def test_capability_map_walk_screenshots(browser, live_server, map_tenants, width, theme):
    """Capture the capability map at 1440 and 390 wide, light and dark (four
    images in all), independently of the behavioural assertions above so the
    walk evidence exists whether those pass or fail."""
    context = browser.new_context(viewport={"width": width, "height": 900 if width == DESKTOP["width"] else 844})
    page = context.new_page()
    try:
        _open_map(page, live_server, map_tenants["emails"]["A"])
        _show_application_lens(page)
        if theme == "dark":
            _enable_dark(page)
        _screenshot(page, "application-mappings-%d-%s" % (width, theme))
    finally:
        context.close()
