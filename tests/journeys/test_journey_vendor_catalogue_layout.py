"""Journey: the Vendor Catalogue table must fit at 1440px, with no empty box beside it.

Reported problem: the "Readiness" header was cut to "READINE", its value was hidden behind the "Actions"
column, and an empty white box floated to the right of the table.

Root cause, measured in a browser before the fix. The products side panel is meant to be "hidden by default to
give table full width" (app/static/js/vendors/list.js: showVendorProducts removes `lg:hidden` and adds
`lg:block` when a vendor is clicked), but the template's wrapper was `class="hidden lg:block ..."`, already
visible from 1024px up. So at 1440px an empty, bordered panel (innerHTML length 0) reserved 288px, the table
(980px wide) was squeezed into an 820px scroll container, and its sticky, opaque, 176px-wide Actions column sat
on top of the Contract and Score columns.

The real page is rendered for a logged-in architect, with a vendor seeded so the table has a row to measure.
"""
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey

STATIC = Path(__file__).resolve().parents[2] / "app" / "static"

MEASURE = r"""() => {
  const wrap = document.querySelector('.workbench-table-scroll');
  const wrapper = document.getElementById('vendor-products-wrapper');
  const ths = [...document.querySelectorAll('table[role=grid] thead th')].map(th => {
    const r = th.getBoundingClientRect();
    return {text: (th.innerText || '').trim(), left: r.left, right: r.right};
  });
  return {
    scrollWidth: wrap.scrollWidth, clientWidth: wrap.clientWidth,
    wrapperDisplay: getComputedStyle(wrapper).display,
    ths: ths,
  };
}"""


PERSONAS = ["procurement", "portfolio_manager", "application_manager"]
EMPTY_LIST = {"success": True, "vendors": [], "total": 0, "page": 1, "pages": 0, "per_page": 25,
              "pagination": {"has_next": False, "has_prev": False, "page": 1, "pages": 0, "per_page": 25, "total": 0}}


def _document(app, client, persona):
    """Seed an organisation, one vendor and a user with `persona`; return the rendered Vendor Catalogue page."""
    from app import db
    from app.models.vendor.vendor_organization import VendorOrganization

    with app.app_context():
        org = make_org(db, "VendorLayout")
        user = make_user(db, org, "u", persona, role_name="Architect")
        vendor = VendorOrganization(
            name="Layout Vendor " + uuid.uuid4().hex[:6], vendor_type="saas_provider", status="active",
            contract_status="contracted", enterprise_readiness_score=72,
        )
        vendor.named_by_organization_id = org  # the organisation's own vendor, so its Vendors list shows it
        db.session.add(vendor)
        db.session.commit()
    login(client, user)
    response = client.get("/applications/vendors")
    assert response.status_code == 200, "%s cannot open the Vendor Catalogue (%s)" % (persona, response.status_code)
    return response.get_data(as_text=True)


@pytest.fixture
def page_and_document(app, client):
    return _document(app, client, "procurement")


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        chromium = p.chromium.launch()
        yield chromium
        chromium.close()


def _open(browser, client, document, width=1440, height=900, empty_api=False):
    import json

    pg = browser.new_page(viewport={"width": width, "height": height})

    def handle(route):
        url = urlparse(route.request.url)
        if url.path.startswith("/static/"):
            f = STATIC / url.path[len("/static/"):]
            return route.fulfill(path=str(f)) if f.is_file() else route.fulfill(status=404, body="")
        if url.path == "/applications/vendors":
            return route.fulfill(status=200, content_type="text/html", body=document)
        if empty_api and url.path == "/api/vendors/register":
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(EMPTY_LIST))
        if url.path.startswith(("/api/", "/applications/", "/vendors")):
            r = client.get(url.path, query_string=dict(parse_qsl(url.query)))
            return route.fulfill(status=r.status_code, content_type=r.content_type or "application/json", body=r.get_data())
        return route.fulfill(status=204, body="")

    pg.route("http://app.test/**", handle)
    pg.goto("http://app.test/applications/vendors")
    if empty_api:
        pg.wait_for_selector("table[role=grid] tbody tr:visible", state="attached", timeout=15000)
    else:
        pg.wait_for_selector('table[role=grid] tbody button[x-text="row.name"]', timeout=15000)  # the vendor-name button
    pg.wait_for_timeout(500)
    return pg


def test_the_products_panel_is_hidden_until_a_vendor_is_clicked(browser, client, page_and_document):
    pg = _open(browser, client, page_and_document)
    try:
        assert pg.evaluate(MEASURE)["wrapperDisplay"] == "none", "an empty products panel is on screen"
    finally:
        pg.close()


