"""The capability heatmap must show only maturity that was actually recorded.

Before this journey existed the grid rendered an unassessed capability as
Level 1, an unset target as Level 3, a 0/0 Health ratio as "0%", and silently
dropped every capability with no domain. This journey signs in as the CTO
persona and reads the rendered page - text and ARIA state, never internal ids -
across the tenant states that can each hide a different invention:

  * a tenant with a fully assessed domain, a partly assessed domain, a domain
    with nothing assessed, and capabilities with no domain at all
  * a tenant where nothing is assessed (no percentage may appear anywhere)
  * a tenant with no capabilities (the existing empty state, unchanged)
  * a catalogue-shaped row (no organisation, no scope) that must appear on no
    tenant's grid

in both view modes, both themes, at a desktop and a narrow width, and by
keyboard alone (Tab to a row, Enter or Space opens the detail panel).

Follows tests/smoke/test_dashboard_health_agreement.py: the shared `seeded`
organisation and its CTO user are the main tenant, `_login` / `_visit` come from
test_archetype_journeys, and the two extra tenants are created through the ORM
the same way `seeded` creates its own rows. Every capability and domain this
module creates is removed at module teardown; the test database is shared.
"""

import os
import re
import tempfile
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD
from tests.smoke.test_archetype_journeys import _login, _visit

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

HEATMAP_PATH = "/dashboard/capability-heatmap"
HEATMAP_API = "/dashboard/api/capability-heatmap"
EM_DASH = "—"
DESKTOP = {"width": 1280, "height": 900}
NARROW = {"width": 390, "height": 844}

# Column order of the rendered grid, by header text, so assertions read like
# the screen does rather than like the template.
COLUMNS = ["Domain", "1", "2", "3", "4", "5", "Not assessed", "Health", "Caps"]

