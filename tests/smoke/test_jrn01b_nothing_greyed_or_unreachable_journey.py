"""No tab, link or page a person needs is greyed out, unreachable or contradictory.

Goals, each walked in a browser in an organisation seeded with linked data:

* a returning user opens an application and its Vendors, Motivation (risks) and Processes tabs show the
  records, or say why they are empty and what to do next; no tab is disabled without a visible reason;
* an administrator finds the vendor pricing upload from the sidebar and from search; an ordinary user is offered
  neither;
* a vendor's page states the same annual spend and contract count as the vendor list, a status a person
  understands, and a breadcrumb that matches the path taken (Spend, Vendors);
* the vendor list filters by status and by renewal window through the toolbar.
"""

import re
import uuid
from datetime import date, datetime, timedelta

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _make_world(app):
    from app import db
    from app.models.apqc_process import APQCProcess, ProcessApplicationMapping
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.organization import Organization
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink
    from app.models.user import Role, User
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.services.billing_plans import set_contract_plan

    tag = uuid.uuid4().hex[:8]
    today = date.today()
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Jrn %s" % tag, slug="jrn-%s" % tag)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        db.session.commit()

        def person(kind, admin):
            user = User(email="jrn.%s.%s@example.com" % (kind, tag), first_name="Jrn", last_name=kind.title(),
                        organization_id=org.id, enterprise_role="platform_admin" if admin else "application_manager",
                        confirmed=True)
            if admin:
                user.role = Role.query.filter_by(name="Administrator").one()
                user.is_platform_admin = True
                user.is_org_admin = True
            user.password = PASSWORD
            user.onboarding_completed_at = datetime.utcnow()
            db.session.add(user)
            db.session.commit()
            return user

        admin = person("admin", True)
        ordinary = person("ordinary", False)

        ms = VendorOrganization(name="Microsoft %s" % tag, status="active")
        other = VendorOrganization(name="Zeta Cloud %s" % tag, status="deprecated")
        db.session.add_all([ms, other])
        db.session.flush()
        ledger = ApplicationComponent(name="Calibration Ledger %s" % tag, organization_id=org.id,
                                      component_type="web_application", lifecycle_status="operational",
                                      vendor_organization_id=ms.id)
        bare = ApplicationComponent(name="Bare Tool %s" % tag, organization_id=org.id,
                                    component_type="web_application", lifecycle_status="operational")
        zeta_app = ApplicationComponent(name="Zeta Tool %s" % tag, organization_id=org.id,
                                        component_type="web_application", lifecycle_status="operational",
                                        vendor_organization_id=other.id)
        db.session.add_all([ledger, bare, zeta_app])
        db.session.flush()
        db.session.add(VendorContract(
            contract_name="Microsoft agreement %s" % tag, organization_id=org.id, vendor_id=ms.id,
            annual_cost=138000.0, currency="GBP", status="active",
            start_date=today - timedelta(days=200), end_date=today + timedelta(days=45)))
        process = APQCProcess(process_code="9.9", process_name="Calibrate instruments %s" % tag)
        db.session.add(process)
        db.session.flush()
        db.session.add(ProcessApplicationMapping(application_id=ledger.id, apqc_process_id=process.id,
                                                 support_level="full", automation_level=3))
        risk = Risk(title="Calibration data loss %s" % tag, likelihood=3, impact=4, organization_id=org.id)
        db.session.add(risk)
        db.session.flush()
        db.session.add(RiskEntityLink(risk_id=risk.id, entity_type="application", entity_id=ledger.id,
                                      organization_id=org.id))
        db.session.commit()
        return {"tag": tag, "admin": admin.email, "ordinary": ordinary.email, "ledger": ledger.id,
                "bare": bare.id, "ms": ms.id}


@pytest.fixture
def world(app, live_server):
    return _make_world(app)


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _text(locator):
    return re.sub(r"\s+", " ", locator.inner_text())


def _open_tab(page, tab):
    page.locator("#tab-%s" % tab).click()
    panel = page.locator("#panel-%s" % tab)
    panel.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    return _text(panel)


