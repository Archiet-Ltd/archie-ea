"""A chief technology officer reads ownership coverage and opens each figure's evidence.

"With a named owner: N" opens exactly those N applications of the unit, "Without an owner: M" the rest of the
unit, and the "No business unit" row opens the applications that have no unit. A second organisation holds a
unit of the same name with its own applications; none of them may appear. At phone width the per-unit footer
stays inside the table's card. Then a work package refuses an end date before its start date, beside the field.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id, noun, unit):
    """Unit with 2 owned + 1 unowned (one stored in other case with spaces, one decommissioned and owned),
    a second unit with one unowned application, and two applications with no unit (one owned)."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.enterprise_intelligence import OrganizationUnit

    app = create_app("testing")
    with app.app_context():
        db.session.add_all([OrganizationUnit(name=unit, organization_id=org_id),
                            OrganizationUnit(name="%s Retail" % noun, organization_id=org_id)])

        def make(name, domain, owner, status="active"):
            row = ApplicationComponent(name="%s %s" % (noun, name), organization_id=org_id, business_domain=domain,
                                       lifecycle_status=status)
            if owner:
                row.legacy_business_owner = "Ada Lovelace"
            db.session.add(row)

        make("Ledger", unit, True)
        make("Payroll", "  %s  " % unit.upper(), True)
        make("Archive", unit, True, "5. DECOMMISSIONED")
        make("Portal", unit, False)
        make("Till", "%s Retail" % noun, False)
        make("Orphan Owned", None, True)
        make("Orphan Bare", None, False)
        db.session.commit()
        db.session.remove()


def _rows(page):
    return page.locator('[data-testid^="app-row-"]:visible')


def _open(page, footer, link_text, expected_names, noun, other):
    link = footer.get_by_test_id("result-footer-evidence").get_by_role("link", name=link_text)
    link.click()
    page.wait_for_url(lambda url: "/ownership-coverage" not in url, timeout=PAGE_TIMEOUT)
    expect(_rows(page).first).to_be_visible(timeout=PAGE_TIMEOUT)
    names = ["%s %s" % (noun, n) for n in expected_names]
    listed = [row.inner_text() for row in _rows(page).all()]
    # Count the application names themselves: a name is one row, and the list holds no other application of ours.
    found = [n for n in names if sum(1 for text in listed if n in text) == 1]
    assert found == names, (link_text, names, listed)
    others = [t for t in listed if noun in t and not any(n in t for n in names)]
    assert not others, (link_text, others)
    assert len(_rows(page).all()) == len(names), (link_text, len(_rows(page).all()))
    assert other not in page.content()


def test_each_coverage_figure_opens_the_applications_it_counts(live_server, browser):
    mine, theirs = "Mine%s" % uuid.uuid4().hex[:6], "Theirs%s" % uuid.uuid4().hex[:6]
    unit = "%s Finance" % mine
    org, other = create_fresh_org("cto"), create_fresh_org("cto")
    _seed(org["org_id"], mine, unit)
    # The other organisation names its unit the same and owns applications under it.
    _seed(other["org_id"], theirs, unit)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["cto"])
        coverage = live_server + "/applications/ownership-coverage"

        def footer_of(label):
            page.goto(coverage, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            return page.get_by_test_id("result-footer").filter(has_text="Business unit: %s" % label).last

        unit_footer = footer_of(unit)
        expect(unit_footer.get_by_test_id("result-footer-evidence")).to_contain_text("Applications counted: 4")
        expect(unit_footer.get_by_test_id("result-footer-evidence")).to_contain_text("With a named owner: 3")
        expect(unit_footer.get_by_test_id("result-footer-evidence")).to_contain_text("Without an owner: 1")

        _open(page, unit_footer, "With a named owner: 3", ["Ledger", "Payroll", "Archive"], mine, theirs)
        _open(page, footer_of(unit), "Without an owner: 1", ["Portal"], mine, theirs)
        _open(page, footer_of(unit), "Applications counted: 4", ["Ledger", "Payroll", "Archive", "Portal"], mine, theirs)

        orphan = footer_of("No business unit")
        expect(orphan.get_by_test_id("result-footer-evidence")).to_contain_text("Applications counted: 2")
        _open(page, orphan, "Applications counted: 2", ["Orphan Owned", "Orphan Bare"], mine, theirs)
        _open(page, footer_of("No business unit"), "With a named owner: 1", ["Orphan Owned"], mine, theirs)
        _open(page, footer_of("No business unit"), "Without an owner: 1", ["Orphan Bare"], mine, theirs)
    finally:
        context.close()


def test_the_unit_footer_stays_inside_its_card_on_a_phone(live_server, browser):
    mine = "Mine%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("cto")
    _seed(org["org_id"], mine, "%s Finance" % mine)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["cto"])
        page.goto(live_server + "/applications/ownership-coverage", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        footer = page.get_by_test_id("result-footer").filter(has_text="Business unit: %s Finance" % mine).last
        expect(footer).to_be_visible(timeout=PAGE_TIMEOUT)
        box = footer.bounding_box()
        card = page.locator('[data-slot="card"]').filter(has=footer).last.bounding_box()
        assert box["x"] >= card["x"] - 1 and box["x"] + box["width"] <= card["x"] + card["width"] + 1, (box, card)
        table = page.locator("table").first.bounding_box()
        assert page.evaluate("document.documentElement.scrollWidth") <= 390
        assert table["x"] + table["width"] <= card["x"] + card["width"] + 1, ("table wider than its card", table, card)
    finally:
        context.close()


def test_a_work_package_refuses_an_end_before_its_start_beside_the_field(live_server, browser):
    org = create_fresh_org("cto")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    name = "Dates%s" % uuid.uuid4().hex[:6]
    try:
        sign_in(page, live_server, org["emails"]["cto"])
        page.goto(live_server + "/implementation/work-packages/create", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        page.get_by_label("Work Package Name").fill(name)
        page.locator("#start_date").fill("2026-12-10")
        page.locator("#end_date").fill("2026-12-01")
        page.get_by_role("button", name="Create Work Package").click()
        error = page.locator("#end_date-error")
        expect(error).to_contain_text("End date must not be before the start date", timeout=PAGE_TIMEOUT)
        # Nothing was saved, and what was typed is still there to correct.
        expect(page.get_by_label("Work Package Name")).to_have_value(name)
        expect(page.locator("#start_date")).to_have_value("2026-12-10")
        page.locator("#end_date").fill("2026-12-20")
        page.get_by_role("button", name="Create Work Package").click()
        page.wait_for_url(lambda url: "/edit" in url, timeout=PAGE_TIMEOUT)
        edit_url = page.url
        page.goto(edit_url, wait_until="domcontentloaded")
        expect(page.locator("#end_date")).to_have_value("2026-12-20")
        expect(page.locator("#start_date")).to_have_value("2026-12-10")
        # An existing package refuses the same change.
        page.locator("#end_date").fill("2026-12-02")
        page.get_by_role("button", name="Edit Work Package").click()
        expect(page.locator("#end_date-error")).to_contain_text("End date must not be before the start date",
                                                                timeout=PAGE_TIMEOUT)
        page.goto(edit_url, wait_until="domcontentloaded")
        expect(page.locator("#end_date")).to_have_value("2026-12-20")
    finally:
        context.close()