# A "0%" (or "0.0%") that is not the tail of a larger number, "null%", an
# undefined or NaN that leaked out of a JS expression, or an average that says
# it was measured over nothing. None of these may reach the page.
INVENTED = re.compile(r"(?<![\d.])0(?:\.0)?%|null%|undefined|NaN|\(0 measured\)")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def heat_tenants(seeded, request):
    """Capabilities in the seeded CTO's organisation plus two further tenants.

    Returns the names to look for on screen, keyed by tenant state, and the
    CTO sign-in for each tenant. Domain codes and names carry a random suffix
    so nothing here can collide with other sessions' rows.
    """
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    out = {"suffix": suffix, "emails": {}, "names": {}, "domains": {}}
    created = {"capabilities": [], "domains": []}

    def domain(session, BusinessDomain, label):
        code = ("H" + uuid.uuid4().hex[:8]).upper()[:10]
        row = BusinessDomain(code=code, name="Heat %s %s" % (label, suffix))
        session.add(row)
        session.flush()
        created["domains"].append(row.id)
        return row

    def capability(session, UnifiedCapability, org_id, name, *, domain_row=None,
                   current=None, target=None, scope="tenant"):
        row = UnifiedCapability(
            name=name, code="HC-%s" % uuid.uuid4().hex[:8], level=1, scope=scope,
            organization_id=org_id,
            domain_id=domain_row.id if domain_row is not None else None,
            current_maturity_level=current, target_maturity_level=target,
        )
        session.add(row)
        session.flush()
        created["capabilities"].append(row.id)
        return row

    def cto_user(session, User, architect_role, org, label):
        email = "smoke.cto-%s.%s@example.com" % (label, suffix)
        user = User(email=email, first_name="Smoke", last_name="cto-" + label,
                    organization_id=org.id, enterprise_role="cto", confirmed=True)
        user.role = architect_role
        user.password = PASSWORD
        session.add(user)
        session.flush()
        return email

    with app.app_context():
        from app.models.organization import Organization
        from app.models.unified_capability import BusinessDomain, UnifiedCapability
        from app.models.user import Role, User

        session = db.session
        architect_role = Role.query.filter_by(name="Architect").one()

        # Tenant 1: the seeded organisation and its CTO. Four groups.
        mixed_org = seeded["ids"]["org"]
        out["emails"]["mixed"] = seeded["emails"]["cto"]
        d_all = domain(session, BusinessDomain, "all assessed")
        d_some = domain(session, BusinessDomain, "some unassessed")
        d_none = domain(session, BusinessDomain, "none assessed")
        out["domains"] = {"all": d_all.name, "some": d_some.name, "none": d_none.name}
        names = {
            "all_a": "Heat all A %s" % suffix,        # current 4, target 5
            "all_b": "Heat all B %s" % suffix,        # current 3, target 4
            "some_assessed": "Heat some assessed %s" % suffix,      # current 3, target 4
            "some_unassessed": "Heat some unassessed %s" % suffix,  # nothing recorded
            "none_1": "Heat none one %s" % suffix,    # nothing recorded
            "none_2": "Heat none two %s" % suffix,    # nothing recorded
            "nodomain_current_only": "Heat nodomain current only %s" % suffix,  # current 2, no target
            "nodomain_unassessed": "Heat nodomain unassessed %s" % suffix,      # nothing recorded
            "catalogue": "Heat catalogue unclassified %s" % suffix,  # org NULL, scope NULL
        }
        out["names"] = names
        capability(session, UnifiedCapability, mixed_org, names["all_a"], domain_row=d_all, current=4, target=5)
        capability(session, UnifiedCapability, mixed_org, names["all_b"], domain_row=d_all, current=3, target=4)
        capability(session, UnifiedCapability, mixed_org, names["some_assessed"], domain_row=d_some, current=3, target=4)
        capability(session, UnifiedCapability, mixed_org, names["some_unassessed"], domain_row=d_some)
        capability(session, UnifiedCapability, mixed_org, names["none_1"], domain_row=d_none)
        capability(session, UnifiedCapability, mixed_org, names["none_2"], domain_row=d_none)
        capability(session, UnifiedCapability, mixed_org, names["nodomain_current_only"], current=2)
        capability(session, UnifiedCapability, mixed_org, names["nodomain_unassessed"])
        # The shape the bulk catalogue seeder writes: no organisation and no
        # scope. Not a shared reference row, so no tenant may see it.
        capability(session, UnifiedCapability, None, names["catalogue"], domain_row=d_all, scope=None)

        # Tenant 2: nothing assessed anywhere.
        unassessed_org = Organization(name="Smoke Heat Unassessed %s" % suffix,
                                      slug="smoke-heat-unassessed-%s" % suffix)
        session.add(unassessed_org)
        session.flush()
        out["emails"]["unassessed"] = cto_user(session, User, architect_role, unassessed_org, "unassessed")
        d_u = domain(session, BusinessDomain, "unassessed only")
        out["domains"]["unassessed_only"] = d_u.name
        names["u_1"] = "Heat U one %s" % suffix
        names["u_2"] = "Heat U two %s" % suffix
        names["u_nodomain"] = "Heat U nodomain %s" % suffix
        capability(session, UnifiedCapability, unassessed_org.id, names["u_1"], domain_row=d_u)
        capability(session, UnifiedCapability, unassessed_org.id, names["u_2"], domain_row=d_u)
        capability(session, UnifiedCapability, unassessed_org.id, names["u_nodomain"])

        # Tenant 3: no capabilities at all.
        empty_org = Organization(name="Smoke Heat Empty %s" % suffix,
                                 slug="smoke-heat-empty-%s" % suffix)
        session.add(empty_org)
        session.flush()
        out["emails"]["empty"] = cto_user(session, User, architect_role, empty_org, "empty")

        session.commit()

    def remove_rows():
        with app.app_context():
            from app.models.unified_capability import BusinessDomain, UnifiedCapability

            db.session.remove()
            if created["capabilities"]:
                db.session.query(UnifiedCapability).filter(
                    UnifiedCapability.id.in_(created["capabilities"])
                ).delete(synchronize_session=False)
            if created["domains"]:
                db.session.query(BusinessDomain).filter(
                    BusinessDomain.id.in_(created["domains"])
                ).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()

    request.addfinalizer(remove_rows)
    return out


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------


def _open_page(page, base, email):
    """Sign in, open the heatmap, wait for the grid's own API call to answer."""
    _login(page, base, email)
    with page.expect_response(lambda r: HEATMAP_API in r.url, timeout=PAGE_TIMEOUT) as answered:
        response, state = _visit(page, base, HEATMAP_PATH)
    assert response.status == 200, "heatmap page HTTP %s" % response.status
    assert state["alpine"] == "object", "front end did not boot"
    api = answered.value
    assert api.status == 200, "heatmap API HTTP %s" % api.status
    page.wait_for_timeout(300)
    return api.json()["data"]


