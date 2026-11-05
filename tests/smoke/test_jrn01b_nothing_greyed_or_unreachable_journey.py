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

        # Bench Notes: no vendor named, an ArchiMate element so vendor products can be added, and one
        # vendor product in the catalogue to add.
        from app.models.archimate_core import ArchiMateElement
        from app.models.vendor.vendor_organization import VendorProduct

        element = ArchiMateElement(name="Bench Notes %s" % tag, type="ApplicationComponent", layer="application",
                                   organization_id=org.id)
        db.session.add(element)
        db.session.flush()
        bench = ApplicationComponent(name="Bench Notes %s" % tag, organization_id=org.id,
                                     component_type="web_application", lifecycle_status="operational",
                                     archimate_element_id=element.id)
        product = VendorProduct(name="Sales Cloud %s" % tag, vendor_organization_id=ms.id)
        db.session.add_all([bench, product])

        # A second organisation with its own application of the same name, to prove it never sees the product.
        other_org = Organization(name="Harbour %s" % tag, slug="harbour-%s" % tag)
        db.session.add(other_org)
        db.session.flush()
        set_contract_plan(other_org, "enterprise", None)
        other_admin = User(email="harbour.%s@example.com" % tag, first_name="Harbour", last_name="Admin",
                           organization_id=other_org.id, enterprise_role="platform_admin", confirmed=True)
        other_admin.role = Role.query.filter_by(name="Administrator").one()
        other_admin.is_platform_admin = True
        other_admin.is_org_admin = True
        other_admin.password = PASSWORD
        other_admin.onboarding_completed_at = datetime.utcnow()
        db.session.add(other_admin)
        other_bench = ApplicationComponent(name="Bench Notes %s" % tag, organization_id=other_org.id,
                                           component_type="web_application", lifecycle_status="operational")
        db.session.add(other_bench)
        db.session.commit()
        return {"tag": tag, "admin": admin.email, "ordinary": ordinary.email, "ledger": ledger.id,
                "bare": bare.id, "ms": ms.id, "bench": bench.id, "product": product.id,
                "process": process.id, "other_admin": other_admin.email, "other_bench": other_bench.id}


def _clean_up(app, world):
    """Remove the APQC catalogue row the world adds: the catalogue is shared by every organisation."""
    from app import db
    from app.models.apqc_process import APQCProcess, ProcessApplicationMapping

    with app.app_context():
        ProcessApplicationMapping.query.filter_by(apqc_process_id=world["process"]).delete()
        APQCProcess.query.filter_by(id=world["process"]).delete()
        db.session.commit()


@pytest.fixture
def world(app, live_server):
    made = _make_world(app)
    yield made
    _clean_up(app, made)


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


def _flash_free(page):
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(400)


def _products_in_use(page):
    card = page.locator('#panel-vendors [data-slot=card]', has_text="Products in Use").first
    return int(re.search(r"(\d+)\s*$", _text(card)).group(1))


def test_vendor_product_added_on_an_application_shows_counts_and_can_be_removed(browser, live_server, world):
    tag = world["tag"]
    product = "Sales Cloud %s" % tag
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["admin"])
        _open_app(page, live_server, world["bench"])
        _open_tab(page, "vendors")
        assert _products_in_use(page) == 0

        page.locator('[data-modal-open="add-vendor-modal"]').click()
        page.locator("#vp-select").select_option(str(world["product"]))
        page.locator("#add-vendor-modal button[type=submit]").click()
        page.wait_for_load_state("domcontentloaded")
        _flash_free(page)
        _open_app(page, live_server, world["bench"])
        panel = _open_tab(page, "vendors")
        assert product in panel, panel
        assert "Microsoft %s" % tag in panel, panel
        assert _products_in_use(page) == 1

        # the vendor link lands on that vendor's page with content
        page.locator("#panel-vendors a", has_text="Open vendor").first.click()
        page.wait_for_url(re.compile(r"/vendors/\d+"), timeout=PAGE_TIMEOUT)
        assert "Microsoft %s" % tag in _text(page.locator("main"))

        # a second organisation never sees the product
        other = browser.new_context(viewport={"width": 1440, "height": 900})
        other.set_default_timeout(PAGE_TIMEOUT)
        other_page = other.new_page()
        try:
            _login(other_page, live_server, world["other_admin"])
            _open_app(other_page, live_server, world["other_bench"])
            other_panel = _open_tab(other_page, "vendors")
            assert product not in other_panel, other_panel
            assert _products_in_use(other_page) == 0
        finally:
            other.close()

        # remove it: the row goes and the count returns to 0 after a reload
        _open_app(page, live_server, world["bench"])
        _open_tab(page, "vendors")
        page.once("dialog", lambda d: d.accept())
        page.locator('#panel-vendors button[aria-label="Remove vendor product"]').click()
        confirm = page.locator('[role=alertdialog] button, [role=dialog] button', has_text=re.compile("^(Remove|Confirm|OK|Yes)", re.I))
        if confirm.count():
            confirm.first.click()
        _flash_free(page)
        _open_app(page, live_server, world["bench"])
        panel = _open_tab(page, "vendors")
        assert product not in panel, panel
        assert _products_in_use(page) == 0
        assert "names no vendor" in panel
    finally:
        ctx.close()


