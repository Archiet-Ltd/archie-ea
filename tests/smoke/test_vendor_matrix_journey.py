"""Vendor capability matrix: a cell says what the vendor's own page says.

An architect picks a product category on the Vendor Capability Matrix and reads how well each vendor's
product covers each capability. The coverage in a cell must be the coverage the vendor's page lists for
that capability, the figures above the matrix must be counted from the same cells, the steps of the
scale must be theme tokens with a legend, and a filter that leaves nothing says why and offers a way back.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org
from .overview_data import purge_org
from .test_try_sample_company_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

CATEGORY = "Suite %s"
# (vendor, product, capability, coverage)
CELLS = [("Alpha", "Alpha One", "Order intake", 85), ("Alpha", "Alpha One", "Invoicing", 45),
         ("Beta", "Beta Core", "Order intake", 62)]


@pytest.fixture(scope="module")
def seeded(live_server):
    from app import create_app, db
    from app.models.business_capabilities import BusinessCapability
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct, VendorProductCapability

    info = create_fresh_org("cto")
    suffix = uuid.uuid4().hex[:6]
    category = CATEGORY % suffix
    app = create_app("testing")
    vendors, products, caps, mappings = {}, {}, {}, []
    with app.app_context():
        for vendor, product, capability, coverage in CELLS:
            if vendor not in vendors:
                row = VendorOrganization(name="%s %s" % (vendor, suffix))
                db.session.add(row)
                db.session.flush()
                vendors[vendor] = row.id
            if product not in products:
                row = VendorProduct(vendor_organization_id=vendors[vendor], name=product, product_type=category)
                db.session.add(row)
                db.session.flush()
                products[product] = row.id
            if capability not in caps:
                row = BusinessCapability(name="%s %s" % (capability, suffix), organization_id=info["org_id"])
                db.session.add(row)
                db.session.flush()
                caps[capability] = row.id
            row = VendorProductCapability(vendor_product_id=products[product], business_capability_id=caps[capability],
                                          coverage_percentage=coverage, out_of_box_percentage=coverage - 10,
                                          organization_id=info["org_id"])
            db.session.add(row)
            db.session.flush()
            mappings.append(row.id)
        db.session.commit()
        db.session.remove()
    info.update(category=category, suffix=suffix, vendors=vendors, caps=caps)
    yield info
    with app.app_context():
        db.session.execute(db.text("DELETE FROM vendor_product_capabilities WHERE id = ANY(:ids)"), {"ids": mappings})
        db.session.execute(db.text("DELETE FROM vendor_products WHERE id = ANY(:ids)"), {"ids": list(products.values())})
        db.session.execute(db.text("DELETE FROM vendor_organizations WHERE id = ANY(:ids)"), {"ids": list(vendors.values())})
        db.session.execute(db.text("DELETE FROM business_capability WHERE id = ANY(:ids)"), {"ids": list(caps.values())})
        db.session.commit()
        db.session.remove()
    purge_org(info["org_id"])


def _open_matrix(page, live_server, seeded):
    _login(page, live_server, seeded["emails"]["cto"])
    response = page.goto(live_server + "/dashboards/vendor-capability-matrix", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert response.status == 200
    page.select_option("#f-category", seeded["category"])
    expect(page.get_by_test_id("matrix-cell").first).to_be_visible(timeout=PAGE_TIMEOUT)


def _cell(page, vendor_name, capability_name):
    return page.locator("[data-testid=matrix-cell][data-vendor='%s'][data-capability='%s']" % (vendor_name, capability_name))


def test_a_cell_equals_the_vendor_page(live_server, seeded, page):
    _open_matrix(page, live_server, seeded)
    suffix = seeded["suffix"]
    checked = 0
    for vendor, _product, capability, coverage in CELLS:
        cell = _cell(page, "%s %s" % (vendor, suffix), "%s %s" % (capability, suffix))
        expect(cell).to_have_count(1)
        assert cell.get_attribute("data-coverage") == str(coverage)
        assert cell.inner_text().strip() == "%d%%" % coverage
        checked += 1
    assert checked == len(CELLS)

    for vendor in ("Alpha", "Beta"):
        page.goto(live_server + "/vendors/%d" % seeded["vendors"][vendor], wait_until="networkidle", timeout=PAGE_TIMEOUT)
        main = " ".join(page.inner_text("main").split())
        for v, _product, capability, coverage in CELLS:
            if v == vendor:
                # The vendor page writes the same figure as a decimal (85.0%); the number is what must agree.
                match = re.search(r"%s %s (\d+(?:\.\d+)?)%%" % (re.escape(capability), suffix), main)
                assert match, "the vendor page does not list %s" % capability
                assert float(match.group(1)) == coverage, "the vendor page says %s, the matrix says %d" % (match.group(1), coverage)


def test_the_figures_are_counted_from_the_cells(live_server, seeded, page):
    _open_matrix(page, live_server, seeded)
    value = lambda key: page.get_by_test_id("vendor-matrix-%s-value" % key).inner_text().strip()
    assert value("capabilities") == "2"
    assert value("vendors") == "2"
    assert value("coverage") == "64%"  # (85 + 45 + 62) / 3
    assert value("strong") == "1"
    assert page.get_by_test_id("matrix-cell").count() == 3
    expect(page.get_by_test_id("matrix-cell-empty")).to_have_count(1)  # Beta offers no invoicing, and the cell says so


def test_clicking_a_cell_opens_that_coverage(live_server, seeded, page):
    _open_matrix(page, live_server, seeded)
    suffix = seeded["suffix"]
    _cell(page, "Alpha " + suffix, "Order intake " + suffix).click()
    dialog = page.get_by_test_id("matrix-detail")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("85%")
    expect(dialog).to_contain_text("Order intake " + suffix)


def test_stat_row_and_scale_use_the_library_and_tokens(live_server, seeded, page):
    _open_matrix(page, live_server, seeded)
    stats = page.get_by_test_id("vendor-matrix-stats")
    assert 1 <= stats.locator("[data-slot='card']").count() <= 4
    assert stats.locator("svg").count() == 0, "stat cards carry no icons"
    assert page.evaluate(
        """() => [...document.querySelectorAll('[data-testid=vendor-matrix-stats] [data-slot=card]')]
                 .every(c => !/border-(success|warning|destructive)/.test(c.className))"""
    )
    legend = page.get_by_test_id("matrix-legend")
    expect(legend).to_be_visible()
    for label in ("Strong", "Moderate", "Partial", "Weak"):
        expect(legend).to_contain_text(label)
    classes = page.evaluate("() => [...document.querySelectorAll('[data-testid=matrix-cell], [data-testid=matrix-legend] *')].map(e => e.className).join(' ')")
    assert not re.search(r"\b(?:emerald|amber|green|red|yellow)-\d", classes), "heat steps must be theme tokens"


def test_a_filter_that_leaves_nothing_says_why_and_offers_the_way_back(live_server, seeded, page):
    _open_matrix(page, live_server, seeded)
    page.fill("#f-vendor", "no such vendor")
    empty = page.get_by_test_id("matrix-empty")
    expect(empty).to_be_visible()
    expect(empty).to_contain_text("No coverage matches these filters")
    page.get_by_test_id("matrix-clear-filters").click()
    expect(page.get_by_test_id("matrix-cell").first).to_be_visible()


def test_before_a_category_is_chosen_the_page_says_what_to_do(live_server, seeded, page):
    _login(page, live_server, seeded["emails"]["cto"])
    page.goto(live_server + "/dashboards/vendor-capability-matrix", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    prompt = page.get_by_test_id("matrix-choose-category")
    expect(prompt).to_be_visible()
    expect(prompt).to_contain_text("Choose a product category")
