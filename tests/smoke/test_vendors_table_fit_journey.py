"""A procurement lead reads the Vendors table and finds every figure where it belongs.

The organisation has vendors with long names, contracts in pounds and in dollars, and three contracts due in 20
days. At 1440 wide the Next renewal column is on screen without sideways scrolling; at 1440 and 390 each row's
evidence text stays inside its own cell and never runs over the cost beside it. The three renewals read 3 on the
Renewals page and on Vendors, and the vendor whose contract is in dollars shows its renewal date.
"""
import os
import re
import uuid
from datetime import date, timedelta

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD, _seed_standard_org
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("VENDORS_FIT_WALK_DIR")


@pytest.fixture(scope="module")
def estate(request, ai_protocol_stub, live_server):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.user import User
    from app.models.vendor.vendor_organization import VendorOrganization

    seeded = _seed_standard_org(request, ai_protocol_stub)
    tag = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        org_id = seeded["ids"]["org"]
        today = date.today()
        email = f"fit-{tag}@example.com"
        admin = User(email=email, first_name="Fit", last_name="Buyer",
                     organization_id=org_id, enterprise_role="procurement", confirmed=True)
        admin.password = PASSWORD
        db.session.add(admin)
        db.session.commit()
        admin.grant_org_admin()
        db.session.commit()
        names = {}
        for n, code in enumerate(("GBP", "GBP", "USD")):
            name = f"Fit vendor {n} with a long registered company name {tag}"
            vendor = VendorOrganization(name=name)
            db.session.add(vendor)
            db.session.flush()
            system = ApplicationComponent(name=f"Fit system {n} {tag}", organization_id=org_id,
                                          lifecycle_status="operational", vendor_organization_id=vendor.id)
            db.session.add(system)
            db.session.flush()
            db.session.add(VendorContract(
                contract_name=f"Fit contract {n} {tag}", organization_id=org_id, vendor_id=vendor.id,
                application_id=system.id, annual_cost=1000.0 * (n + 1), currency=code, status="active",
                start_date=today - timedelta(days=300), renewal_date=today + timedelta(days=20),
                end_date=today + timedelta(days=200)))
            names[code if n == 2 else n] = name
        db.session.commit()
    return {"tag": tag, "email": email, "names": names}


def _fits(page):
    """Every footer is inside its own cell and clear of the other cells in its row; the table needs no sideways scroll."""
    return page.evaluate("""() => {
      const out = {rows: 0, outside: [], overlapping: [], tableScroll: null, nextOnScreen: null, pageWide: false};
      const inside = (a, b) => a.left >= b.left - 1 && a.right <= b.right + 1;
      const meets = (a, b) => a.left < b.right - 1 && a.right > b.left + 1 && a.top < b.bottom - 1 && a.bottom > b.top + 1;
      document.querySelectorAll('[data-testid=vendor-row]').forEach(row => {
        out.rows += 1;
        const footer = row.querySelector('[data-testid=result-footer]');
        if (!footer) return;
        const f = footer.getBoundingClientRect();
        const cell = footer.closest('td');
        if (!inside(f, cell.getBoundingClientRect())) out.outside.push(row.innerText.slice(0, 40));
        row.querySelectorAll('td').forEach(td => {
          if (td !== cell && meets(f, td.getBoundingClientRect())) out.overlapping.push(row.innerText.slice(0, 40));
        });
        footer.querySelectorAll('p').forEach(p => {
          if (p.scrollWidth > p.clientWidth + 1) out.outside.push('text of ' + row.innerText.slice(0, 40));
        });
      });
      const table = document.querySelector('[data-testid=vendors-table]');
      let box = table.parentElement;
      while (box && box.scrollWidth <= box.clientWidth + 1 && box !== document.body) box = box.parentElement;
      out.tableScroll = box && box !== document.body ? box.scrollWidth - box.clientWidth : 0;
      const th = [...table.querySelectorAll('th')].find(t => t.innerText.trim().startsWith('Next renewal'));
      out.nextOnScreen = !!th && th.getBoundingClientRect().right <= window.innerWidth;
      out.pageWide = document.documentElement.scrollWidth > window.innerWidth + 1;
      return out;
    }""")


def _shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name + ".png"), full_page=True)


@pytest.mark.parametrize("width,height,scheme", [(1440, 900, "light"), (390, 844, "dark")])
def test_vendor_evidence_stays_in_its_cell_and_next_renewal_is_on_screen(browser, live_server, estate, width, height, scheme):
    page = browser.new_page(viewport={"width": width, "height": height}, color_scheme=scheme)
    try:
        _login(page, live_server, estate["email"])
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        page.wait_for_selector("[data-testid=vendor-row]", timeout=PAGE_TIMEOUT)
        _shot(page, "vendors-%d-%s" % (width, scheme))
        fit = _fits(page)
        assert fit["rows"] >= 3 and not fit["pageWide"], fit
        assert fit["outside"] == [] and fit["overlapping"] == [], fit
        if width >= 1280:
            assert fit["tableScroll"] == 0 and fit["nextOnScreen"], fit
    finally:
        page.close()


def test_three_renewals_read_three_on_renewals_and_vendors_and_the_dollar_vendor_shows_its_date(browser, live_server, estate):
    tag, due = estate["tag"], date.today() + timedelta(days=20)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    try:
        _login(page, live_server, estate["email"])
        assert page.goto(live_server + "/procurement/renewals?days=90", timeout=PAGE_TIMEOUT).status == 200
        renewals = re.sub(r"\s+", " ", page.locator("main").inner_text())
        assert re.search(r"\b3 Renew within 90 days", renewals) or re.search(r"\b3\b.*Renew within 90 days", renewals), renewals
        for n in range(3):
            assert "Fit contract %d %s" % (n, tag) in renewals
        assert page.goto(live_server + "/applications/vendors", timeout=PAGE_TIMEOUT).status == 200
        assert "3 renew within 90 days" in re.sub(r"\s+", " ", page.locator("main").inner_text())
        row = page.get_by_test_id("vendor-row").filter(has_text=estate["names"]["USD"])
        text = re.sub(r"\s+", " ", row.inner_text())
        assert due.strftime("%-d %b %Y") in text and "Due within 90 days" in text and "No renewal ahead" not in text, text
        row.get_by_test_id("vendor-link").click()
        page.wait_for_url(re.compile(r"/vendors?/"), timeout=PAGE_TIMEOUT)
        vendor = re.sub(r"\s+", " ", page.locator("main").inner_text())
        assert "next renewal " + due.strftime("%d %b %Y") in vendor, vendor
    finally:
        page.close()
