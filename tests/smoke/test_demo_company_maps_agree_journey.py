"""The demonstration organisation's two capability screens agree.

Signed in as the Lantern Quay Systems demo user, the architect opens the
Capability Map and reads how many capabilities are mapped to applications, then
opens the Investment Map and finds the same number of capabilities supported by
applications -- not the "map your first application" page. A second organisation
that has its own mappings never changes either number.

The architect then opens a mapped capability on the Capability Map (its side
panel and its mapping dialog) and finds the same applications the row and the
Investment Map name, and follows every capability and application link on the
Investment Map to a page with content. All of it after a single seed.
"""

import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"


@pytest.fixture(scope="module")
def demo_org(seeded, live_server):
    """Lantern Quay seeded by its own command, beside another organisation that
    has one mapping of its own."""
    from app import create_app, db
    from app.commands.seed_demo_company import seed_demo_company
    from app.jobs.tenant_safe_job import tenant_scope
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.organization import Organization
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )
    from app.commands.seed_demo_mappings import _unified_capabilities_by_source
    from app.commands.seed_demo_mappings import _project_missing_unified_rows

    app = create_app("testing")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            seed_demo_company()
            demo_id = db.session.query(Organization.id).filter_by(slug="lantern-quay").scalar()
            from app.models.user import User

            demo_user = User.query.filter_by(email=DEMO_EMAIL, organization_id=demo_id).one()
            demo_user.password = PASSWORD  # a database seeded earlier keeps its old password
            db.session.commit()
            other_id = seeded["ids"]["org"]
            with tenant_scope(other_id):
                other_app = ApplicationComponent(name="Other Org Billing Hub", organization_id=other_id)
                other_cap = BusinessCapability(name="Other Org Only Capability", organization_id=other_id)
                db.session.add_all([other_app, other_cap])
                db.session.commit()
                _project_missing_unified_rows([other_cap])
                unified = _unified_capabilities_by_source(other_id, [other_cap.id])[other_cap.id]
                db.session.add(UnifiedApplicationCapabilityMapping(
                    application_component_id=other_app.id,
                    unified_capability_id=unified.id,
                    support_level="partial", coverage_percentage=50,
                    relationship_type="enables",
                ))
                db.session.commit()
            return {"demo_id": demo_id}
    finally:
        os.environ.pop("DEMO_USER_PASSWORD", None)


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
    page.click("#submit", no_wait_after=True)
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _capability_map_mapped(page, base):
    page.goto(base + "/capability-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    total = page.locator("#app-cap-count")
    expect(total).not_to_have_text("0", timeout=PAGE_TIMEOUT)
    return int(page.locator("#app-mapped-count").inner_text())


def _investment_map_mapped(page, base):
    page.goto(base + "/architecture/investment-priorities",
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("Setup Required")).to_have_count(0)
    expect(page.get_by_role("heading", name="Investment Priority Matrix")).to_be_visible()
    cells = page.locator("td", has_text=re.compile(r"^\d+ apps$"))
    expect(cells.first).to_be_visible()
    texts = cells.all_inner_texts()
    return sum(1 for t in texts if int(t.split()[0]) > 0), page.content()


def test_demo_investment_map_agrees_with_capability_map(page, live_server, demo_org):
    _login(page, live_server, DEMO_EMAIL)
    on_capability_map = _capability_map_mapped(page, live_server)
    assert on_capability_map > 0
    on_investment_map, html = _investment_map_mapped(page, live_server)
    assert on_investment_map == on_capability_map
    assert "Other Org Only Capability" not in html


def test_another_organisations_mappings_never_count(app, demo_org):
    """Counted in the data, with the other organisation's mapping present."""
    from flask import g

    from app import db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping as M,
    )

    with app.app_context():
        def count(org_id):
            return (
                db.session.query(M)
                .join(ApplicationComponent, M.application_component_id == ApplicationComponent.id)
                .filter(ApplicationComponent.organization_id == org_id)
                .count()
            )

        demo = count(demo_org["demo_id"])
        assert demo == 20, "each of the 20 demo applications carries one unified mapping"
        total = db.session.query(M).count()
        assert total > demo, "another organisation's mapping exists and is excluded"


