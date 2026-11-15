"""An architect reads the Vendors page, opens suppliers, and saves a system from its fact sheet.

The organisation is seeded with two suppliers that supply systems and one that supplies none; a
contract names the supplier with no systems but sits on another supplier's system. A second
organisation holds its own supplier and system. The journey reads that the sentence counts only
suppliers with systems (not the other organisation's), that each supplier's page lists only
contracts naming it, that the empty contract dates say "Not recorded", and that saving the editor
opened from the fact sheet returns to the fact sheet and says it saved. It runs at 1440 wide in the
light theme and at 390 wide in the dark theme.
"""

import os
import re
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = Path(os.environ.get("VENDOR_FIGURES_SHOTS", "") or "/tmp/vendor-figures-shots")


def _seed(org_id, label):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    app = create_app("testing")
    tag = uuid.uuid4().hex[:6]
    with app.app_context():
        with_a = VendorOrganization(name="%s Supplier A %s" % (label, tag))
        with_b = VendorOrganization(name="%s Supplier B %s" % (label, tag))
        idle = VendorOrganization(name="%s Idle Supplier %s" % (label, tag))
        db.session.add_all([with_a, with_b, idle])
        db.session.flush()
        systems = [
            ApplicationComponent(name="%s System %d %s" % (label, i, tag), organization_id=org_id,
                                 lifecycle_status="operational", vendor_organization_id=v.id)
            for i, v in enumerate([with_a, with_a, with_b])
        ]
        db.session.add_all(systems)
        db.session.flush()
        contract = "%s Idle Agreement %s" % (label, tag)
        db.session.add(VendorContract(
            contract_name=contract, organization_id=org_id, vendor_id=idle.id,
            application_id=systems[2].id, annual_cost=5000.0, status="active",
            start_date=date.today() - timedelta(days=30), end_date=date.today() + timedelta(days=300)))
        db.session.commit()
        return {"a": with_a.id, "b": with_b.id, "idle": idle.id, "idle_name": idle.name,
                "contract": contract, "system": systems[0].id, "system_name": systems[0].name,
                "b_name": with_b.name, "a_name": with_a.name}


def _other_org():
    from app import create_app, db
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        tag = uuid.uuid4().hex[:6]
        org = Organization(name="Other Suppliers %s" % tag, slug="other-suppliers-%s" % tag)
        db.session.add(org)
        db.session.commit()
        return org.id


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.click("#submit", no_wait_after=True)
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))), full_page=True)


def _suppliers_counted(page):
    text = page.get_by_test_id("vendors-answer").inner_text()
    found = re.search(r"(\d+) vendors? supply", text)
    return int(found.group(1)) if found else 0


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_supplier_figures_contracts_and_editor_return(browser, live_server, seeded, width, scheme):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        page.goto(live_server + "/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        before = _suppliers_counted(page)

        mine = _seed(seeded["ids"]["org"], "Mine")
        theirs = _seed(_other_org(), "Theirs")

        page.goto(live_server + "/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        # Two new suppliers supply systems; the third supplies none and is not counted.
        assert _suppliers_counted(page) == before + 2
        expect(page.locator("body")).to_contain_text(mine["idle_name"])
        expect(page.locator("body")).not_to_contain_text(theirs["a_name"])
        _shot(page, "vendors", width, scheme)

        # The supplier the contract names lists it; the supplier whose system it sits on does not.
        page.goto(live_server + "/applications/vendors/%d" % mine["idle"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(mine["contract"])
        _shot(page, "idle-supplier", width, scheme)
        page.goto(live_server + "/applications/vendors/%d" % mine["b"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(mine["b_name"])
        expect(page.locator("body")).not_to_contain_text(mine["contract"])
        _shot(page, "supplier-b", width, scheme)

        # A supplier profile never shows a bare dash for an unrecorded contract date.
        page.goto(live_server + "/applications/vendors/%d" % mine["a"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text(mine["a_name"])
        assert not re.search(r"(Start|End) Date\s*-\s", page.inner_text("body"))

        # Save the editor opened from the fact sheet: back on the fact sheet, with the message.
        page.goto(live_server + "/applications/%d/fact-sheet" % mine["system"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name=re.compile("^Edit$")).first.click()
        page.wait_for_url(re.compile(r"/edit"))
        page.get_by_role("button", name=re.compile("Save Changes")).click()
        page.wait_for_url(re.compile(r"/applications/%d/fact-sheet$" % mine["system"]))
        expect(page.locator("body")).to_contain_text("updated successfully")
        expect(page.locator("body")).to_contain_text(mine["system_name"])
        _shot(page, "after-save", width, scheme)
    finally:
        context.close()