def test_facet_combines_with_search_and_offers_only_options_with_rows(browser, live_server, world):
    tag = world["tag"]
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["admin"])
        page.goto(live_server + "/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(500)
        for facet in ("status", "renewal_window"):
            select = page.locator('[data-table-facet="%s"]' % facet)
            options = select.locator("option").all_inner_texts()[1:]
            for option in options:
                label = re.sub(r"\s*\(\d+\)$", "", option)
                count = int(re.search(r"\((\d+)\)$", option).group(1))
                assert count >= 1, option
                select.select_option(label=option)
                shown = page.locator('[data-testid="vendor-row"]:visible').count()
                assert shown >= 1, (facet, option)
            select.select_option("")
        # "Later" matches no vendor here (Zeta has no renewal ahead), so it is not offered
        renewal = page.locator('[data-table-facet="renewal_window"] option').all_inner_texts()
        assert not any(o.startswith("Later") for o in renewal), renewal
        assert any(o.startswith("Due within 90 days (") for o in renewal), renewal

        page.locator('[data-table-facet="renewal_window"]').select_option(label=[o for o in renewal if o.startswith("Due")][0])
        page.locator("[data-table-filter]").fill("Zeta")
        page.wait_for_timeout(300)
        assert page.locator('[data-testid="vendor-row"]:visible').count() == 0
        page.locator("[data-table-filter]").fill("Microsoft")
        page.wait_for_timeout(300)
        rows = page.locator('[data-testid="vendor-row"]:visible')
        assert rows.count() == 1 and "Microsoft %s" % tag in _text(rows.first)
    finally:
        ctx.close()


def test_ordinary_user_gets_403_on_pricing_upload_and_admin_follows_open_vendor(browser, live_server, world):
    tag = world["tag"]
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["ordinary"])
        response = page.goto(live_server + "/admin/vendor-pricing/import", wait_until="domcontentloaded",
                             timeout=PAGE_TIMEOUT)
        assert response is not None and response.status == 403
        assert "access" in _text(page.locator("body")).lower()
    finally:
        ctx.close()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["admin"])
        _open_app(page, live_server, world["ledger"])
        _open_tab(page, "vendors")
        page.locator("#panel-vendors a", has_text="Open vendor").first.click()
        page.wait_for_url(re.compile(r"/vendors/\d+"), timeout=PAGE_TIMEOUT)
        assert "Microsoft %s" % tag in _text(page.locator("main"))
    finally:
        ctx.close()