# ---- the capability panel and dialog read the same mapping as the row -------------

MAPPED_CAPABILITIES = ("Order Fulfilment", "Sensor Calibration", "Incident Response")


def _row_application_count(row):
    """How many applications the Capability Map row names: "A", or "A +N more"."""
    cell = row.locator("td[data-open-mapping]").inner_text()
    more = re.search(r"\+(\d+) more", cell)
    return 1 + int(more.group(1)) if more else 1


def _investment_row(page, name):
    link = page.get_by_test_id("investment-capability-link").filter(
        has_text=re.compile("^%s$" % re.escape(name)))
    return page.locator("tr").filter(has=link)


def _investment_apps(page, base, name):
    """The "N apps" the Investment Map shows against one capability."""
    page.goto(base + "/architecture/investment-priorities",
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.locator("table.dense-table tbody tr").filter(has_text=name)
    expect(row).to_have_count(1, timeout=PAGE_TIMEOUT)
    cell = row.locator("td", has_text=re.compile(r"^\s*\d+ apps\s*$"))
    return int(cell.inner_text().split()[0])


def _open_capability_row(page, base, name):
    page.goto(base + "/capability-map", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("#app-cap-count")).not_to_have_text("0", timeout=PAGE_TIMEOUT)
    page.fill("#application-search-filter", name)
    row = page.locator("tr[data-cap-name='%s']:visible" % name)
    expect(row).to_have_count(1)
    return row


@pytest.mark.parametrize("name", MAPPED_CAPABILITIES)
def test_capability_panel_and_dialog_agree_with_the_row_and_the_investment_map(
        page, live_server, demo_org, name):
    _login(page, live_server, DEMO_EMAIL)
    on_row = _row_application_count(_open_capability_row(page, live_server, name))
    assert on_row > 0, "the demonstration maps %s to at least one application" % name
    assert _investment_apps(page, live_server, name) == on_row

    row = _open_capability_row(page, live_server, name)
    # Actions > View Details: the panel says the same number and lists the applications.
    row.locator("[data-cap-actions-toggle]").click()
    row.locator("[data-action='view-detail']").click()
    expect(page.get_by_text("%d of 20 apps mapped" % on_row, exact=True).first).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("Linked Applications (%d)" % on_row, exact=True).first).to_be_visible()
    expect(page.get_by_text("No applications mapped to this capability.")).to_have_count(0)
    page.keyboard.press("Escape")

    # The application cell: the mapping dialog opens with the same applications ticked.
    row.locator("td[data-open-mapping]").click()
    expect(page.get_by_text("%d selected" % on_row, exact=True).first).to_be_visible(timeout=PAGE_TIMEOUT)


# ---- the Investment Map's links, defaults and console ----------------------------

