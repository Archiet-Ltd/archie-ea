"""Journey: the Vendors table must fit at 1440px, with no empty box beside it.

Reported problem (on the earlier catalogue): the "Readiness" header was cut to "READINE", its value was hidden
behind the "Actions" column, and an empty white box (a products side panel nobody had opened) floated to the right
of the table.

The page is now the shared data table (components/list_table.html): the vendors' names, systems, cost and next
renewal, with each row's actions in an ellipsis menu in the last column, and no side panel. These checks hold the
same outcomes against it: the table fits without scrolling sideways, no column runs under the actions column, the
Next renewal header has room for its text, no empty panel takes width from the table, an organisation with no
vendors is told so with an Add action, and a filter that matches nothing says so and can be cleared.

The real page is rendered for a logged-in user, with vendors seeded so the table has rows to measure.
"""
import uuid
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey

STATIC = Path(__file__).resolve().parents[2] / "app" / "static"
TABLE = '[data-testid="vendors-table"]'

MEASURE = r"""() => {
  const pager = document.querySelector('[data-testid="vendors-table-pager"]');
  const wrap = pager.querySelector('.overflow-x-auto');
  const ths = [...pager.querySelectorAll('table thead th')].map(th => {
    const r = th.getBoundingClientRect();
    return {text: (th.innerText || '').trim(), left: r.left, right: r.right};
  });
  return {
    scrollWidth: wrap.scrollWidth, clientWidth: wrap.clientWidth,
    pagerWidth: pager.getBoundingClientRect().width,
    rowWidth: pager.parentElement.getBoundingClientRect().width,
    productsPanels: document.querySelectorAll('#vendor-products-wrapper').length,
    ths: ths,
  };
}"""


PERSONAS = ["procurement", "portfolio_manager", "application_manager"]


def _document(app, client, persona, vendors=1):
    """Seed an organisation, `vendors` vendors (the first with a contract that renews) and a user with
    `persona`; return the rendered Vendors page."""
    from app import db
    from app.models.application_portfolio import VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    with app.app_context():
        org = make_org(db, "VendorLayout")
        user = make_user(db, org, "u", persona, role_name="Architect")
        for i in range(vendors):
            vendor = VendorOrganization(
                name="Layout Vendor %d %s" % (i, uuid.uuid4().hex[:6]), vendor_type="saas_provider", status="active",
                contract_status="contracted", enterprise_readiness_score=72,
            )
            vendor.named_by_organization_id = org  # the organisation's own vendor, so its Vendors list shows it
            db.session.add(vendor)
            db.session.flush()
            if i == 0:
                db.session.add(VendorContract(
                    contract_name="Layout contract", organization_id=org, vendor_id=vendor.id, status="active",
                    start_date=date.today() - timedelta(days=30), end_date=date.today() + timedelta(days=200),
                    annual_cost=12000.0))
        db.session.commit()
    login(client, user)
    response = client.get("/applications/vendors")
    assert response.status_code == 200, "%s cannot open the Vendors page (%s)" % (persona, response.status_code)
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


def _open(browser, client, document, width=1440, height=900, rows=True):
    pg = browser.new_page(viewport={"width": width, "height": height})

    def handle(route):
        url = urlparse(route.request.url)
        if url.path.startswith("/static/"):
            f = STATIC / url.path[len("/static/"):]
            return route.fulfill(path=str(f)) if f.is_file() else route.fulfill(status=404, body="")
        if url.path == "/applications/vendors":
            return route.fulfill(status=200, content_type="text/html", body=document)
        if url.path.startswith(("/api/", "/applications/", "/vendors")):
            r = client.get(url.path, query_string=dict(parse_qsl(url.query)))
            return route.fulfill(status=r.status_code, content_type=r.content_type or "application/json", body=r.get_data())
        return route.fulfill(status=204, body="")

    pg.route("http://app.test/**", handle)
    pg.goto("http://app.test/applications/vendors")
    if rows:
        pg.wait_for_selector(TABLE + ' tbody [data-testid="vendor-link"]', timeout=15000)  # a vendor's name
    else:
        pg.wait_for_selector(TABLE + " tbody tr[data-table-none]", timeout=15000)
    pg.wait_for_timeout(500)
    return pg