def _switch_view(page, mode):
    """Change the View selector; the page refetches with or without group_by."""
    def is_refetch(r):
        if HEATMAP_API not in r.url:
            return False
        return ("group_by=domain" in r.url) == (mode == "investment")

    with page.expect_response(is_refetch, timeout=PAGE_TIMEOUT) as answered:
        page.get_by_label("View", exact=True).select_option(mode)
    page.wait_for_timeout(300)
    return answered.value.json()["data"]


def _content(page):
    return page.locator('[x-data="capabilityHeatmap()"]')


def _row(page, domain_name):
    return page.get_by_role("button", name="Show detail for " + domain_name, exact=True)


def _cells(row):
    """Visible cell texts keyed by header, in the order the screen shows them."""
    texts = [t.strip() for t in row.locator("td").all_inner_texts()]
    headers = COLUMNS + (["Investment"] if len(texts) > len(COLUMNS) else [])
    return dict(zip(headers, texts))


def _health_cell(row):
    return row.locator("td").nth(COLUMNS.index("Health"))


def _investment_cell(row):
    return row.locator("td").nth(len(COLUMNS))


def _card(page, label):
    return _content(page).locator("p", has_text=re.compile("^%s$" % re.escape(label))).locator("..").locator("p").nth(1)


def _panel(page):
    return _content(page).locator("h2").locator("../..")


def _open_panel(page, domain_name):
    row = _row(page, domain_name)
    row.click()
    expect(row).to_have_attribute("aria-expanded", "true")
    panel = _panel(page)
    expect(panel.get_by_role("heading", name=domain_name, exact=True)).to_be_visible()
    return panel


def _close_panel(page):
    page.get_by_role("button", name="Close detail panel", exact=True).click()
    expect(_content(page).locator("h2")).to_be_hidden()


def _panel_figure(panel, label):
    """The figure under a detail-panel label: its visible text and its
    screen-reader text, so an em-dash can be checked with its explanation.
    Only rendered elements count; a hidden alternative branch is ignored."""
    block = panel.get_by_text(label, exact=True).locator("..")
    visible, sr = block.evaluate(
        "b => { const ps = [...b.querySelectorAll(':scope > p')].slice(1)"
        "  .filter(p => getComputedStyle(p).display !== 'none');"
        "  return [ps.map(p => p.innerText.trim()).join(' '),"
        "          ps.map(p => [...p.querySelectorAll('.sr-only')].map(s => s.textContent.trim()).join(' ')).join(' ').trim()]; }")
    return visible, sr


def _panel_columns(panel):
    """Every column of "Capabilities by Maturity Level": heading -> rendered names."""
    grid = panel.get_by_text("Capabilities by Maturity Level", exact=True).locator("..").locator(".grid")
    return grid.evaluate(
        "g => Object.fromEntries([...g.children].map(col => {"
        "  const ps = [...col.querySelectorAll('p')].filter(p => getComputedStyle(p).display !== 'none')"
        "    .map(p => p.textContent.trim());"
        "  return [ps[0], ps.slice(1)]; }))")


def _panel_column(panel, heading):
    columns = _panel_columns(panel)
    assert heading in columns, "no %r column in the detail panel: %r" % (heading, list(columns))
    return columns[heading]


def _marker(cell):
    """The rendered direct-child span of a cell (the absence pill or the figure)."""
    return cell.locator(":scope > span").last


def _assert_nothing_invented(page):
    text = _content(page).inner_text()
    found = INVENTED.findall(text)
    assert not found, "invented figure(s) on the page: %r" % found


def _screenshot(page, name):
    folder = os.path.join(tempfile.gettempdir(), "capability-heatmap-honesty")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name + ".png")
    page.screenshot(path=path, full_page=True)
    print("[smoke] screenshot %s" % path)
    return path


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
  // Effective background: composite every translucent ancestor background
  // down to the first opaque one (or white).
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
# Journeys
# ---------------------------------------------------------------------------


