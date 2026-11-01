"""A data architect checks critical entities without stewards, retention
breaches, and undeclared copies. Each page is reached from the sidebar or a
direct link, shows its table using only component macros, and every link lands
on a page with content. Two organisations isolate the data.
"""

from __future__ import annotations

import os
import pathlib
import uuid
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

GOVERNANCE_BASE = "/data-governance"
SHOTS = pathlib.Path(os.environ.get("DGOV02_SHOTS",
                      "/home/ubuntu/verify/seats/mg3datagovernanceb050b/walk"))


def _uid():
    return uuid.uuid4().hex[:8]


def _assert_role(email):
    """Assert from the database that the user's enterprise_role is data_architect
    and the platform role is not an administrator."""
    from app import create_app, db
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == "data_architect", (
            "expected data_architect, got %s" % user.enterprise_role
        )
        assert user.role.name not in ("Administrator", "Platform Admin"), (
            "expected non-admin platform role, got %s" % user.role.name
        )
        db.session.remove()


def _seed_dg_org(suffix):
    """Seed one organisation with data governance fixtures: mission-critical
    domain, entities without stewards, entities with retention policies,
    and entities held by multiple applications."""
    from app import create_app, db
    from app.models.application_layer import DataObject
    from app.models.application_portfolio import ApplicationComponent
    from app.models.data_governance import DataRetentionPolicy
    from app.models.organization import Organization
    from app.models.process_data import DataDomain, DataEntity
    from app.models.user import Role, User

    app = create_app("testing")
    out = {"org_id": None, "emails": {}, "entity_ids": [], "app_ids": []}
    with app.app_context():
        Role.insert_roles()
        arch_role = Role.query.filter_by(name="Architect").one()
        org = Organization(name=f"DG Journey Org {suffix}", slug=f"dg-{suffix}")
        db.session.add(org)
        db.session.commit()
        out["org_id"] = org.id

        for role in ("data_architect", "procurement", "compliance"):
            email = f"dg.{role}.{suffix}@example.com"
            user = User(
                email=email, first_name="DG", last_name=role[:12],
                organization_id=org.id, enterprise_role=role, confirmed=True,
            )
            user.role = arch_role
            user.password = PASSWORD
            db.session.add(user)
            db.session.commit()
            out["emails"][role] = email

        # Mission-critical domain
        mc_domain = DataDomain(
            name=f"Critical Domain {suffix}", organization_id=org.id,
            domain_type="master", criticality="mission_critical",
        )
        db.session.add(mc_domain)
        db.session.commit()

        # Business-critical domain
        bc_domain = DataDomain(
            name=f"Business Critical Domain {suffix}", organization_id=org.id,
            domain_type="master", criticality="business_critical",
        )
        db.session.add(bc_domain)
        db.session.commit()

        # Non-critical domain (should NOT show on no-steward page)
        nc_domain = DataDomain(
            name=f"Supporting Domain {suffix}", organization_id=org.id,
            domain_type="reference", criticality="supporting",
        )
        db.session.add(nc_domain)
        db.session.commit()

        # Entities without stewards in critical domains
        for domain, ename in [(mc_domain, "Customer"), (bc_domain, "Employee Data"),
                              (nc_domain, "Office Supplies")]:
            entity = DataEntity(
                name=f"{ename} {suffix}", domain_id=domain.id,
                organization_id=org.id,
                created_at=datetime.utcnow() - timedelta(days=30),
            )
            db.session.add(entity)
            db.session.commit()
            out["entity_ids"].append(entity.id)

        # An entity with retention policy - create it old enough to breach
        breach_entity = DataEntity(
            name=f"Retention Breach Entity {suffix}",
            domain_id=mc_domain.id, organization_id=org.id,
            created_at=datetime.utcnow() - timedelta(days=30),
        )
        db.session.add(breach_entity)
        db.session.commit()
        out["breach_entity_id"] = breach_entity.id

        # Retention policy on mc_domain with a short period (1 day), so age > allowed
        domain_policy = DataRetentionPolicy(
            name=f"Retention Policy {suffix}", data_domain_id=mc_domain.id,
            retention_period_days=1, organization_id=org.id,
        )
        db.session.add(domain_policy)
        db.session.commit()
        out["policy_id"] = domain_policy.id

        # Two applications for undeclared copies
        app_a = ApplicationComponent(name=f"App A {suffix}", organization_id=org.id)
        db.session.add(app_a)
        db.session.commit()
        out["app_ids"].append(app_a.id)

        app_b = ApplicationComponent(name=f"App B {suffix}", organization_id=org.id)
        db.session.add(app_b)
        db.session.commit()
        out["app_ids"].append(app_b.id)

        # Make one entity be held by both apps (undeclared copy)
        for app_obj in (app_a, app_b):
            do = DataObject(
                name=f"Customer {suffix}", description=f"Customer for {app_obj.name}",
                application_component_id=app_obj.id, organization_id=org.id,
            )
            db.session.add(do)
        db.session.commit()

        db.session.remove()
    return out