def test_vendor_page_spend_names_its_source_and_counts_only_included_contracts(app, browser, live_server, world):
    from app import db
    from app.models.application_portfolio import ApplicationComponent, VendorContract

    tag = world["tag"]
    with app.app_context():
        # a second contract sits on a system that records its own cost: it is listed, but not in the figure
        ledger = db.session.get(ApplicationComponent, world["ledger"])
        from app.services.application_cost_accessor import set_annual_cost
        set_annual_cost(ledger, 5000)
        db.session.add(VendorContract(contract_name="Support on ledger %s" % tag, organization_id=ledger.organization_id,
                                      vendor_id=world["ms"], application_id=ledger.id, annual_cost=9000.0,
                                      currency="GBP", status="active",
                                      start_date=date.today() - timedelta(days=30)))
        db.session.commit()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["admin"])
        page.goto(live_server + "/vendors/%s" % world["ms"], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        spend = _text(page.locator('[data-testid="vendor-annual-spend"]'))
        assert "from systems and contracts" in spend, spend
        assert "1 contract included" in spend, spend
        assert "across" not in spend, spend
    finally:
        ctx.close()


def _toolbar_reachable(page):
    """Every control in the page's table toolbars lies inside the window and is what a click would hit."""
    bad = page.evaluate("""() => {
        const out = [];
        for (const bar of document.querySelectorAll('[data-slot=data-table-toolbar]')) {
            for (const el of bar.querySelectorAll('button, a, input, select')) {
                const r = el.getBoundingClientRect();
                if (!r.width || !r.height) continue;
                if (r.left < -1 || r.right > window.innerWidth + 1) { out.push([el.outerHTML.slice(0, 80), 'outside', Math.round(r.right)]); continue; }
                el.scrollIntoView({block: 'center'});
                const r2 = el.getBoundingClientRect();
                const hit = document.elementFromPoint(r2.left + r2.width / 2, r2.top + r2.height / 2);
                if (!(hit && (hit === el || el.contains(hit) || hit.contains(el)))) out.push([el.outerHTML.slice(0, 80), 'covered']);
            }
        }
        return out; }""")
    return bad


def test_vendor_list_toolbar_is_reachable_at_390_by_click_and_keyboard(browser, live_server, world):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["admin"])
        page.goto(live_server + "/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(600)
        assert _toolbar_reachable(page) == []
        for locator in (page.locator('[data-table-facet="renewal_window"]'),
                        page.locator('[data-testid="vendors-pricing-upload"]'),
                        page.get_by_role("button", name="View")):
            assert locator.first.is_visible()
        # Renewal: by click (a choice made) and by keyboard
        renewal = page.locator('[data-table-facet="renewal_window"]')
        renewal.select_option(index=1)
        assert renewal.input_value() != ""
        renewal.select_option("")
        renewal.focus()
        page.keyboard.press("ArrowDown")
        assert renewal.input_value() != ""
        page.locator("[data-table-reset]").click()
        # View menu: opens by click and by keyboard
        view = page.get_by_role("button", name="View")
        view.click()
        menu = page.locator('[role=menu]:visible', has_text="Toggle columns")
        menu.wait_for(state="visible")
        page.keyboard.press("Escape")
        view.focus()
        page.keyboard.press("Enter")
        page.locator('[role=menu]:visible', has_text="Toggle columns").wait_for(state="visible")
        page.keyboard.press("Escape")
        # Upload pricing: reached by keyboard focus, opened by click
        upload = page.locator('[data-testid="vendors-pricing-upload"]')
        upload.focus()
        assert page.evaluate("document.activeElement.getAttribute('data-testid')") == "vendors-pricing-upload"
        upload.click()
        page.wait_for_url(re.compile(r"/admin/vendor-pricing/import"), timeout=PAGE_TIMEOUT)
        assert page.locator("main").inner_text().strip()
    finally:
        ctx.close()


@pytest.mark.parametrize("path", [
    "/enterprise/implementation/plateaus", "/enterprise/consolidation/candidates",
    "/enterprise/consolidation/opportunities", "/architecture/decision-tables/",
    "/applications/rationalization/gaps", "/adm-kanban/gap-analysis", "/admin/organizations",
    "/procurement/contracts", "/admin/solution-prompts"])
def test_other_list_table_pages_keep_their_toolbar_reachable_at_390(browser, live_server, world, path):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["admin"])
        response = page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response is not None and response.status == 200, (path, response and response.status)
        page.wait_for_timeout(700)
        assert page.locator("[data-slot=data-table-toolbar]").count() >= 1, path
        assert _toolbar_reachable(page) == [], path
        view = page.get_by_role("button", name="View")
        if view.count() and view.first.is_visible():
            view.first.click()
            page.locator('[role=menu]:visible').first.wait_for(state="visible")
            page.keyboard.press("Escape")
            view.first.focus()
            page.keyboard.press("Enter")
            page.locator('[role=menu]:visible').first.wait_for(state="visible")
    finally:
        ctx.close()
