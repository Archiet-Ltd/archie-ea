"""The demonstration organisation's two capability screens agree.

Signed in as the Lantern Quay Systems demo user, the architect opens the
Capability Map and reads how many capabilities are mapped to applications, then
opens the Investment Map and finds the same number of capabilities supported by
applications -- not the "map your first application" page. A second organisation
that has its own mappings never changes either number.
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