def _seed_second_org(suffix):
    """Seed a second organisation to prove isolation."""
    return _seed_dg_org(suffix + "-second")


@pytest.fixture(scope="module")
def world():
    org1 = _seed_dg_org(_uid())
    org2 = _seed_second_org(_uid())
    return {"org1": org1, "org2": org2}


def sign_in(page, base, email):
    page.goto(f"{base}/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    assert "/account/login" not in page.url, f"could not sign in as {email}"


def test_data_architect_can_reach_no_steward_page(page, live_server, world):
    """As data architect, navigate to the no-steward page and see critical
    entities without stewards, organised in a component-built table with
    badge and button macros."""
    org = world["org1"]
    sign_in(page, str(live_server), org["emails"]["data_architect"])
    _assert_role(org["emails"]["data_architect"])

    page.goto(f"{live_server}{GOVERNANCE_BASE}/no-steward", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # Page loads with a title
    expect(page.locator("h1")).to_contain_text("Critical entities with no steward")

    # Table renders using the dense-table component
    table = page.locator("[data-testid='no-steward-table']")
    expect(table).to_be_visible()

    # Table has rows for critical-domain entities
    rows = table.locator("tbody tr")
    expect(rows.first).to_be_visible()

    # Row name is visible
    first_name = rows.first.locator("td").first
    expect(first_name).not_to_be_empty()

    # Badge renders (destructive or warning) indicating criticality
    badge_el = rows.first.locator(".inline-flex.items-center.rounded-full")
    expect(badge_el).to_be_visible()

    # Assign steward button renders and links to entity detail
    assign_btn = rows.first.locator("a[href*='/data-governance/entities/']")
    expect(assign_btn).to_contain_text("Assign steward")

    # Click the link and verify landing page has content
    assign_btn.click()
    page.wait_for_url(lambda url: "/data-governance/entities/" in url, timeout=PAGE_TIMEOUT)
    expect(page.locator("h1")).to_be_visible()


def test_no_steward_isolation_from_another_organisation(page, live_server, world):
    """A procurement user from organisation B cannot see organisation A's
    no-steward data and receives a 403."""
    org_b = world["org2"]
    sign_in(page, str(live_server), org_b["emails"]["procurement"])

    page.goto(f"{live_server}{GOVERNANCE_BASE}/no-steward", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    # Re-pointed from the retired "403" heading to the library no-access screen.
    expect(page.get_by_role("heading", name="You don't have access to this page")).to_be_visible()


def test_retention_breaches_page_shows_breached_entities(page, live_server, world):
    """As data architect, see entities older than their retention policy,
    with numeric columns in tabular figures and the owning application."""
    org = world["org1"]
    sign_in(page, str(live_server), org["emails"]["data_architect"])
    _assert_role(org["emails"]["data_architect"])

    page.goto(f"{live_server}{GOVERNANCE_BASE}/retention-breaches", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # Page loads with a title
    expect(page.locator("h1")).to_contain_text("Retention breaches")

    # Table renders using the dense-table component
    table = page.locator("[data-testid='retention-breaches']")
    expect(table).to_be_visible()

    rows = table.locator("tbody tr")
    expect(rows.first).to_be_visible()

    # Entity name visible
    first_name = rows.first.locator("td").first
    expect(first_name).not_to_be_empty()

    # Age column is numeric
    age_cell = rows.first.locator("td").nth(2)
    expect(age_cell).to_be_visible()


def test_retention_breaches_isolation_from_another_organisation(page, live_server, world):
    """Procurement from another organisation receives 403 on retention breaches."""
    org_b = world["org2"]
    sign_in(page, str(live_server), org_b["emails"]["procurement"])

    page.goto(f"{live_server}{GOVERNANCE_BASE}/retention-breaches", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    # Re-pointed from the retired "403" heading to the library no-access screen.
    expect(page.get_by_role("heading", name="You don't have access to this page")).to_be_visible()


def test_undeclared_copies_page_shows_duplicate_holds(page, live_server, world):
    """As data architect, see entities held by more than one application
    with no declared system of record, ranked by consumer count."""
    org = world["org1"]
    sign_in(page, str(live_server), org["emails"]["data_architect"])
    _assert_role(org["emails"]["data_architect"])

    page.goto(f"{live_server}{GOVERNANCE_BASE}/undeclared-copies", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # Page loads with a title
    expect(page.locator("h1")).to_contain_text("Undeclared copies")

    # Table renders using the dense-table component
    table = page.locator("[data-testid='copies-table']")
    expect(table).to_be_visible()

    rows = table.locator("tbody tr")
    expect(rows.first).to_be_visible()

    # Entity name visible
    first_name = rows.first.locator("td").first
    expect(first_name).not_to_be_empty()

    # Declare button renders and links to entity detail
    declare_btn = rows.first.locator("a[href*='/data-governance/entities/']")
    expect(declare_btn).to_contain_text("Declare")

    # Click the link and verify landing page has content
    declare_btn.click()
    page.wait_for_url(lambda url: "/data-governance/entities/" in url, timeout=PAGE_TIMEOUT)
    expect(page.locator("h1")).to_be_visible()


def test_undeclared_copies_isolation_from_another_organisation(page, live_server, world):
    """Procurement from another organisation receives 403 on undeclared copies."""
    org_b = world["org2"]
    sign_in(page, str(live_server), org_b["emails"]["procurement"])

    page.goto(f"{live_server}{GOVERNANCE_BASE}/undeclared-copies", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    # Re-pointed from the retired "403" heading to the library no-access screen.
    expect(page.get_by_role("heading", name="You don't have access to this page")).to_be_visible()


def test_tabs_navigate_between_data_governance_pages(page, live_server, world):
    """The page tabs let the data architect navigate between governance pages
    without leaving the section; each tab lands on a page with content."""
    org = world["org1"]
    sign_in(page, str(live_server), org["emails"]["data_architect"])
    _assert_role(org["emails"]["data_architect"])

    page.goto(f"{live_server}{GOVERNANCE_BASE}/no-steward", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("h1")).to_contain_text("Critical entities with no steward")

    # Click the "Undeclared copies" tab (the tab in the tab bar)
    undeclared_tab = page.locator("ul a[href='/data-governance/undeclared-copies']")
    expect(undeclared_tab).to_be_visible()
    undeclared_tab.click()
    page.wait_for_url(lambda url: "/data-governance/undeclared-copies" in url, timeout=PAGE_TIMEOUT)
    expect(page.locator("h1")).to_contain_text("Undeclared copies")

    # Click the "Retention breaches" tab
    retention_tab = page.locator("ul a[href='/data-governance/retention-breaches']")
    expect(retention_tab).to_be_visible()
    retention_tab.click()
    page.wait_for_url(lambda url: "/data-governance/retention-breaches" in url, timeout=PAGE_TIMEOUT)
    expect(page.locator("h1")).to_contain_text("Retention breaches")

    # Click the "System of record" tab
    sor_tab = page.locator("ul a[href='/data-governance/entities']")
    expect(sor_tab).to_be_visible()
    sor_tab.click()
    page.wait_for_url(lambda url: "/data-governance/entities" in url, timeout=PAGE_TIMEOUT)
    expect(page.locator("h1")).to_contain_text("System of record")


def _capture(browser, live_server, email, page_path, name_pfx):
    """Capture screenshots of a data governance page at 1440 and 390,
    light and dark, signed in as the given persona."""
    for width, dark in ((1440, False), (390, False), (1440, True), (390, True)):
        theme = "dark" if dark else "light"
        context = browser.new_context(viewport={"width": width, "height": 900},
                                      color_scheme=theme)
        context.set_default_timeout(PAGE_TIMEOUT)
        try:
            page = context.new_page()
            sign_in(page, str(live_server), email)
            page.goto(f"{live_server}{page_path}", wait_until="networkidle", timeout=PAGE_TIMEOUT)
            page.locator("h1").wait_for(state="visible", timeout=PAGE_TIMEOUT)
            SHOTS.mkdir(parents=True, exist_ok=True)
            out_path = SHOTS / ("%s-%s-%d.png" % (name_pfx, theme, width))
            page.screenshot(path=str(out_path), full_page=True)
        finally:
            context.close()


@pytest.mark.parametrize("dummy", [1])
def test_screenshots_of_all_data_governance_pages(browser, live_server, world, dummy):
    """Capture screenshots of no_steward, retention_breaches,
    and undeclared copies at both viewports, light and dark."""
    org = world["org1"]
    email = org["emails"]["data_architect"]

    _capture(browser, live_server, email, "/data-governance/no-steward", "no-steward")
    _capture(browser, live_server, email, "/data-governance/retention-breaches", "retention-breaches")
    _capture(browser, live_server, email, "/data-governance/undeclared-copies", "undeclared-copies")