def _open_app(page, base, app_id):
    response = page.goto("%s/applications/%s" % (base, app_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response is not None and response.status == 200
    page.wait_for_selector("[role=tablist]")
    page.wait_for_timeout(500)


@pytest.mark.parametrize("width,height,scheme", [(1440, 900, "light"), (390, 844, "dark")])
def test_application_tabs_show_records_or_say_why_they_are_empty(browser, live_server, world, width, height, scheme):
    tag = world["tag"]
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["admin"])

        _open_app(page, live_server, world["ledger"])
        assert page.locator('[role=tab][aria-disabled="true"]').count() == 0
        vendors = _open_tab(page, "vendors")
        assert "Microsoft %s" % tag in vendors, vendors
        motivation = _open_tab(page, "motivation")
        assert "Calibration data loss %s" % tag in motivation, motivation
        link = page.locator("#panel-motivation a", has_text="Calibration data loss %s" % tag)
        link.click()
        page.wait_for_url(re.compile(r"/risks/\d+"), timeout=PAGE_TIMEOUT)
        assert "Calibration data loss %s" % tag in _text(page.locator("main"))
        _open_app(page, live_server, world["ledger"])
        processes = _open_tab(page, "processes")
        assert "Calibrate instruments %s" % tag in processes, processes
        if width == 1440:
            page.screenshot(path="/tmp/jrn01b-tabs-1440-light.png", full_page=True)
        else:
            page.screenshot(path="/tmp/jrn01b-tabs-390-dark.png", full_page=True)

        # An application with nothing recorded: each tab is open and says why it is empty and what to do next.
        _open_app(page, live_server, world["bare"])
        assert page.locator('[role=tab][aria-disabled="true"]').count() == 0
        for tab, reason, step in (
            ("vendors", "names no vendor", "Edit"),
            ("motivation", "No risk is linked", "risk register"),
            ("processes", "No business process is mapped", "Map Process"),
        ):
            body = _open_tab(page, tab)
            assert reason in body, (tab, body)
            assert step in body, (tab, body)
    finally:
        ctx.close()


def test_pricing_upload_is_in_the_menu_and_search_for_an_administrator_only(browser, live_server, world):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["admin"])
        page.goto(live_server + "/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator('a[href="/admin/vendor-pricing/import"]').count() >= 1
        found = page.request.get(live_server + "/api/sidebar/search?q=pricing%20upload").json()
        urls = [r.get("url") for r in found.get("results", found if isinstance(found, list) else [])]
        assert "/admin/vendor-pricing/import" in urls, found
        page.goto(live_server + "/admin/vendor-pricing/import", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator("main").inner_text().strip()
    finally:
        ctx.close()

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["ordinary"])
        page.goto(live_server + "/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator('a[href="/admin/vendor-pricing/import"]').count() == 0
        found = page.request.get(live_server + "/api/sidebar/search?q=pricing%20upload").json()
        urls = [r.get("url") for r in found.get("results", found if isinstance(found, list) else [])]
        assert "/admin/vendor-pricing/import" not in urls, found
    finally:
        ctx.close()


def test_vendor_page_agrees_with_the_vendor_list(browser, live_server, world):
    tag = world["tag"]
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["admin"])
        page.goto(live_server + "/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.locator('[data-testid="vendor-row"]', has_text="Microsoft %s" % tag)
        list_text = _text(row)
        assert "138,000" in list_text, list_text
        row.locator('[data-testid="vendor-link"]').click()
        page.wait_for_url(re.compile(r"/vendors/\d+"), timeout=PAGE_TIMEOUT)
        card = _text(page.locator("#contract-lifecycle"))
        assert "138,000" in card, card
        assert "1 contract" in card, card
        assert "$" not in card, card
        assert "Catalog" not in card, card
        # the catalogue's own USD field, if empty, says it is the catalogue's and points at the spend above
        assert "Not recorded" not in card or "your spend is stated above" in card, card
        assert "Contracted" in card, card
        crumbs = _text(page.locator('nav[aria-label="Breadcrumb"]').first)
        assert "Spend" in crumbs and "Vendors" in crumbs and "Vendor Catalogue" not in crumbs, crumbs
    finally:
        ctx.close()


def test_vendor_list_filters_by_status_and_renewal_window(browser, live_server, world):
    tag = world["tag"]
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["admin"])
        page.goto(live_server + "/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)
        both = page.locator('[data-testid="vendor-row"]:visible')
        assert both.filter(has_text="Microsoft %s" % tag).count() == 1
        assert both.filter(has_text="Zeta Cloud %s" % tag).count() == 1

        page.locator('[data-table-facet="status"]').select_option("deprecated")
        assert page.locator('[data-testid="vendor-row"]:visible', has_text="Zeta Cloud %s" % tag).count() == 1
        assert page.locator('[data-testid="vendor-row"]:visible', has_text="Microsoft %s" % tag).count() == 0
        page.locator('[data-table-facet="status"]').select_option("")

        page.locator('[data-table-facet="renewal_window"]').select_option("due")
        assert page.locator('[data-testid="vendor-row"]:visible', has_text="Microsoft %s" % tag).count() == 1
        assert page.locator('[data-testid="vendor-row"]:visible', has_text="Zeta Cloud %s" % tag).count() == 0
        page.locator("[data-table-reset]").click()
        assert page.locator('[data-testid="vendor-row"]:visible', has_text="Zeta Cloud %s" % tag).count() == 1
    finally:
        ctx.close()