def test_mixed_tenant_grid_shows_only_recorded_maturity_in_both_view_modes(browser, live_server, heat_tenants):
    """Four groups on one tenant: every figure on screen is either recorded or
    a named absence, the no-domain group is present and last, the catalogue
    row is absent, and both view modes keep that true."""
    names, domains = heat_tenants["names"], heat_tenants["domains"]
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        data = _open_page(page, live_server, heat_tenants["emails"]["mixed"])

        # Summary cards agree with the API's own totals and count real domains only.
        expect(_card(page, "Domains")).to_have_text(str(data["total_domains"]))
        expect(_card(page, "Capabilities")).to_have_text(str(data["total_capabilities"]))
        assert data["total_domains"] >= 3 and data["total_capabilities"] >= 8

        # The "Not assessed" column and legend entry are present and labelled.
        expect(page.get_by_role("columnheader", name="Not assessed", exact=True)).to_be_visible()
        legend = _content(page).get_by_text("Maturity:", exact=True).locator("..")
        expect(legend.get_by_text("Not assessed", exact=True)).to_be_visible()
        for level, label in ((1, "Initial"), (5, "Optimizing")):
            expect(legend.get_by_text("%d %s %s" % (level, EM_DASH, label), exact=True)).to_be_visible()

        # Row order: real domains, then the no-domain group last, with no "(code)".
        rows = _content(page).locator("tbody tr")
        labels = [r.get_attribute("aria-label") for r in rows.all()]
        assert labels[-1] == "Show detail for No domain", labels
        assert labels.index("Show detail for " + domains["all"]) < labels.index("Show detail for No domain")
        no_domain = _row(page, "No domain")
        assert "(" not in _cells(no_domain)["Domain"]
        expect(no_domain).to_have_attribute("aria-expanded", "false")

        # Fully assessed: recorded levels in their own columns, a real Health figure.
        cells = _cells(_row(page, domains["all"]))
        assert cells["1"] == "-" and cells["3"] == "1" and cells["4"] == "1"
        assert cells["Not assessed"] == "-"
        assert cells["Health"] == "77.8%" and cells["Caps"] == "2"

        # Partly assessed: the unassessed one is in its own column, never Level 1.
        cells = _cells(_row(page, domains["some"]))
        assert cells["1"] == "-" and cells["3"] == "1" and cells["Not assessed"] == "1"
        assert cells["Health"] == "75%" and cells["Caps"] == "2"

        # Nothing assessed: no level column, a count of 2 not assessed, Health absent.
        row_none = _row(page, domains["none"])
        cells = _cells(row_none)
        assert all(cells[c] == "-" for c in ("1", "2", "3", "4", "5"))
        assert cells["Not assessed"] == "2" and cells["Caps"] == "2"
        assert EM_DASH in _health_cell(row_none).text_content()
        assert "%" not in _health_cell(row_none).text_content()
        assert "needs a recorded current and a recorded target maturity" in " ".join(
            _health_cell(row_none).locator(".sr-only").all_text_contents())

        # No domain: a recorded current with no target still yields no Health.
        cells = _cells(no_domain)
        assert cells["1"] == "-" and cells["2"] == "1" and cells["Not assessed"] == "1"
        assert cells["Caps"] == "2"
        assert EM_DASH in _health_cell(no_domain).text_content()

        # Every Health cell on the grid is either a recorded percentage or the marker.
        for row in rows.all():
            health = _health_cell(row).text_content().strip()
            assert EM_DASH in health or re.fullmatch(r"\d+(\.\d)?%", _cells(row)["Health"]), health
        _assert_nothing_invented(page)

        # Detail panels state the denominator for every average.
        panel = _open_panel(page, domains["all"])
        assert _panel_figure(panel, "Avg Maturity")[0] == "3.5 (2 measured)"
        assert _panel_figure(panel, "Avg Target")[0] == "4.5 (2 measured)"
        assert _panel_figure(panel, "Health Score")[0] == "77.8%"
        assert _panel_column(panel, "Level 4") == [names["all_a"]]
        assert _panel_column(panel, "Level 3") == [names["all_b"]]
        assert _panel_column(panel, "Level 1") == ["None"]
        assert _panel_column(panel, "Not assessed") == ["None"]
        # The catalogue-shaped row sits in this domain and must not be listed.
        assert names["catalogue"] not in _content(page).inner_text()
        _close_panel(page)

        panel = _open_panel(page, domains["none"])
        visible, sr = _panel_figure(panel, "Avg Maturity")
        assert EM_DASH in visible and "no recorded current maturity" in sr, (visible, sr)
        visible, sr = _panel_figure(panel, "Avg Target")
        assert EM_DASH in visible and "no recorded target maturity" in sr, (visible, sr)
        visible, sr = _panel_figure(panel, "Health Score")
        assert EM_DASH in visible and "recorded current and a recorded target" in sr, (visible, sr)
        assert sorted(_panel_column(panel, "Not assessed")) == sorted([names["none_1"], names["none_2"]])
        for level in range(1, 6):
            assert _panel_column(panel, "Level %d" % level) == ["None"]
        panel_text = panel.inner_text()
        assert "measured" not in panel_text and "%" not in panel_text
        _close_panel(page)

        panel = _open_panel(page, "No domain")
        assert _panel_figure(panel, "Avg Maturity")[0] == "2 (1 measured)"
        assert EM_DASH in _panel_figure(panel, "Avg Target")[0]
        assert EM_DASH in _panel_figure(panel, "Health Score")[0]
        assert _panel_column(panel, "Level 2") == [names["nodomain_current_only"]]
        assert _panel_column(panel, "Not assessed") == [names["nodomain_unassessed"]]
        assert _panel_column(panel, "Level 1") == ["None"]
        _close_panel(page)
        _screenshot(page, "mixed-maturity-1280-light")

        # With Investment: a real domain shows a currency figure, the no-domain
        # group shows the marker, and nothing else changes.
        inv = _switch_view(page, "investment")
        assert inv["domains"][-1]["code"] is None and "total_investment" not in inv["domains"][-1]
        expect(page.get_by_role("columnheader", name="Investment", exact=True)).to_be_visible()
        expect(_card(page, "Total Investment")).to_be_visible()
        expect(_row(page, "No domain")).to_have_attribute("aria-expanded", "false")
        real_investment = _investment_cell(_row(page, domains["all"])).inner_text().strip()
        assert EM_DASH not in real_investment and re.search(r"\d", real_investment), real_investment
        no_domain_investment = _investment_cell(_row(page, "No domain"))
        assert EM_DASH in no_domain_investment.text_content()
        assert "Investment not available for this group" in " ".join(
            no_domain_investment.locator(".sr-only").all_text_contents())
        cells = _cells(_row(page, domains["none"]))
        assert cells["Not assessed"] == "2" and all(cells[c] == "-" for c in ("1", "2", "3", "4", "5"))
        _assert_nothing_invented(page)

        panel = _open_panel(page, "No domain")
        visible, sr = _panel_figure(panel, "Investment")
        assert EM_DASH in visible and "Investment not available" in sr, (visible, sr)
        assert _panel_figure(panel, "Avg Maturity")[0] == "2 (1 measured)"
        _close_panel(page)
        panel = _open_panel(page, domains["all"])
        assert EM_DASH not in _panel_figure(panel, "Investment")[0]
        assert _panel_figure(panel, "Avg Target")[0] == "4.5 (2 measured)"
        _close_panel(page)
        _screenshot(page, "mixed-investment-1280-light")

        # And back: Maturity Only still renders the same grid.
        _switch_view(page, "maturity")
        expect(page.get_by_role("columnheader", name="Investment", exact=True)).to_be_hidden()
        assert _cells(_row(page, domains["some"]))["Not assessed"] == "1"
    finally:
        context.close()