def test_no_empty_products_panel_takes_width_from_the_table(browser, client, page_and_document):
    pg = _open(browser, client, page_and_document)
    try:
        m = pg.evaluate(MEASURE)
        assert m["productsPanels"] == 0, "an empty products panel is on the page"
        assert m["pagerWidth"] >= m["rowWidth"] - 1, "the table is %dpx in a %dpx row" % (m["pagerWidth"], m["rowWidth"])
    finally:
        pg.close()


def test_the_table_fits_at_1440_without_scrolling(browser, client, page_and_document):
    pg = _open(browser, client, page_and_document)
    try:
        m = pg.evaluate(MEASURE)
        assert m["scrollWidth"] <= m["clientWidth"] + 1, (
            "the table is %dpx wide in a %dpx container, so it scrolls sideways" % (m["scrollWidth"], m["clientWidth"])
        )
    finally:
        pg.close()


def _actions_and_renewal(ths):
    actions = ths[-1]  # the row-actions column: no header text, the ellipsis menu in each row
    assert actions["text"] == "", "the last column should hold the row actions, got %r" % actions["text"]
    renewal = next(t for t in ths if t["text"].lower() == "next renewal")
    return actions, renewal


def test_no_column_sits_underneath_the_actions_column(browser, client, page_and_document):
    pg = _open(browser, client, page_and_document)
    try:
        ths = pg.evaluate(MEASURE)["ths"]
        actions, renewal = _actions_and_renewal(ths)
        for t in ths[:-1]:
            assert t["right"] <= actions["left"] + 1, "%r runs underneath the actions column" % t["text"]
        assert renewal["right"] - renewal["left"] >= 90, "the Next renewal header has no room for its text"
        assert pg.locator(TABLE + " tbody").get_by_role("button", name="Actions for Layout Vendor 0").count() == 1
    finally:
        pg.close()


@pytest.mark.parametrize("persona", PERSONAS)
def test_no_vendors_and_no_filter_says_no_vendors_yet_with_an_add_action(app, browser, client, persona):
    pg = _open(browser, client, _document(app, client, persona, vendors=0), rows=False)
    try:
        row = pg.locator(TABLE + " tbody tr[data-table-none]")
        row.wait_for(state="visible", timeout=10000)
        text = row.inner_text()
        assert "No vendors yet." in text, text
        assert "No results" not in text, text
        assert row.get_by_role("button", name="Add your first vendor").is_visible(), "no Add vendor action inside the empty state"
    finally:
        pg.close()


@pytest.mark.parametrize("persona", PERSONAS)
def test_a_filter_with_no_match_says_so_and_can_be_cleared(app, browser, client, persona):
    # The filter box appears once the list is long enough to need it.
    pg = _open(browser, client, _document(app, client, persona, vendors=9))
    try:
        search = pg.locator('[data-testid="vendors-table-pager"] input[data-table-filter]')
        search.fill("zzz-no-such-vendor-zzz")
        row = pg.locator(TABLE + " tbody tr[data-table-empty]")
        row.wait_for(state="visible", timeout=10000)
        assert "No results." in row.inner_text()
        assert pg.locator(TABLE + ' tbody [data-testid="vendor-row"]:visible').count() == 0
        pg.locator('[data-testid="vendors-table-pager"] [data-table-reset]').click()
        pg.wait_for_selector(TABLE + ' tbody [data-testid="vendor-link"]', timeout=10000)
        assert pg.locator(TABLE + ' tbody [data-testid="vendor-row"]:visible').count() == 9
        assert search.input_value() == "", "the search box still shows the old text"
    finally:
        pg.close()


def test_next_renewal_is_readable_at_1280_without_scrolling(browser, client, page_and_document):
    pg = _open(browser, client, page_and_document, width=1280, height=800)
    try:
        m = pg.evaluate(MEASURE)
        actions, renewal = _actions_and_renewal(m["ths"])
        assert renewal["right"] <= actions["left"] + 1, "NEXT RENEWAL runs underneath the actions column at 1280px"
        assert m["scrollWidth"] <= m["clientWidth"] + 1
    finally:
        pg.close()


def test_no_horizontal_overflow_at_1024(browser, client, page_and_document):
    pg = _open(browser, client, page_and_document, width=1024, height=768)
    try:
        page_overflow = pg.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        assert page_overflow <= 0, "the page overflows sideways by %dpx at 1024px" % page_overflow
        assert pg.evaluate(MEASURE)["productsPanels"] == 0
    finally:
        pg.close()
