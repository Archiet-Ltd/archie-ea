"""Journey: a startup CTO opens Vendors > Catalogue, opens Okta, sees its
components, services, interfaces, certifications and lifecycle, each with its
source and the date it was checked, and links the vendor product the company
uses to it."""

import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(seeded):
    """Load the reviewed catalogue and give the smoke organisation an application
    that uses an Okta product, as a connected source would."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.application_portfolio import ApplicationComponent
        from app.models.capability_to_vendor_mapping import ApplicationVendorProductMapping
        from app.models.user import User
        from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
        from app.services import vendor_catalogue_service as svc

        svc.load_reviewed()
        cto = User.query.filter_by(email=seeded["emails"]["cto"]).one()
        org_id = cto.organization_id
        okta = VendorOrganization.query.filter_by(name="Okta").one()
        vp = VendorProduct(vendor_organization_id=okta.id, name=f"Okta SSO {uuid.uuid4().hex[:6]}")
        db.session.add(vp)
        app_row = ApplicationComponent(name=f"Staff portal {uuid.uuid4().hex[:6]}", organization_id=org_id)
        db.session.add(app_row)
        db.session.flush()
        db.session.add(ApplicationVendorProductMapping(application_component_id=app_row.id, vendor_product_id=vp.id))
        # A vendor product no application or contract of the organisation names.
        not_used = VendorProduct(vendor_organization_id=okta.id, name=f"Okta elsewhere {uuid.uuid4().hex[:6]}")
        db.session.add(not_used)
        db.session.commit()
        return {"vendor_product": vp.name, "application": app_row.name, "not_used": not_used.id, "cto_role": cto.enterprise_role,
                "cto_admin": bool(cto.is_platform_admin or cto.is_org_admin)}


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def test_startup_cto_reads_okta_in_the_catalogue_and_links_it(page, live_server, seeded):
    seed = _seed(seeded)
    # Signed in as the startup CTO, not an administrator.
    assert seed["cto_role"] == "cto"
    assert seed["cto_admin"] is False

    _login(page, live_server, seeded["emails"]["cto"])
    page.goto(live_server + "/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    # The sidebar's Spend group offers the catalogue beside Vendors.
    page.locator("nav a[href$='/applications/vendors/catalogue']").first.click()
    page.wait_for_selector("[data-testid=catalogue-table]", timeout=PAGE_TIMEOUT)
    # The section links mark this page as the current one and lead back to the organisation's own vendors.
    assert page.locator("main a[aria-current=page]", has_text="Catalogue").count() == 1
    assert page.locator("main a[href$='/applications/vendors']", has_text="Vendors").count() >= 1
    assert page.locator("main [role=tab]").count() == 0
    assert page.locator("[data-testid^=catalogue-row-]").count() >= 20

    page.get_by_test_id("catalogue-row-okta").get_by_role("link", name="Okta Workforce Identity").click()
    page.wait_for_selector("[data-testid=catalogue-product][data-slug=okta]", timeout=PAGE_TIMEOUT)
    assert page.locator("h1", has_text="Okta Workforce Identity").count() == 1

    for kind in ("component", "service", "interface", "certification"):
        rows = page.locator(f"tr[data-testid=fact-{kind}]")
        assert rows.count() >= 1, f"no {kind} shown"
        for i in range(rows.count()):
            row = rows.nth(i)
            href = row.get_by_test_id("fact-source").get_attribute("href")
            assert href and href.startswith("https://"), f"{kind} row without a source"
            assert row.get_by_test_id("fact-checked").inner_text().startswith("Checked "), f"{kind} row without a date"

    arch = page.get_by_test_id("catalogue-architecture")
    assert arch.get_by_text("Single sign-on", exact=True).count() == 1
    assert arch.locator("tr[data-testid=fact-interface] td:first-child", has_text="SCIM 2.0").count() == 1
    # The Protocol column is there, with a recorded protocol in it.
    assert arch.locator("thead th", has_text="Protocol").count() == 1
    scim_row = arch.locator("tr[data-testid=fact-interface]", has=page.locator("td:first-child", has_text="SCIM 2.0"))
    assert scim_row.locator("td").nth(2).inner_text().strip() == "SCIM 2.0"
    assert page.get_by_test_id("catalogue-certification").get_by_text("SOC 2 Type II").count() == 1
    lifecycle = page.get_by_test_id("catalogue-lifecycle")
    assert lifecycle.locator("a[href='https://endoflife.date/']").count() == 1

    # A product the organisation does not use is refused, and the page says so out loud.
    page.locator("#vendor_product_id option").first.evaluate("(o, v) => { o.value = String(v); }", seed["not_used"])
    page.select_option("#vendor_product_id", index=0)
    page.get_by_test_id("catalogue-link-submit").click()
    refused = page.get_by_role("alert").filter(has_text="That vendor product is not one your organisation uses.")
    refused.wait_for(timeout=PAGE_TIMEOUT)
    assert refused.get_attribute("data-testid") == "catalogue-link-message"

    page.select_option("#vendor_product_id", label=seed["vendor_product"])
    page.get_by_test_id("catalogue-link-submit").click()
    page.wait_for_selector("[data-testid=catalogue-links]", timeout=PAGE_TIMEOUT)
    assert page.get_by_test_id("catalogue-links").get_by_text(seed["vendor_product"]).count() == 1
    done = page.get_by_role("status").filter(has_text=f"Okta Workforce Identity is now linked to {seed['application']}.")
    assert done.count() == 1 and done.is_visible()