def test_unassessed_tenant_shows_no_percentage_and_no_level_anywhere(browser, live_server, heat_tenants):
    """A tenant with nothing recorded: not one percentage, level count or
    average on the whole page, in either view mode."""
    names, domains = heat_tenants["names"], heat_tenants["domains"]
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        data = _open_page(page, live_server, heat_tenants["emails"]["unassessed"])
        assert data["total_domains"] == 1 and data["total_capabilities"] == 3
        expect(_card(page, "Domains")).to_have_text("1")
        expect(_card(page, "Capabilities")).to_have_text("3")

        for mode in ("maturity", "investment"):
            if mode == "investment":
                _switch_view(page, mode)
            rows = _content(page).locator("tbody tr")
            expect(rows).to_have_count(2)
            cells = _cells(_row(page, domains["unassessed_only"]))
            assert all(cells[c] == "-" for c in ("1", "2", "3", "4", "5")), cells
            assert cells["Not assessed"] == "2" and cells["Caps"] == "2"
            cells = _cells(_row(page, "No domain"))
            assert all(cells[c] == "-" for c in ("1", "2", "3", "4", "5")), cells
            assert cells["Not assessed"] == "1" and cells["Caps"] == "1"
            for row in rows.all():
                assert EM_DASH in _health_cell(row).text_content()
            grid_text = _content(page).locator("table").inner_text()
            assert "%" not in grid_text, grid_text
            _assert_nothing_invented(page)

            panel = _open_panel(page, domains["unassessed_only"])
            assert sorted(_panel_column(panel, "Not assessed")) == sorted([names["u_1"], names["u_2"]])
            assert _panel_column(panel, "Level 1") == ["None"]
            text = panel.inner_text()
            assert "%" not in text and "measured" not in text, text
            _close_panel(page)
        _screenshot(page, "unassessed-investment-1280-light")
    finally:
        context.close()