def test_investment_map_links_land_on_content_and_the_page_is_clean(page, live_server, demo_org):
    problems = []
    page.on("console", lambda m: problems.append("console: " + m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append("page error: %s" % e))
    _login(page, live_server, DEMO_EMAIL)
    on_investment_map, _ = _investment_map_mapped(page, live_server)
    assert on_investment_map > 0
    assert problems == [], problems

    # The table fits the page: the Timeframe heading is whole and nothing needs a sideways scroll.
    fits = page.evaluate(
        "() => { const t = document.querySelector('table.dense-table');"
        " return t.scrollWidth <= t.parentElement.clientWidth + 1; }")
    assert fits, "the matrix is wider than its card at 1440"
    heading = page.locator("table.dense-table").first.locator("th", has_text="Timeframe")
    expect(heading).to_be_visible()
    box = heading.bounding_box()
    card = page.locator("table.dense-table").first.locator("xpath=..").bounding_box()
    assert box["x"] + box["width"] <= card["x"] + card["width"] + 1

    # Domain reads as the Capability Map reads it.
    row = _open_capability_row(page, live_server, "Order Fulfilment")
    on_capability_map = row.locator("td").nth(2).inner_text().strip()
    page.goto(live_server + "/architecture/investment-priorities",
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    inv_row = _investment_row(page, "Order Fulfilment")
    expect(inv_row).to_contain_text(on_capability_map)
    assert "Unknown" not in inv_row.inner_text()

    # Scores that rest on a default say so, with the next step.
    expect(inv_row.get_by_test_id("strategic-default-note")).to_contain_text("Default: not set")
    expect(inv_row.get_by_test_id("risk-default-note")).to_contain_text("No risk signals")

    # The capability name opens the capability's own page, with its applications.
    inv_row.get_by_test_id("investment-capability-link").click()
    page.wait_for_url(re.compile(r"/enterprise/capabilities/\d+/fact-sheet$"), timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("Order Fulfilment").first).to_be_visible()
    apps = page.locator("#applications a")
    expect(apps).to_have_count(2)
    assert "Dispatch Planner" in [t.strip() for t in apps.all_inner_texts()]

    # The "2 apps" cell lands on that section, and each application opens its own page.
    page.go_back(wait_until="domcontentloaded")
    _investment_row(page, "Order Fulfilment").get_by_test_id("investment-apps-link").click()
    page.wait_for_url(re.compile(r"/fact-sheet#applications$"), timeout=PAGE_TIMEOUT)
    expect(page.locator("#applications")).to_contain_text("Dispatch Planner")
    page.locator("#applications a").filter(has_text="Dispatch Planner").click()
    page.wait_for_url(lambda url: "fact-sheet" in url and "#applications" not in url, timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("Dispatch Planner").first).to_be_visible()

    # Compare Scenarios lists the capabilities by name, and nothing raised an error on the way.
    page.goto(live_server + "/architecture/investment-priorities",
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Compare Scenarios").click()
    expect(page.locator("select option", has_text="Order Fulfilment").first).to_be_attached(timeout=PAGE_TIMEOUT)
    assert problems == [], problems


# ---- one seed, either order ---------------------------------------------------------

def test_demo_seed_projects_capabilities_without_the_provenance_migration(app, demo_org):
    """A database upgraded but not given the provenance index is seeded in one run."""
    from sqlalchemy import text

    from app import db
    from app.commands.project_capabilities import PROVENANCE_INDEX, _has_provenance_index
    from app.commands.seed_demo_mappings import _project_missing_unified_rows
    from app.jobs.tenant_safe_job import tenant_scope
    from app.models.business_capabilities import BusinessCapability
    from app.models.organization import Organization
    from app.models.unified_capability import UnifiedCapability

    with app.app_context():
        tag = os.urandom(3).hex()
        org = Organization(name="Order Test Org %s" % tag, slug="order-test-%s" % tag)
        db.session.add(org)
        db.session.commit()
        org_id = org.id
        from app.models.business_capabilities import _provenance_index_cache

        db.session.execute(text("DROP INDEX IF EXISTS %s" % PROVENANCE_INDEX))
        db.session.commit()
        _provenance_index_cache.clear()  # this process remembered the index
        assert not _has_provenance_index(db.session.connection())
        try:
            with tenant_scope(org_id):
                cap = BusinessCapability(name="Order Test Capability", organization_id=org_id)
                db.session.add(cap)
                db.session.commit()
                _project_missing_unified_rows([cap])
                db.session.commit()
                projected = UnifiedCapability.query.filter_by(
                    source_table="business_capability", source_id=str(cap.id)).count()
            assert projected == 1, "the seed must project without a second step"
        finally:
            db.session.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS %s ON unified_capabilities (source_table, source_id) "
                "WHERE source_table IS NOT NULL AND source_id IS NOT NULL" % PROVENANCE_INDEX))
            db.session.commit()
            _provenance_index_cache.clear()
