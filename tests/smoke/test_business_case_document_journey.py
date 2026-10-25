"""A portfolio manager opens the business case for a work package.

From the sidebar's Changes group they open the list, open the case, and read
it in order: recommendation, problem, options, costs and payback, risks, the
decision. Every figure agrees with the list and with the cost facts, every
link lands on a page with content, the printed page carries the document and
none of the screen furniture, and a second case with nothing in it says what
to add and the link opens the form that does it.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SECTIONS = ["Recommendation", "The problem", "Options compared", "Costs and benefits", "Risks", "Decision"]


def _seed(org_id, user_id):
    from app import create_app, db
    from app.commands.seed_demo_company import (
        _BUSINESS_CASE_RISKS,
        _BUSINESS_CASE_WORK_PACKAGE,
        _seed_business_case,
    )
    from app.models.risk import Risk
    from app.services import work_package_service

    app = create_app("testing")
    with app.app_context():
        work_package_service.create_work_package(
            organization_id=org_id, name=_BUSINESS_CASE_WORK_PACKAGE)
        work_package_service.create_work_package(
            organization_id=org_id, name="Second work package")
        for title in _BUSINESS_CASE_RISKS:
            db.session.add(Risk(title=title, likelihood=4, impact=4, organization_id=org_id,
                                owner="Ivo Reed", mitigation_plan="Second region"))
        db.session.commit()
        _seed_business_case(org_id, user_id)
        db.session.commit()
        db.session.remove()


def _no_sideways_scroll(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def test_portfolio_manager_reads_a_business_case_end_to_end(live_server, browser):
    org = create_fresh_org("portfolio_manager")
    email = org["emails"]["portfolio_manager"]
    _seed(org["org_id"], org["user_ids"]["portfolio_manager"])
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    base = live_server
    sign_in(page, base, email)

    # The role is the one the journey claims.
    from app import create_app, db
    from app.models.user import User
    with create_app("testing").app_context():
        assert db.session.get(User, org["user_ids"]["portfolio_manager"]).enterprise_role == "portfolio_manager"

    # From the sidebar: Changes > Business cases.
    page.goto(base + "/enterprise/implementation/gap-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator('[data-slot="sidebar-subitem"]', has_text="Business cases").click()
    page.wait_for_url(re.compile(r"/business-case/$"), timeout=PAGE_TIMEOUT)

    # The list: work package, cost, payback and decision for the case.
    row = page.locator("[data-testid^='bc-row-']", has_text="Move the event backbone")
    expect(row).to_be_visible()
    assert row.locator("td").nth(1).inner_text().strip() == "Migrate event backbone to cloud"
    list_cost = row.locator("[data-cell=cost]").inner_text().strip()
    assert re.sub(r"\D", "", list_cost) == "87000000", list_cost   # 420,000 + 3 x 150,000, to the cent
    assert row.locator("[data-cell=payback]").inner_text().strip() == "56 months"
    assert "Approved" in row.locator("[data-cell=decision]").inner_text()
    assert "Launch" in row.locator("[data-cell=decision]").inner_text()   # who decided

    # Open the case: the six sections, in order, each with content.
    row.get_by_role("link", name="Move the event backbone").click()
    page.wait_for_url(re.compile(r"/business-case/\d+$"), timeout=PAGE_TIMEOUT)
    document_url = page.url
    text = page.locator("[data-testid=bc-document]").inner_text()
    positions = [text.index(name) for name in SECTIONS]
    assert positions == sorted(positions), positions
    assert page.locator("[data-empty-section]").count() == 0
    recommendation = page.locator("[data-testid=bc-section-recommendation]")
    expect(recommendation).to_contain_text("Managed broker")
    # Figures agree: the list, the headline and the table all say the same.
    headline = recommendation.locator("[data-figure=recommended-three-year-cost]").inner_text().strip()
    assert headline == list_cost
    options = page.locator("[data-testid=bc-options-table]")
    expect(options).to_contain_text("Do nothing")
    expect(options).to_contain_text("Harden the current relay")
    expect(options).to_contain_text("Managed broker")
    costs = page.locator("[data-testid=bc-costs-table]")
    assert costs.locator("[data-figure^=three_year_cost-]").all_inner_texts().count(headline) == 1
    assert "56 months" in recommendation.inner_text()
    assert costs.locator("[data-figure^=payback-]").all_inner_texts() == ["—", "—", "56 months"]
    # Each dash is explained under the table.
    expect(page.locator("[data-testid=bc-section-costs]")).to_contain_text("Nothing is invested")
    expect(page.locator("[data-testid=bc-section-costs]")).to_contain_text("does not cover the running cost")
    expect(page.locator("[data-testid=bc-section-risks]")).to_contain_text("Event Relay single point of failure")
    expect(page.locator("[data-testid=bc-section-risks]")).to_contain_text("Sensor Gateway capacity exceeded")
    decision = page.locator("[data-testid=bc-section-decision]")
    expect(decision).to_contain_text("Approved by")
    expect(decision).to_contain_text("pays back inside two years")

    # The work package link lands on the work package, and back returns to the case.
    page.locator("[data-testid=bc-work-package-link]").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator("body")).to_contain_text("Migrate event backbone to cloud")
    expect(page.locator("body")).not_to_contain_text("Page not found")
    page.go_back()
    page.wait_for_url(document_url, timeout=PAGE_TIMEOUT)
    expect(page.locator("[data-testid=bc-document]")).to_be_visible()

    # A risk link lands on that risk.
    page.locator("[data-testid=bc-risks-table] a", has_text="Event Relay single point").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator("body")).to_contain_text("Event Relay single point of failure")
    expect(page.locator("body")).not_to_contain_text("Page not found")
    page.goto(document_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # Printed: the document stays; the sidebar, header and buttons go.
    page.emulate_media(media="print")
    expect(page.locator("[data-testid=bc-section-recommendation]")).to_be_visible()
    expect(page.locator("[data-testid=bc-options-table]")).to_be_visible()
    expect(page.locator("[data-testid=bc-section-decision]")).to_be_visible()
    expect(page.locator("#admin-sidebar")).to_be_hidden()
    expect(page.get_by_test_id("bc-print")).to_be_hidden()
    pdf = page.pdf(format="A4")
    assert pdf.startswith(b"%PDF") and len(pdf) > 5000
    page.emulate_media(media="screen")

    # Phone width: the document does not scroll sideways.
    page.set_viewport_size({"width": 390, "height": 900})
    page.goto(document_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _no_sideways_scroll(page)
    page.set_viewport_size({"width": 1440, "height": 1000})

    # Create a second case linked to the other work package; it is empty and says what to add.
    page.goto(base + "/business-case/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="New business case").click()
    title = "Second case %s" % uuid.uuid4().hex[:5]
    page.fill("#bc-title", title)
    page.select_option("#bc-work-package", label="Second work package")
    page.get_by_test_id("bc-create-submit").click()
    page.wait_for_url(re.compile(r"/business-case/\d+$"), timeout=PAGE_TIMEOUT)
    expect(page.locator("[data-testid=bc-work-package-link]")).to_have_text("Second work package")
    empties = page.locator("[data-empty-section]")
    assert empties.count() >= 5
    expect(page.locator("[data-testid=bc-section-recommendation]")).to_contain_text("No option is recommended yet")
    assert "—" not in page.locator("[data-testid=bc-section-costs]").inner_text()

    # Its link opens the form, and what is written there is on the document after a reload.
    page.locator("[data-testid=bc-section-problem] a", has_text="Write the problem").click()
    page.wait_for_url(re.compile(r"/edit$"), timeout=PAGE_TIMEOUT)
    page.fill("#bc-problem", "Spreadsheets hide the real cost of each change.")
    page.fill("#bc-option-0-name", "Buy a planning tool")
    page.fill("#bc-option-0-one-off", "12000")
    page.fill("#bc-option-0-run", "2000")
    page.fill("#bc-option-0-benefit", "10000")
    page.locator("input[name=recommended][value='0']").check()
    page.get_by_role("button", name="Save business case").click()
    page.wait_for_url(re.compile(r"/business-case/\d+$"), timeout=PAGE_TIMEOUT)
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("[data-testid=bc-section-problem]")).to_contain_text("Spreadsheets hide the real cost")
    recommendation = page.locator("[data-testid=bc-section-recommendation]")
    expect(recommendation).to_contain_text("Buy a planning tool")
    assert "18 months" in recommendation.inner_text()   # 12000 / (10000 - 2000) * 12
    context.close()