def test_empty_tenant_keeps_the_existing_empty_state(browser, live_server, heat_tenants):
    """A tenant with no capabilities gets the same empty state as before, in
    both view modes, and no grid, marker or percentage."""
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        data = _open_page(page, live_server, heat_tenants["emails"]["empty"])
        assert data["domains"] == [] and data["total_capabilities"] == 0
        for mode in ("maturity", "investment"):
            if mode == "investment":
                _switch_view(page, mode)
            expect(_content(page).get_by_text(
                "No capability data available. Add capabilities to see the heatmap.", exact=True)
            ).to_be_visible()
            expect(_card(page, "Domains")).to_have_text("0")
            expect(_card(page, "Capabilities")).to_have_text("0")
            expect(_content(page).locator("table")).to_be_hidden()
            assert "%" not in _content(page).inner_text()
            _assert_nothing_invented(page)
        _screenshot(page, "empty-investment-1280-light")
    finally:
        context.close()


def test_keyboard_reaches_every_row_and_opens_the_detail_panel(browser, live_server, heat_tenants):
    """Tab from the View selector lands on the first grid row; Enter and Space
    each open the panel for the focused row; the focus ring is visible; the
    denominators and the Not-assessed names are reachable without a mouse."""
    names, domains = heat_tenants["names"], heat_tenants["domains"]
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        _open_page(page, live_server, heat_tenants["emails"]["mixed"])
        page.get_by_label("View", exact=True).focus()

        def focused():
            return page.evaluate(
                "() => { const e = document.activeElement; return {"
                "  role: e.getAttribute('role'), label: e.getAttribute('aria-label'),"
                "  expanded: e.getAttribute('aria-expanded') } }")

        def ring_is_painted(row):
            """A focus ring must be visible, not merely declared: compare the
            pixels around the row with and without focus."""
            box = row.bounding_box()
            clip = {"x": max(box["x"] - 4, 0), "y": max(box["y"] - 4, 0),
                    "width": box["width"] + 8, "height": box["height"] + 8}
            page.evaluate("() => document.activeElement.blur()")
            before = page.screenshot(clip=clip)
            row.focus()
            after = page.screenshot(clip=clip)
            return before != after

        page.keyboard.press("Tab")
        first = focused()
        assert first["role"] == "button" and first["label"].startswith("Show detail for "), first
        assert first["expanded"] == "false", first
        first_row = page.get_by_role("button", name=first["label"], exact=True)
        assert ring_is_painted(first_row), "no visible focus ring on the focused grid row"

        # Enter opens the focused row's panel; Enter again closes it.
        page.keyboard.press("Enter")
        assert focused()["expanded"] == "true"
        expect(_panel(page).get_by_role("heading", name=first["label"].replace("Show detail for ", ""), exact=True)).to_be_visible()
        page.keyboard.press("Enter")
        assert focused()["expanded"] == "false"
        expect(_content(page).locator("h2")).to_be_hidden()

        # Tab along the rows to the domain with nothing assessed; Space opens it.
        target = "Show detail for " + domains["none"]
        visited = [first["label"]]
        for _ in range(12):
            if visited[-1] == target:
                break
            page.keyboard.press("Tab")
            visited.append(focused()["label"])
        assert visited[-1] == target, visited
        page.keyboard.press("Space")
        state = focused()
        assert state["expanded"] == "true", state
        panel = _panel(page)
        expect(panel.get_by_role("heading", name=domains["none"], exact=True)).to_be_visible()
        assert sorted(_panel_column(panel, "Not assessed")) == sorted([names["none_1"], names["none_2"]])
        assert EM_DASH in _panel_figure(panel, "Avg Maturity")[0]
        assert "%" not in panel.inner_text()

        # Space again toggles it shut; then the no-domain row, last in the tab order.
        page.keyboard.press("Space")
        assert focused()["expanded"] == "false"
        for _ in range(12):
            if visited[-1] == "Show detail for No domain":
                break
            page.keyboard.press("Tab")
            visited.append(focused()["label"])
        assert visited[-1] == "Show detail for No domain", visited
        page.keyboard.press("Enter")
        panel = _panel(page)
        expect(panel.get_by_role("heading", name="No domain", exact=True)).to_be_visible()
        # The panel is reused: its heading can switch a moment before the figures
        # re-render from the previous domain's, so wait for this row's figure.
        expect(panel.get_by_text("Avg Maturity", exact=True).locator("..")).to_contain_text("2 (1 measured)")
        assert _panel_figure(panel, "Avg Maturity")[0] == "2 (1 measured)"
        assert _panel_column(panel, "Not assessed") == [names["nodomain_unassessed"]]

        # The Close button is next in the tab order after the rows and works by keyboard.
        for _ in range(3):
            page.keyboard.press("Tab")
            if page.evaluate("() => document.activeElement.getAttribute('aria-label')") == "Close detail panel":
                break
        assert page.evaluate("() => document.activeElement.getAttribute('aria-label')") == "Close detail panel"
        page.keyboard.press("Enter")
        expect(_content(page).locator("h2")).to_be_hidden()
        _assert_nothing_invented(page)
    finally:
        context.close()


