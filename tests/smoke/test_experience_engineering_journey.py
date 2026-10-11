"""One renderer, signature screens in the sidebar, and a table for every chart: in a real browser.

Three journeys, each driven as the persona it is for, clicking the real controls:

* An ARB member opens the Twin map from their own sidebar, picks an element, and
  reads the map the Composer's renderer draws: typed ArchiMate arrows, the owner
  under each element (the centre's included), one hop's evidence in the drawer,
  and the same map after a reload.
* An ARB member opens the review-status chart's table from the keyboard alone,
  walks it with the arrow keys, returns with Escape, and the table holds exactly
  the chart's numbers. A second organisation's member sees only its own numbers.
* The same table passes the audit's axe tag set with the table open.
"""

import re
import uuid

import pytest

from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .intelligence_graph import seed_impact_graph
from .test_accessibility_audit import TAGS

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


# --- tenants ------------------------------------------------------------------


@pytest.fixture(scope="module")
def graph(seeded, live_server):
    return seed_impact_graph(seeded["ids"]["org"], "Tollgate")


def _add_reviews(org_id, statuses):
    """ARB review items in one organisation, one per status given."""
    from app import create_app, db
    from app.models.architecture_review_board import ARBReviewItem
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        submitter = User.query.filter_by(organization_id=org_id).order_by(User.id).first()
        for status in statuses:
            db.session.add(ARBReviewItem(
                organization_id=org_id,
                submitter_id=submitter.id,
                review_number="XP-%s" % uuid.uuid4().hex[:12],
                title="Table view review %s" % status,
                review_type="architecture_change",
                status=status,
            ))
        db.session.commit()


@pytest.fixture(scope="module")
def other_org(live_server):
    """A second organisation with one ARB member and one rejected review."""
    import datetime

    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Other Org %s" % suffix, slug="other-%s" % suffix)
        db.session.add(org)
        db.session.commit()
        user = User(
            email="other.arb.%s@example.com" % suffix, first_name="Other", last_name="Member",
            organization_id=org.id, enterprise_role="arb_member", confirmed=True,
            onboarding_completed_at=datetime.datetime.utcnow(),
        )
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        out = {"org": org.id, "email": user.email}
    _add_reviews(out["org"], ["rejected"])
    return out


# --- driving the page -----------------------------------------------------------


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _dismiss_first_run(page):
    try:
        page.eval_on_selector_all("[x-show='showOnboarding']", "els => els.forEach(e => e.remove())")
    except Exception:
        pass


def _wait_for_map(page):
    page.wait_for_selector("svg .intel-edge", state="attached")
    page.wait_for_selector("[data-graph-nodes] button", state="visible")


def _node_text(page, element_id):
    """The words drawn for one element, line by line, as a person reads them."""
    text = page.locator('svg [data-element="%s"]' % element_id).evaluate(
        "el => [...el.querySelectorAll('text')].map(t => { const lines = t.querySelectorAll('tspan');"
        " return lines.length ? [...lines].map(l => l.textContent).join(' ') : t.textContent; }).join(' ')")
    return " ".join(text.replace("\xa0", " ").split())


# --- the Twin map, from the ARB member's sidebar -------------------------------


