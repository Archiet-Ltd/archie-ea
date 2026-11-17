"""A chief technology officer opens every page that states a result and finds, under each result, what it
was worked out from, where that came from, how far to trust it and when.

The organisation holds linked records: applications (one entered by hand with an owner, one synced by a
connector with none), a vendor supplying both, a business unit, work packages, a critical gap, a plateau, a
scored capability and an element reaching another. A second organisation holds records with other names and
none of them may appear. Pages: the applications list and ownership coverage, the vendor list and a vendor,
the investment matrix, the capability roadmap, the work package list and a work package, and the impact
analysis.
"""

import os
import re
import uuid
from datetime import date, datetime, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PARTS = ("evidence", "source", "confidence", "date")


def _seed(org_id, noun):
    """One organisation's linked records; returns the ids a journey needs."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.enterprise_intelligence import OrganizationUnit
    from app.models.implementation_migration import Gap, Plateau
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.models.vendor.vendor_organization import VendorOrganization

    app = create_app("testing")
    with app.app_context():
        vendor = VendorOrganization(name="%s Vendor" % noun, status="active", named_by_organization_id=org_id)
        db.session.add(vendor)
        db.session.flush()
        db.session.add(OrganizationUnit(name="%s Finance" % noun, organization_id=org_id))
        owned = ApplicationComponent(name="%s Ledger" % noun, organization_id=org_id, business_domain="%s Finance" % noun,
                                     lifecycle_status="active", vendor_organization_id=vendor.id)
        owned.legacy_business_owner = "Ada Lovelace"
        synced = ApplicationComponent(name="%s Portal" % noun, organization_id=org_id, business_domain="%s Finance" % noun,
                                      lifecycle_status="active", data_source="abacus", vendor_organization_id=vendor.id)
        db.session.add_all([owned, synced])
        db.session.add(Gap(name="%s gap" % noun, severity="critical", priority="critical", organization_id=org_id))
        db.session.add(Plateau(name="%s Target" % noun, organization_id=org_id, sequence_order=1,
                               target_date=date.today() + timedelta(days=200)))
        db.session.add(UnifiedWorkPackage(name="%s rollout" % noun, organization_id=org_id, status="in_progress",
                                          priority="high", progress_percentage=40,
                                          end_date=datetime.utcnow() + timedelta(days=30)))
        db.session.add(UnifiedCapability(name="%s Billing" % noun, organization_id=org_id, level=1,
                                         strategic_importance="high"))
        hub = ArchiMateElement(name="%s Hub" % noun, type="ApplicationComponent", layer="application",
                               organization_id=org_id)
        spoke = ArchiMateElement(name="%s Spoke" % noun, type="ApplicationComponent", layer="application",
                                 organization_id=org_id)
        db.session.add_all([hub, spoke])
        db.session.flush()
        db.session.add(ArchiMateRelationship(type="Serving", source_id=hub.id, target_id=spoke.id,
                                             organization_id=org_id))
        db.session.commit()
        ids = {"vendor": vendor.id, "wp": db.session.query(UnifiedWorkPackage.id).filter_by(
            organization_id=org_id).scalar()}
        db.session.remove()
    return ids


def _four_parts(footer):
    for part in PARTS:
        expect(footer.get_by_test_id("result-footer-" + part)).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(footer.get_by_test_id("result-footer-date")).to_contain_text("Worked out")


def _all_footers(page, expected_at_least, text=None):
    footers = page.locator('[data-testid="result-footer"]:visible')
    if text:
        footers = footers.filter(has_text=text)
    expect(footers.first).to_be_visible(timeout=PAGE_TIMEOUT)
    assert footers.count() >= expected_at_least, (page.url, footers.count())
    for index in range(footers.count()):
        _four_parts(footers.nth(index))
    return footers


def _walk(page):
    """Evidence for a walk, only when WALK_DIR is set: the page's first footer in view, in the walk's theme."""
    folder = os.environ.get("WALK_DIR")
    if not folder:
        return
    os.makedirs(folder, exist_ok=True)
    if os.environ.get("WALK_MODE") == "narrow-dark":
        page.evaluate("() => document.documentElement.classList.add('dark')")
    footer = page.locator('[data-testid="result-footer"]:visible').first
    if footer.count():
        footer.scroll_into_view_if_needed()
    name = re.sub(r"[^a-z0-9]+", "-", page.url.split("//", 1)[-1].split("/", 1)[-1].lower()).strip("-") or "home"
    page.screenshot(path=os.path.join(folder, "%s-%s.png" % (os.environ.get("WALK_MODE", "wide-light"), name[:60])))


def _no_other_organisation(page, other):
    assert other not in page.content(), (page.url, other)
    _walk(page)


def test_every_result_states_its_evidence(live_server, browser):
    mine, theirs = "Mine%s" % uuid.uuid4().hex[:6], "Theirs%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("cto")
    other = create_fresh_org("cto")
    ids = _seed(org["org_id"], mine)
    _seed(other["org_id"], theirs)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 900} if os.environ.get("WALK_MODE") == "narrow-dark"
                                  else {"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["cto"])

        # Item 1: each application row states what it rests on; the hand-entered one reads medium.
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        ledger = page.locator('[data-footer-for]:visible, [data-testid^="mobile-card-"]:visible').filter(has_text="%s Ledger" % mine)
        _four_parts(ledger.get_by_test_id("result-footer"))
        expect(ledger.get_by_test_id("result-footer")).to_have_attribute("data-confidence", "medium")
        expect(ledger.get_by_test_id("result-footer-confidence")).to_contain_text("entered by hand")
        expect(ledger.get_by_test_id("result-footer-evidence")).to_contain_text("Owner: Ada Lovelace")
        portal = page.locator('[data-footer-for]:visible, [data-testid^="mobile-card-"]:visible').filter(has_text="%s Portal" % mine)
        expect(portal.get_by_test_id("result-footer-source")).to_contain_text("Record: synced by a connector from Abacus")
        expect(portal.get_by_test_id("result-footer")).to_have_attribute("data-confidence", "low")
        expect(portal.get_by_test_id("result-footer-confidence")).to_contain_text("Owner: none recorded")
        _all_footers(page, 2)
        _no_other_organisation(page, theirs)
        # The evidence link opens the application with its content.
        ledger.get_by_test_id("result-footer-evidence").get_by_role("link", name="%s Ledger" % mine).click()
        page.wait_for_url(lambda url: "/applications/" in url and url.rstrip("/") != live_server + "/applications",
                          timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("%s Ledger" % mine).first).to_be_visible()

        # Item 1: ownership coverage footers its answer and each business unit.
        page.goto(live_server + "/applications/ownership-coverage", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        answer = page.get_by_test_id("coverage-answer-footer").get_by_test_id("result-footer")
        _four_parts(answer)
        unit = page.get_by_test_id("result-footer").filter(has_text="%s Finance" % mine).last
        _four_parts(unit)
        expect(unit.get_by_test_id("result-footer-evidence")).to_contain_text("Applications counted: 2")
        _all_footers(page, 2)
        _no_other_organisation(page, theirs)

        # Item 2: the vendor list and the vendor page.
        page.goto(live_server + "/vendors/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.locator('[data-testid="vendor-row"]').filter(has_text="%s Vendor" % mine)
        _four_parts(row.get_by_test_id("result-footer"))
        expect(row.get_by_test_id("result-footer-evidence")).to_contain_text("%s Ledger" % mine)
        _no_other_organisation(page, theirs)
        page.goto(live_server + "/applications/vendors/%d" % ids["vendor"], wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        supplied = page.locator("#vendor-systems").get_by_test_id("result-footer")
        _four_parts(supplied)
        expect(supplied.get_by_test_id("result-footer-evidence")).to_contain_text("%s Portal" % mine)
        # On the vendor's own page the footer names the record, not the vendor again, and never links back here.
        expect(supplied.get_by_test_id("result-footer-evidence")).to_contain_text("Vendor record")
        assert supplied.get_by_test_id("result-footer-evidence").get_by_text("%s Vendor" % mine).count() == 0
        assert supplied.locator('a[href$="/applications/vendors/%d"]' % ids["vendor"]).count() == 0
        _no_other_organisation(page, theirs)

        # Items 3 and 11: each scored capability states what its score rests on.
        page.goto(live_server + "/strategic/investment-matrix", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        capability = page.get_by_test_id("capability-footer").filter(has_text="%s Billing" % mine).first
        _four_parts(capability.get_by_test_id("result-footer"))
        expect(capability.get_by_test_id("result-footer-confidence")).to_contain_text("Maturity: not assessed")
        _no_other_organisation(page, theirs)

        # Items 4 and 8: the roadmap's counts, plateaus and work packages.
        page.goto(live_server + "/capability-roadmap", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        summary = page.get_by_test_id("roadmap-summary-footer").get_by_test_id("result-footer")
        _four_parts(summary)
        expect(summary.get_by_test_id("result-footer-evidence")).to_contain_text("Work packages: 1")
        plateau = page.get_by_test_id("plateau-footer").first.get_by_test_id("result-footer")
        _four_parts(plateau)
        expect(plateau.get_by_test_id("result-footer-evidence")).to_contain_text("%s Target" % mine)
        wp_row = page.get_by_test_id("roadmap-wp-footer").first.get_by_test_id("result-footer")
        _four_parts(wp_row)
        expect(wp_row).to_have_attribute("data-confidence", "medium")
        expect(wp_row.get_by_test_id("result-footer-confidence")).to_contain_text("entered by hand")
        _no_other_organisation(page, theirs)

        page.goto(live_server + "/implementation/work-packages", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        gap = page.get_by_test_id("critical-gap-row").filter(has_text="%s gap" % mine)
        _four_parts(gap.get_by_test_id("result-footer"))
        _four_parts(page.get_by_test_id("roadmap-footer").get_by_test_id("result-footer"))
        _no_other_organisation(page, theirs)
        page.goto(live_server + "/implementation/work-packages/%d" % ids["wp"], wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        detail = page.get_by_test_id("work-package-detail").get_by_test_id("result-footer")
        _four_parts(detail)
        expect(detail.get_by_test_id("result-footer-evidence")).to_contain_text("Progress: 40%")
        assert detail.get_by_test_id("result-footer-evidence").get_by_role("link", name="%s rollout" % mine).count() == 0
        _no_other_organisation(page, theirs)

        # Impact analysis: pick the element, run it, read the footer under the answer.
        page.goto(live_server + "/strategic/impact-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_label("Type to search...").fill("%s Hub" % mine)
        page.get_by_role("button", name="%s Hub" % mine).first.click()
        page.get_by_role("button", name="Analyze Impact").click()
        result = page.get_by_test_id("impact-footer").get_by_test_id("result-footer")
        _four_parts(result)
        expect(result.get_by_test_id("result-footer-evidence")).to_contain_text("%s Spoke" % mine)
        _no_other_organisation(page, theirs)
    finally:
        context.close()
