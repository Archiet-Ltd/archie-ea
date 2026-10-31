"""A capability box on the map opens that capability's own record.

Goal: a CTO looking at the map clicks a box (or tabs to it and presses Enter) and sees who owns the
capability, how mature it is against its target, which applications support it and when it was assessed,
the same facts the capability's record page holds. Another organisation's capability can never be opened.

Tenants are created through the ORM with a three-level hierarchy; the capabilities and mappings are removed
at module teardown.
"""

import re
import uuid
from datetime import datetime

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD
from tests.smoke.test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

MAP_PATH = "/capability-map/dashboard"
BOX = '[data-testid="cap-box"]'
NARROW = {"width": 390, "height": 844}


@pytest.fixture(scope="module")
def detail_tenants(live_server, request):
    from app import create_app, db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_layer import ApplicationComponent
    from app.models.capability_models import BusinessCapability
    from app.models.organization import Organization
    from app.models.user import Role, User

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    out = {"suffix": suffix, "ids": {}, "names": {}, "owner": "Owner %s" % suffix, "app": "Billing App %s" % suffix}
    created = {"caps": [], "apps": [], "maps": []}

    def org_and_user(label):
        org = Organization(name="Smoke Detail %s %s" % (label, suffix), slug="smoke-detail-%s-%s" % (label, suffix))
        db.session.add(org)
        db.session.flush()
        email = "smoke.detail-%s.%s@example.com" % (label, suffix)
        user = User(email=email, first_name="Smoke", last_name="detail-" + label, organization_id=org.id,
                    enterprise_role="cto", confirmed=True, role=Role.query.filter_by(name="Architect").one())
        user.password = PASSWORD
        db.session.add(user)
        db.session.flush()
        out["email_" + label] = email
        return org

    def cap(org, key, level, parent=None, **kw):
        row = BusinessCapability(
            name="Detail %s %s" % (key, suffix), organization_id=org.id, category="Operations", level=level,
            parent_capability_id=parent.id if parent else None, business_owner=out["owner"],
            current_maturity_level=2, target_maturity_level=4, maturity_assessment_date=datetime(2026, 5, 1), **kw)
        db.session.add(row)
        db.session.flush()
        created["caps"].append(row.id)
        out["ids"][key] = row.id
        out["names"][key] = row.name
        return row

    with app.app_context():
        org = org_and_user("main")
        other = org_and_user("other")
        l1 = cap(org, "l1", 1)
        l2 = cap(org, "l2", 2, l1)
        l3 = cap(org, "l3", 3, l2)
        foreign = cap(other, "foreign", 1)
        component = ApplicationComponent(name=out["app"], organization_id=org.id)
        db.session.add(component)
        db.session.flush()
        created["apps"].append(component.id)
        for row in (l1, l2, l3):
            m = ApplicationCapabilityMapping(organization_id=org.id, application_component_id=component.id,
                                             business_capability_id=row.id)
            db.session.add(m)
            db.session.flush()
            created["maps"].append(m.id)
        db.session.commit()

    def remove_rows():
        with app.app_context():
            db.session.remove()
            if created["maps"]:
                db.session.query(ApplicationCapabilityMapping).filter(
                    ApplicationCapabilityMapping.id.in_(created["maps"])).delete(synchronize_session=False)
            if created["caps"]:
                db.session.query(BusinessCapability).filter(
                    BusinessCapability.id.in_(created["caps"])).update(
                    {"parent_capability_id": None}, synchronize_session=False)
                db.session.query(BusinessCapability).filter(
                    BusinessCapability.id.in_(created["caps"])).delete(synchronize_session=False)
            if created["apps"]:
                db.session.query(ApplicationComponent).filter(
                    ApplicationComponent.id.in_(created["apps"])).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()

    request.addfinalizer(remove_rows)
    return out


