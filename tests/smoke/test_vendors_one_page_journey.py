"""Two organisations each read one Vendors page: their own vendors, one count, filters and Edit.

The retired list at /dashboard/applications/vendors counted every organisation's
vendors. Each administrator now opens that old address, lands on the one Vendors
page, reads a count that is only their own, filters by type and by contract
status, and opens Edit on a row. The count agrees with the vendor the Spend page
names and with the AI chat's vendors link, and never includes the other
organisation's vendor.
"""
import re
import uuid
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, _seed_standard_org
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def two_orgs(request, ai_protocol_stub, live_server):
    from app import create_app, db
    from app.models.application_portfolio import VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    tag = uuid.uuid4().hex[:6]
    app = create_app("testing")
    worlds = {}
    for key, vtype, status in (("a", "software_vendor", "active"), ("b", "cloud_provider", "negotiating")):
        seeded = _seed_standard_org(request, ai_protocol_stub)
        org_id = seeded["ids"]["org"]
        with app.app_context():
            names = {}
            for i, (t, s) in enumerate(((vtype, status), ("systems_integrator", "expired"))):
                name = f"Vendor {key.upper()}{i} {tag}"
                v = VendorOrganization(
                    name=name, code=f"V-{key}{i}-{tag}".upper(), seed_source_id=f"smoke:{key}{i}:{tag}",
                    vendor_type=t, contract_status=s, status="active", named_by_organization_id=org_id)
                db.session.add(v)
                db.session.commit()
                names[i] = (name, v.id, t, s)
            db.session.add(VendorContract(
                contract_name=f"Contract {key} {tag}", organization_id=org_id, vendor_id=names[0][1],
                start_date=date.today() - timedelta(days=100), end_date=date.today() + timedelta(days=265),
                annual_cost=50000.0, status="active"))
            db.session.commit()
        worlds[key] = {"email": seeded["emails"]["platform_admin"], "vendors": names, "org": org_id}
    return {"tag": tag, "a": worlds["a"], "b": worlds["b"]}


def _count_on_page(page):
    return page.get_by_test_id("vendor-row").count()


@pytest.mark.parametrize("key,other", [("a", "b"), ("b", "a")])
def test_administrator_reads_one_vendors_page_with_only_their_vendors(browser, live_server, two_orgs, key, other):
    me, them = two_orgs[key], two_orgs[other]
    mine = [v[0] for v in me["vendors"].values()]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, me["email"])

        # The retired address lands on the one Vendors page and keeps the filters as that page's own.
        page.goto(live_server + "/dashboard/applications/vendors?vendor_type=%s&contract_status=%s"
                  % (me["vendors"][0][2], me["vendors"][0][3]), timeout=PAGE_TIMEOUT)
        url = urlparse(page.url)
        assert url.path.rstrip("/") == "/applications/vendors", page.url
        assert parse_qs(url.query).get("vendor_type") == [me["vendors"][0][2]]
        assert parse_qs(url.query).get("contract_status") == [me["vendors"][0][3]]
        rows = page.get_by_test_id("vendor-row")
        expect(rows).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(rows.first).to_contain_text(me["vendors"][0][0])

        # Unfiltered: exactly this organisation's vendors, never the other's.
        page.goto(live_server + "/dashboard/applications/vendors/", timeout=PAGE_TIMEOUT)
        assert urlparse(page.url).path.rstrip("/") == "/applications/vendors"
        expect(page.get_by_test_id("vendor-row")).to_have_count(len(mine), timeout=PAGE_TIMEOUT)
        body = page.locator("main, body").first.inner_text()
        for name in mine:
            assert name in body
        for v in them["vendors"].values():
            assert v[0] not in body
        answer = page.get_by_test_id("vendors-answer")
        expect(answer).to_contain_text("%d vendor" % len(mine))

        # Filter by type, then by contract status, through the toolbar.
        page.get_by_test_id("vendor-type-filter").select_option(me["vendors"][1][2])
        page.get_by_test_id("vendor-filter-apply").click()
        expect(page.get_by_test_id("vendor-row")).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("vendor-row").first).to_contain_text(me["vendors"][1][0])
        page.get_by_test_id("vendor-type-filter").select_option("all")
        page.get_by_test_id("vendor-status-filter").select_option(me["vendors"][0][3])
        page.get_by_test_id("vendor-filter-apply").click()
        expect(page.get_by_test_id("vendor-row")).to_have_count(1, timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("vendor-row").first).to_contain_text(me["vendors"][0][0])

        # Edit opens on a page with the vendor's own name in the form.
        row = page.get_by_test_id("vendor-row").first
        row.get_by_role("button", name=re.compile("Actions for")).click()
        page.get_by_test_id("vendor-edit").first.click()
        expect(page).to_have_url(re.compile(r"/vendors/%d/edit" % me["vendors"][0][1]), timeout=PAGE_TIMEOUT)
        expect(page.locator("input[name=name]").first).to_have_value(me["vendors"][0][0])

        # The Spend page names this organisation's vendor and links to the same page.
        page.goto(live_server + "/procurement/spend", timeout=PAGE_TIMEOUT)
        card = page.get_by_test_id("spend-top-vendor")
        expect(card).to_contain_text(me["vendors"][0][0], timeout=PAGE_TIMEOUT)
        href = card.get_attribute("href")
        assert urlparse(href).path.rstrip("/") == "/applications/vendors"

        # The AI chat's vendors link goes to the same page.
        from app.modules.ai_chat.services.ai_chat_link_service import AIChatLinkService
        links = AIChatLinkService().extract_contextual_links("show me the vendor analysis", "vendors")
        assert any(urlparse(l["url"]).path.rstrip("/") == "/applications/vendors" for l in links), links

        # Phone, dark.
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT)
        page.evaluate("document.documentElement.classList.add('dark')")
        expect(page.get_by_test_id("vendor-row").first).to_be_visible()
    finally:
        page.close()