@pytest.mark.parametrize("width,theme", [
    (DESKTOP["width"], "light"), (DESKTOP["width"], "dark"),
    (NARROW["width"], "light"), (NARROW["width"], "dark"),
])
def test_absence_markers_are_legible_in_both_themes_at_both_widths(browser, live_server, heat_tenants, width, theme):
    """The Not-assessed count, the Health marker and the investment marker
    keep AA text contrast in light and dark, the grid scrolls inside its own
    container at a narrow width rather than pushing the page sideways, and the
    detail panel still states its denominators."""
    domains = heat_tenants["domains"]
    viewport = DESKTOP if width == DESKTOP["width"] else NARROW
    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    try:
        _open_page(page, live_server, heat_tenants["emails"]["mixed"])
        light_bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        if theme == "dark":
            page.evaluate("() => document.documentElement.classList.add('dark')")
            page.wait_for_timeout(200)
            assert page.evaluate("() => getComputedStyle(document.body).backgroundColor") != light_bg, \
                "dark theme did not change the page background"
        _switch_view(page, "investment")

        expect(page.get_by_role("columnheader", name="Not assessed", exact=True)).to_be_visible()
        row_none = _row(page, domains["none"])
        row_none.scroll_into_view_if_needed()
        not_assessed_pill = row_none.locator("td").nth(COLUMNS.index("Not assessed")).locator(":scope > span").first
        expect(not_assessed_pill).to_have_text("2")
        health_marker = _marker(_health_cell(row_none))
        investment_marker = _marker(_investment_cell(_row(page, "No domain")))

        for label, element in (("Not assessed count", not_assessed_pill),
                               ("Health marker", health_marker),
                               ("Investment marker", investment_marker)):
            ratio = element.evaluate(CONTRAST_JS)
            print("[smoke] %s contrast at %dpx %s: %s" % (label, width, theme, ratio))
            assert ratio >= 4.5, "%s contrast %s:1 in %s theme at %dpx (AA needs 4.5)" % (label, ratio, theme, width)

        # The marker is a glyph plus text, never a colour alone.
        assert EM_DASH in health_marker.text_content()
        assert health_marker.locator(".sr-only").count() == 1

        # No sideways page scroll: the table scrolls within its own container.
        overflow = page.evaluate(
            "() => ({ page: document.documentElement.scrollWidth - window.innerWidth,"
            " table: (() => { const w = document.querySelector('.overflow-x-auto');"
            " return w ? w.scrollWidth - w.clientWidth : 0; })() })")
        assert overflow["page"] <= 1, "page overflows horizontally by %spx at %dpx" % (overflow["page"], width)
        if width == NARROW["width"]:
            assert overflow["table"] > 0, "expected the grid to scroll inside its container at %dpx" % width

        panel = _open_panel(page, domains["some"])
        assert _panel_figure(panel, "Avg Maturity")[0] == "3 (1 measured)"
        assert _panel_figure(panel, "Avg Target")[0] == "4 (1 measured)"
        ratio = panel.locator("p", has_text="(1 measured)").first.evaluate(CONTRAST_JS)
        assert ratio >= 4.5, "denominator text contrast %s:1 in %s theme" % (ratio, theme)
        _assert_nothing_invented(page)
        _screenshot(page, "mixed-investment-%d-%s" % (width, theme))
    finally:
        context.close()