def _open_map(browser, base, email, viewport=None, scheme="light"):
    context = browser.new_context(viewport=viewport or {"width": 1440, "height": 900}, color_scheme=scheme)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    _login(page, base, email)
    page.goto(base + MAP_PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    return context, page


def _box_button(page, name):
    return page.locator(BOX, has=page.locator("> button", has_text=name)).first.locator("> button")


def _assert_record(page, detail_tenants, key):
    expected = "/capabilities/%d/fact-sheet" % detail_tenants["ids"][key]
    expect(page).to_have_url(re.compile(re.escape(expected) + "$"), timeout=PAGE_TIMEOUT)
    body = page.locator("body")
    expect(body).to_contain_text(detail_tenants["names"][key])
    expect(body).to_contain_text(detail_tenants["owner"])
    expect(body).to_contain_text("2-level gap to target")
    expect(body).to_contain_text("2026")
    expect(body).to_contain_text(detail_tenants["app"])


@pytest.mark.parametrize("key", ["l1", "l2", "l3"])
def test_clicking_a_box_opens_that_capabilitys_record(browser, live_server, detail_tenants, key):
    context, page = _open_map(browser, live_server, detail_tenants["email_main"])
    try:
        button = _box_button(page, detail_tenants["names"][key])
        expect(button).to_be_visible(timeout=PAGE_TIMEOUT)
        button.click()
        _assert_record(page, detail_tenants, key)
    finally:
        context.close()


def test_tab_then_enter_opens_the_record_and_the_box_has_a_name(browser, live_server, detail_tenants):
    context, page = _open_map(browser, live_server, detail_tenants["email_main"], NARROW, "dark")
    try:
        button = _box_button(page, detail_tenants["names"]["l2"])
        expect(button).to_be_visible(timeout=PAGE_TIMEOUT)
        label = button.get_attribute("aria-label")
        assert detail_tenants["names"]["l2"] in label and "level 2" in label and "Gap 2" in label, label
        button.focus()
        page.keyboard.press("Enter")
        _assert_record(page, detail_tenants, "l2")
    finally:
        context.close()


def test_another_organisations_capability_cannot_be_opened(browser, live_server, detail_tenants):
    context, page = _open_map(browser, live_server, detail_tenants["email_main"])
    try:
        assert page.locator(BOX, has_text=detail_tenants["names"]["foreign"]).count() == 0
        response = page.goto(live_server + "/enterprise/capabilities/%d/fact-sheet" % detail_tenants["ids"]["foreign"],
                             wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 404, response.status
        assert detail_tenants["names"]["foreign"] not in page.content()
    finally:
        context.close()



@pytest.mark.parametrize("viewport,scheme,tag", [
    ({"width": 1440, "height": 900}, "light", "1440-light"), (NARROW, "dark", "390-dark"),
])
def test_map_and_opened_record_have_no_serious_accessibility_issue(browser, live_server, detail_tenants, viewport, scheme, tag):
    import os
    from axe_playwright_python.sync_playwright import Axe

    shots = os.environ.get("CAPMAP03D_SHOTS")

    def serious(p):
        report = Axe().run(p, options={"runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa", "wcag22aa"]}})
        data = report.response if hasattr(report, "response") else report
        return [(v["id"], v["impact"], len(v["nodes"])) for v in data.get("violations", [])
                if v.get("impact") in ("serious", "critical")]

    context, page = _open_map(browser, live_server, detail_tenants["email_main"], viewport, scheme)
    try:
        expect(_box_button(page, detail_tenants["names"]["l2"])).to_be_visible(timeout=PAGE_TIMEOUT)
        if shots:
            page.screenshot(path=os.path.join(shots, "map-%s.png" % tag))
        assert serious(page) == [], "map"
        _box_button(page, detail_tenants["names"]["l2"]).click()
        _assert_record(page, detail_tenants, "l2")
        page.wait_for_load_state("networkidle")
        if shots:
            page.screenshot(path=os.path.join(shots, "detail-%s.png" % tag))
        assert serious(page) == [], "opened record"
    finally:
        context.close()