def test_the_table_fits_at_1440_without_scrolling(browser, client, page_and_document):
    pg = _open(browser, client, page_and_document)
    try:
        m = pg.evaluate(MEASURE)
        assert m["scrollWidth"] <= m["clientWidth"] + 1, (
            "the table is %dpx wide in a %dpx container, so it scrolls and the sticky Actions column "
            "covers the columns beside it" % (m["scrollWidth"], m["clientWidth"])
        )
    finally:
        pg.close()


def test_no_column_sits_underneath_the_actions_column(browser, client, page_and_document):
    pg = _open(browser, client, page_and_document)
    try:
        ths = pg.evaluate(MEASURE)["ths"]
        actions = next(t for t in ths if t["text"].lower() == "actions")
        for t in ths:
            if t is actions:
                continue
            assert t["right"] <= actions["left"] + 1, "%r runs underneath the Actions column" % t["text"]
        renewal = next(t for t in ths if t["text"].lower() == "next renewal")
        assert renewal["right"] - renewal["left"] >= 90, "the Next renewal header has no room for its text"
    finally:
        pg.close()


def test_showvendorproducts_still_reveals_the_wrapper_when_called(browser, client, page_and_document):
    """Not a user journey: nothing on this page currently calls showVendorProducts() (no row carries
    data-vendor-id), so this does not prove a click reveals the panel. It only guards the function's own
    contract -- that hiding the wrapper by default (this fix) did not also break its reveal path, in case
    something is wired up to call it later."""
    pg = _open(browser, client, page_and_document)
    try:
        pg.evaluate("() => { try { window.showVendorProducts(1, 'Probe'); } catch (e) {} }")
        pg.wait_for_timeout(300)
        assert pg.evaluate(MEASURE)["wrapperDisplay"] == "block"
    finally:
        pg.close()


EMPTY_ROW = 'table[role=grid] tbody tr:has-text("No vendors")'


@pytest.mark.parametrize("persona", PERSONAS)
def test_no_vendors_and_no_filter_says_no_vendors_yet_with_an_add_action(app, browser, client, persona):
    pg = _open(browser, client, _document(app, client, persona), empty_api=True)
    try:
        row = pg.locator(EMPTY_ROW)
        row.wait_for(state="visible", timeout=10000)
        text = row.inner_text()
        assert "No vendors yet." in text, text
        assert "match these filters" not in text and "Try adjusting" not in text, text
        assert row.get_by_role("button", name="Add your first vendor").is_visible(), "no Add Vendor action inside the empty state"
        assert not row.get_by_role("button", name="Clear filters").is_visible()
    finally:
        pg.close()


@pytest.mark.parametrize("persona", PERSONAS)
def test_a_filter_with_no_match_says_so_and_can_be_cleared(app, browser, client, persona):
    pg = _open(browser, client, _document(app, client, persona))
    try:
        pg.fill('input[type="search"]', "zzz-no-such-vendor-zzz")
        row = pg.locator(EMPTY_ROW)
        row.wait_for(state="visible", timeout=10000)
        text = row.inner_text()
        assert "No vendors match these filters." in text, text
        assert "No vendors yet" not in text, text
        row.get_by_role("button", name="Clear filters").click()
        pg.wait_for_selector('table[role=grid] tbody button[x-text="row.name"]', timeout=10000)
        assert pg.input_value('input[type="search"]') == "", "the search box still shows the old text"
    finally:
        pg.close()


def test_the_filter_card_spans_the_full_content_width_at_1440(browser, client, page_and_document):
    pg = _open(browser, client, page_and_document)
    try:
        widths = pg.evaluate("""() => { const c = document.querySelector('[data-slot="card"]');
            return [c.getBoundingClientRect().width, c.parentElement.getBoundingClientRect().width]; }""")
        assert widths[0] >= widths[1] - 1, "the card is %dpx in a %dpx row" % tuple(widths)
    finally:
        pg.close()


def test_next_renewal_is_readable_at_1280_without_scrolling(browser, client, page_and_document):
    pg = _open(browser, client, page_and_document, width=1280, height=800)
    try:
        ths = pg.evaluate(MEASURE)["ths"]
        actions = next(t for t in ths if t["text"].lower() == "actions")
        renewal = next(t for t in ths if t["text"].lower() == "next renewal")
        assert renewal["right"] <= actions["left"] + 1, "NEXT RENEWAL runs underneath the Actions column at 1280px"
    finally:
        pg.close()


def test_no_horizontal_overflow_and_a_hidden_panel_at_1024(browser, client, page_and_document):
    pg = _open(browser, client, page_and_document, width=1024, height=768)
    try:
        page_overflow = pg.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        assert page_overflow <= 0, "the page overflows sideways by %dpx at 1024px" % page_overflow
        assert pg.evaluate(MEASURE)["wrapperDisplay"] == "none"
    finally:
        pg.close()