def test_arb_member_opens_the_twin_map_from_the_sidebar_and_reads_typed_arrows_owners_and_evidence(
    page, live_server, seeded, graph
):
    names = graph["names"]
    _login(page, live_server, seeded["emails"]["arb_member"])
    page.goto(live_server + "/arb/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _dismiss_first_run(page)

    # One click from the persona's own sidebar.
    page.get_by_test_id("sidebar").get_by_role("link", name="Twin map", exact=True).click()
    page.wait_for_url(re.compile(r"/intelligence/twin-map$"))

    picker = page.locator("#twin-picker-input")
    picker.press_sequentially(graph["noun"], delay=15)
    page.locator("#twin-picker-listbox [role=option]", has_text=names["service"]).click()
    _wait_for_map(page)
    page.wait_for_url(re.compile(r"element=%s$" % graph["service"]))

    # Drawn by the Composer's renderer: ArchiMate shapes as renderer cells.
    assert page.locator("[data-graph-svg].joint-paper").count() == 1
    assert page.locator('svg g.joint-element[data-element="%s"]' % graph["gateway"]).count() == 1

    # Typed arrows: each explicit connection is drawn as its ArchiMate type,
    # with the type written on it and the type's arrowhead at its end.
    explicit = page.locator("svg g.intel-edge[data-kind=explicit]")
    assert explicit.count() == 3
    for index in range(explicit.count()):
        edge = explicit.nth(index)
        assert "Serving" in edge.text_content()
        assert (edge.locator("path[data-kind=explicit]").get_attribute("marker-end") or "").startswith("url(#")
    derived = page.locator("svg g.intel-edge[data-kind=derived]")
    assert derived.count() == 1
    assert derived.locator("g.intel-badge text").text_content() == "Worked out"

    # Owners: written under every element, or plainly absent.
    assert "Owner: %s" % graph["owner"] in _node_text(page, graph["gateway"])
    assert "No owner recorded" in _node_text(page, graph["portal"])

    # Select the gateway on the map; the side panel follows.
    page.locator('[data-graph-nodes] button[data-node="%s"]' % graph["gateway"]).click()
    expect(page.locator('button[data-node="%s"]' % graph["gateway"])).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#twin-rail").get_by_role("heading", name=names["gateway"], exact=True)).to_be_visible()

    # One hop's evidence: the drawer for the gateway connection.
    row = page.locator("[data-map-row][data-kind=explicit]").filter(
        has=page.get_by_role("cell", name=names["gateway"], exact=True)).first
    row.get_by_role("button", name="Why?").click()
    dialog = page.locator("#drawer-provenance [role=dialog]")
    dialog.wait_for(state="visible")
    assert names["gateway"] in page.locator("#drawer-title-provenance").inner_text()
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")

    # Re-centre on the gateway: the centre's own owner is written under it.
    row.get_by_role("button", name="Centre on this").click()
    page.wait_for_url(re.compile(r"element=%s$" % graph["gateway"]))
    page.wait_for_function(
        "(id) => { const g = document.querySelector('svg [data-element=\"' + id + '\"]');"
        " return !!g && g.textContent.indexOf('Owner:') !== -1; }",
        arg=str(graph["gateway"]))
    assert "Owner: %s" % graph["owner"] in _node_text(page, graph["gateway"])

    # Reload: the same map comes back, drawn the same way.
    page.reload(wait_until="domcontentloaded")
    _wait_for_map(page)
    assert page.url.endswith("element=%s" % graph["gateway"])
    assert "Owner: %s" % graph["owner"] in _node_text(page, graph["gateway"])
    assert page.locator("svg g.intel-edge[data-kind=explicit]").count() >= 1


# --- a chart's table, from the keyboard -----------------------------------------


def _chart_rows(page, canvas_id):
    return page.evaluate(
        "(id) => { const c = Chart.getChart(document.getElementById(id));"
        " return c.data.labels.map((l, i) => [String(l), String(c.data.datasets[0].data[i])]); }",
        canvas_id)


def _table_rows(page):
    return page.eval_on_selector_all(
        "[data-graph-table-view][data-for=arbStatusChart] tbody tr",
        "rows => rows.map(r => [...r.cells].map(c => c.textContent.trim()))")


def _open_table_by_keyboard(page):
    toggle = page.locator("[data-graph-table-view][data-for=arbStatusChart] [data-graph-table-toggle]")
    toggle.focus()
    expect(toggle).to_be_focused()
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    page.wait_for_selector("[data-graph-table-view][data-for=arbStatusChart] table[data-graph-table]")
    return toggle


def _open_arb_dashboard(page, base):
    page.goto(base + "/arb/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _dismiss_first_run(page)
    page.wait_for_function(
        "() => window.Chart && Chart.getChart(document.getElementById('arbStatusChart'))")


def test_a_chart_table_holds_the_charts_rows_and_is_walked_by_keyboard_in_each_organisation(
    page, live_server, seeded, other_org
):
    _add_reviews(seeded["ids"]["org"], ["submitted", "under_review", "approved"])

    _login(page, live_server, seeded["emails"]["arb_member"])
    _open_arb_dashboard(page, live_server)
    toggle = _open_table_by_keyboard(page)

    rows = _table_rows(page)
    assert rows == _chart_rows(page, "arbStatusChart")
    labels = [r[0] for r in rows]
    assert labels == ["Pending / In Review", "Approved", "Rejected", "Other"]
    counts = {r[0]: int(r[1]) for r in rows}
    assert counts["Pending / In Review"] >= 2 and counts["Approved"] >= 1

    # The keyboard walks the table: one tab stop, arrows between cells, Escape out.
    table = page.locator("[data-graph-table-view][data-for=arbStatusChart] table")
    page.keyboard.press("Tab")
    first = table.locator("thead th").first
    expect(first).to_be_focused()
    page.keyboard.press("ArrowDown")
    expect(table.locator("tbody tr").nth(0).locator("th")).to_be_focused()
    page.keyboard.press("ArrowRight")
    expect(table.locator("tbody tr").nth(0).locator("td")).to_be_focused()
    page.keyboard.press("Control+End")
    expect(table.locator("tbody tr").last.locator("td")).to_be_focused()
    page.keyboard.press("Home")
    expect(table.locator("tbody tr").last.locator("th")).to_be_focused()
    assert table.locator("[tabindex='0']").count() == 1
    page.keyboard.press("Escape")
    expect(toggle).to_be_focused()

    # Closing and reopening reads the chart again and gives the same rows.
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    page.keyboard.press("Enter")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    assert _table_rows(page) == rows

    # The other organisation's member sees only its own reviews.
    page.context.clear_cookies()
    _login(page, live_server, other_org["email"])
    _open_arb_dashboard(page, live_server)
    _open_table_by_keyboard(page)
    assert _table_rows(page) == [
        ["Pending / In Review", "0"], ["Approved", "0"], ["Rejected", "1"], ["Other", "0"],
    ]


def test_a_chart_table_passes_the_audit_tag_set_with_the_table_open(page, live_server, seeded):
    from axe_playwright_python import sync_playwright as axe_module

    _add_reviews(seeded["ids"]["org"], ["submitted"])
    _login(page, live_server, seeded["emails"]["arb_member"])
    _open_arb_dashboard(page, live_server)
    _open_table_by_keyboard(page)

    axe = axe_module.Axe()
    report = axe.run(page, context="[data-graph-table-view]",
                     options={"runOnly": {"type": "tag", "values": TAGS}})
    data = report.response if hasattr(report, "response") else report
    violations = [(v["id"], v.get("impact")) for v in data.get("violations", [])]
    assert violations == [], violations
