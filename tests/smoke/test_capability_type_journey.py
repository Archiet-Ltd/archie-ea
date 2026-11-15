"""An enterprise architect and a startup CTO classify a capability and see it move.

Goal: "I decide which capabilities differentiate the company, and every place that
groups capabilities shows my decision." The organisation is seeded with linked data
(a capability with an application behind it, one at a deeper level, and a capability
that belongs to a different organisation that must never appear).
"""
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
MAP_URL = "/capability-map/simple"
INVESTMENT_URL = "/architecture/investment-priorities"


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _role_of(email):
    """The signed-in person's role, read from the data: never an administrator."""
    from app import create_app, db
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        return user.enterprise_role, bool(user.is_admin()), bool(user.is_platform_admin)


@pytest.fixture(scope="module")
def typed_org(seeded):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import (
        ApplicationCapabilityCoverage,
        BusinessCapability,
    )
    from app.models.organization import Organization
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )
    from app.models.unified_capability import UnifiedCapability

    suffix = uuid.uuid4().hex[:6]
    org_id = seeded["ids"]["org"]
    names = {
        "top": "Sell online %s" % suffix,
        "deep": "Pick items %s" % suffix,
        "foreign": "Other org capability %s" % suffix,
        "app": "Storefront %s" % suffix,
    }
    app = create_app("testing")
    with app.app_context():
        other = Organization(name="Other %s" % suffix, slug="other-%s" % suffix)
        db.session.add(other)
        db.session.flush()
        top = BusinessCapability(name=names["top"], code="J-T-%s" % suffix,
                                 organization_id=org_id, level=1,
                                 current_maturity_level=2, target_maturity_level=4)
        deep = BusinessCapability(name=names["deep"], code="J-D-%s" % suffix,
                                  organization_id=org_id, level=3)
        foreign = BusinessCapability(name=names["foreign"], code="J-F-%s" % suffix,
                                     organization_id=other.id, level=1)
        storefront = ApplicationComponent(name=names["app"], organization_id=org_id)
        db.session.add_all([top, deep, foreign, storefront])
        db.session.flush()
        db.session.add(ApplicationCapabilityCoverage(
            application_component_id=storefront.id, capability_id=top.id,
            support_level="full"))
        db.session.flush()
        unified = UnifiedCapability.query.filter_by(
            organization_id=org_id, source_table="business_capability",
            source_id=str(top.id)).one()
        db.session.add(UnifiedApplicationCapabilityMapping(
            unified_capability_id=unified.id, application_component_id=storefront.id,
            support_level="full", coverage_percentage=80, is_active=True))
        db.session.commit()
        names["top_id"] = top.id
    return names


def _group_text(page, key):
    return page.locator('[data-testid="type-group-%s"]' % key)


@pytest.mark.smoke
@pytest.mark.journey
def test_enterprise_architect_classifies_a_capability_and_sees_it_move(browser, live_server, seeded, typed_org):
    email = seeded["emails"]["enterprise_architect"]
    role, is_admin, is_platform_admin = _role_of(email)
    assert role == "enterprise_architect" and not is_admin and not is_platform_admin

    page = browser.new_context(ignore_https_errors=True).new_page()
    _login(page, live_server, email)

    # The map: top-level capabilities start strategic, deeper ones supporting.
    page.goto(live_server + MAP_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(_group_text(page, "strategic")).to_contain_text(typed_org["top"])
    expect(_group_text(page, "supporting")).to_contain_text(typed_org["deep"])
    expect(page.locator("body")).not_to_contain_text(typed_org["foreign"])

    # Follow the capability link: it lands on its own fact sheet, with content.
    page.get_by_role("link", name=typed_org["top"]).click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_role("heading", name=typed_org["top"]).first).to_be_visible()
    expect(page.get_by_test_id("capability-type")).to_have_text("Strategic")
    expect(page.locator("body")).to_contain_text(typed_org["app"])
    expect(page.locator("body")).to_contain_text("Differentiates the company")

    # Change it to operational with the real control; the page says so.
    page.get_by_label("Capability type").select_option("operational")
    page.get_by_test_id("save-capability-type").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_test_id("capability-type")).to_have_text("Operational")
    expect(page.locator("body")).to_contain_text("is now operational")
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_test_id("capability-type")).to_have_text("Operational")

    # The map shows it in its new group, and no longer in the old one.
    page.goto(live_server + MAP_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(_group_text(page, "operational")).to_contain_text(typed_org["top"])
    expect(_group_text(page, "strategic")).not_to_contain_text(typed_org["top"])

    # The investment view reads the same decision, and can be filtered by it.
    page.goto(live_server + INVESTMENT_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.locator("tbody tr", has_text=typed_org["top"])
    expect(row).to_have_count(1)
    expect(row.get_by_test_id("capability-type")).to_have_text("operational")
    page.get_by_test_id("type-filter").select_option("strategic")
    expect(page.locator("tbody tr", has_text=typed_org["top"])).to_have_count(0)
    page.get_by_test_id("type-filter").select_option("operational")
    expect(page.locator("tbody tr", has_text=typed_org["top"])).to_have_count(1)


@pytest.mark.smoke
@pytest.mark.journey
def test_startup_cto_classifies_a_capability(browser, live_server, seeded, typed_org):
    email = seeded["emails"]["cto"]
    role, is_admin, is_platform_admin = _role_of(email)
    assert role == "cto" and not is_admin and not is_platform_admin

    page = browser.new_context(ignore_https_errors=True).new_page()
    _login(page, live_server, email)
    page.goto(live_server + "/enterprise/capabilities/%d/fact-sheet" % typed_org["top_id"],
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_label("Capability type").select_option("supporting")
    page.get_by_test_id("save-capability-type").click()
    page.wait_for_load_state("domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    expect(page.get_by_test_id("capability-type")).to_have_text("Supporting")
    expect(page.locator("body")).to_contain_text("Every company needs it")


@pytest.mark.smoke
@pytest.mark.journey
def test_a_reader_sees_the_type_but_has_no_control_to_change_it(browser, live_server, seeded, typed_org):
    email = seeded["emails"]["procurement"]
    role, is_admin, _ = _role_of(email)
    assert role == "procurement" and not is_admin
    page = browser.new_context(ignore_https_errors=True).new_page()
    _login(page, live_server, email)
    page.goto(live_server + "/enterprise/capabilities/%d/fact-sheet" % typed_org["top_id"],
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("capability-type")).to_be_visible()
    expect(page.get_by_test_id("save-capability-type")).to_have_count(0)
