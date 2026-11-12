"""An enterprise architect types the three addresses a buyer knows by name, reads the consolidation and
recommendation screens of a model that holds applications, and compares impact scenarios.

Written from the goal "every address I type or button I press leads somewhere that tells me the truth":

* /opportunities, /recommendations and /simulations each land on a page with a heading and a way to join
  the waiting list, with the switch off and with the first release on;
* Architecture Intelligence in a model holding applications names how many it holds and does not ask for
  more mapping data; in an empty organisation it says the model holds none;
* the consolidation opportunities screen says why it is empty and its link opens the candidates page;
* Compare Scenarios opens a dialog with a title.

Screenshots for the walk are written when WALK_DIR is set.
"""

import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

NAMED = (
    ("/opportunities", "Opportunities"),
    ("/recommendations", "Recommendations"),
    ("/simulations", "Simulation scenarios"),
)
APPLICATIONS = 3
# The addresses a signed-in person opens as pages, with the sentence an organisation holding no records reads there.
SIGNED_IN_PAGES = {
    "/opportunities": "No opportunity has been found",
    "/recommendations": "Nothing to recommend yet",
}


@pytest.fixture(scope="module", params=["", "1"], ids=["switch-off", "wave-1"])
def server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": request.param})


def _seed(org_id, suffix):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        for i in range(APPLICATIONS):
            db.session.add(ApplicationComponent(name="Ledger %s %d" % (suffix, i), organization_id=org_id))
        db.session.commit()
        db.session.remove()


def _seed_opportunity(org_id, suffix):
    """One approved opportunity consolidating into a real application of the organisation; returns both ids."""
    from datetime import date

    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.consolidation import ConsolidationOpportunity

    app = create_app("testing")
    with app.app_context():
        target = ApplicationComponent(name="Target %s" % suffix, organization_id=org_id)
        db.session.add(target)
        db.session.flush()
        opportunity = ConsolidationOpportunity(
            name="Merge ledgers %s" % suffix, organization_id=org_id, status="approved", target_application_id=target.id,
            estimated_annual_savings=120000.0, business_impact="Fewer licences %s" % suffix,
            target_completion_date=date(date.today().year, 1, 1),
        )
        db.session.add(opportunity)
        db.session.commit()
        ids = (opportunity.id, target.id)
        db.session.remove()
    return ids


def _drop_opportunity(opportunity_id, target_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.consolidation import ConsolidationOpportunity

    app = create_app("testing")
    with app.app_context():
        ConsolidationOpportunity.query.filter_by(id=opportunity_id).delete()
        ApplicationComponent.query.filter_by(id=target_id).delete()
        db.session.commit()
        db.session.remove()


def _shot(page, name):
    walk = os.environ.get("WALK_DIR")
    if walk:
        page.screenshot(path=os.path.join(walk, name + ".png"), full_page=True)


def _context(browser, width=1440, dark=False):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1000},
                                  color_scheme="dark" if dark else "light")
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context


def test_named_addresses_land_on_a_page_with_a_next_step(browser, server, request):
    # With the switch off, /simulations is the simulation screen itself (tests/smoke/test_simulation_journey.py);
    # under the first release it is still the waiting list.
    switch_off = not request.node.callspec.params["server"]
    named = NAMED[:2] if switch_off else NAMED
    mine = create_fresh_org("enterprise_architect")
    context = _context(browser)
    page = context.new_page()
    sign_in(page, server, mine["emails"]["enterprise_architect"])
    for path, title in named:
        response = page.goto(server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200, "%s answered %s" % (path, response.status)
        expect(page.get_by_role("heading", level=1)).to_have_text(title)
        assert "couldn't find that page" not in page.locator("body").inner_text().lower()
        if path in SIGNED_IN_PAGES and (switch_off or path == "/recommendations"):
            # Open to a signed-in person (recommendations from the first release, opportunities with the
            # switch off): the page itself, never the waiting list; this organisation holds no
            # records, so the page says why it is empty and what to do next. Opportunities under the first
            # release are still the waiting list, below.
            expect(page.get_by_test_id("early-access-page")).to_have_count(0)
            main = page.locator("main").inner_text()
            assert "waiting list" not in main.lower(), (path, main[-300:])
            assert SIGNED_IN_PAGES[path] in main, (path, main[-600:])
            continue
        expect(page.get_by_test_id("early-access-page")).to_contain_text(title)
        expect(page.get_by_test_id("early-access-submit")).to_be_visible()
    if not switch_off:
        mobile = _context(browser, 390, dark=True).new_page()
        sign_in(mobile, server, mine["emails"]["enterprise_architect"])
        mobile.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _shot(mobile, "named-simulations-390-dark")
    page.goto(server + "/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _shot(page, "named-opportunities-1440-light")
    context.close()


def test_recommendations_and_consolidation_say_the_real_reason(browser, server):
    suffix = uuid.uuid4().hex[:6]
    mine = create_fresh_org("enterprise_architect")
    empty = create_fresh_org("enterprise_architect")
    _seed(mine["org_id"], suffix)
    page = _context(browser).new_page()
    sign_in(page, server, mine["emails"]["enterprise_architect"])
    page.goto(server + "/enterprise/analysis/ai-architecture", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    if page.get_by_test_id("early-access-page").count():
        # the first release closes this screen: it says so and offers the waiting list
        expect(page.get_by_test_id("early-access-submit")).to_be_visible()
        assert page.get_by_role("heading", level=1).inner_text().strip()
        return
    body = page.locator("main").inner_text()
    assert "more application mapping data" not in body
    assert "Your model holds %d applications" % APPLICATIONS in body, body[-600:]
    for testid, label in (
        ("ai-analysis-recommendations", "Total Recommendations"),
        ("ai-analysis-gaps-critical", "Open Gaps (Critical)"),
        ("ai-analysis-gaps-high", "Open Gaps (High)"),
        ("ai-analysis-apps-review", "Apps Under Review"),
    ):
        card = page.get_by_test_id(testid)
        expect(card).to_contain_text(label)
        assert page.get_by_test_id(testid + "-value").inner_text().strip().isdigit(), label
    reviewed = int(page.get_by_test_id("ai-analysis-apps-review-value").inner_text())
    assert page.get_by_role("heading", name="Applications Under Review").count() == 1
    assert page.locator("main a[href*='/applications/']").count() >= reviewed
    _shot(page, "recommendations-1440-light")
    phone = _context(browser, 390, dark=True).new_page()
    sign_in(phone, server, mine["emails"]["enterprise_architect"])
    phone.goto(server + "/enterprise/analysis/ai-architecture", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(phone.get_by_test_id("ai-analysis-apps-review")).to_be_visible()
    _shot(phone, "recommendations-390-dark")

    page.goto(server + "/enterprise/consolidation/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    if "/enterprise/consolidation/opportunities" not in page.url:
        # the first release moves this address to the consolidation list
        assert page.locator("h1, h2").first.inner_text().strip()
        return
    expect(page.locator("main")).to_contain_text("No consolidation opportunity has been recorded")
    _shot(page, "opportunities-empty-1440-light")
    phone.goto(server + "/enterprise/consolidation/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _shot(phone, "opportunities-empty-390-dark")
    page.get_by_role("link", name="Review consolidation candidates").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_role("heading", name="Consolidation Candidates").first).to_be_visible()
    assert page.locator("main").inner_text().strip()
    expect(page.locator("main")).to_contain_text("No consolidation candidate has been found")
    expect(page.get_by_role("button", name="Find consolidation candidates")).to_be_visible()

    other = _context(browser).new_page()
    sign_in(other, server, empty["emails"]["enterprise_architect"])
    other.goto(server + "/enterprise/analysis/ai-architecture", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    text = other.locator("main").inner_text()
    assert "Your model holds no applications" in text and "Add applications" in text


def test_compare_scenarios_opens_a_dialog(browser, server):
    mine = create_fresh_org("enterprise_architect")
    page = _context(browser).new_page()
    sign_in(page, server, mine["emails"]["enterprise_architect"])
    page.goto(server + "/strategic/impact-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    button = page.get_by_role("button", name="Compare Scenarios")
    if button.count() == 0:
        # the first release closes this screen: it says so and offers the waiting list
        expect(page.get_by_test_id("early-access-submit")).to_be_visible()
        assert page.get_by_role("heading", level=1).inner_text().strip()
        return
    button.click()
    expect(page.get_by_role("heading", name="Compare Impact Scenarios")).to_be_visible(timeout=PAGE_TIMEOUT)
    dialog = page.locator("[aria-labelledby='impact-scenario-comparison-title']")
    expect(dialog).to_contain_text("Select Scenarios to Compare")
    _shot(page, "compare-scenarios-1440-light")


def test_consolidation_opportunities_list_agrees_with_its_savings_and_links_to_the_application(browser, server):
    suffix = uuid.uuid4().hex[:6]
    mine = create_fresh_org("portfolio_manager")
    opportunity_id, target_id = _seed_opportunity(mine["org_id"], suffix)
    try:
        page = _context(browser).new_page()
        sign_in(page, server, mine["emails"]["portfolio_manager"])
        page.goto(server + "/enterprise/consolidation/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        if page.get_by_test_id("early-access-page").count():
            # the first release closes this screen: it says so and offers the waiting list
            expect(page.get_by_test_id("early-access-submit")).to_be_visible()
            assert page.get_by_role("heading", level=1).inner_text().strip()
            return
        row = page.get_by_test_id("opportunity-row").filter(has_text="Merge ledgers %s" % suffix)
        expect(row).to_have_count(1)
        expect(row).to_contain_text("Approved")
        headers = [h.strip() for h in page.get_by_test_id("opportunities-table").locator("thead th").all_inner_texts()]
        assert headers == ["Opportunity", "Consolidate into", "Status", "Impact"], headers
        expect(row).to_contain_text("Fewer licences %s" % suffix)
        # the savings figures count this opportunity, and each is a currency amount
        figures = page.locator("[data-testid^='opportunities-savings-'][data-testid$='-value']")
        assert figures.count() >= 1
        for i in range(figures.count()):
            assert any(ch.isdigit() for ch in figures.nth(i).inner_text()), figures.nth(i).inner_text()
        expect(page.get_by_test_id("opportunities-savings-row-1")).to_contain_text("From 1 opportunity")
        _shot(page, "opportunities-list-1440-light")
        phone = _context(browser, 390, dark=True).new_page()
        sign_in(phone, server, mine["emails"]["portfolio_manager"])
        phone.goto(server + "/enterprise/consolidation/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(phone.get_by_test_id("opportunities-table")).to_be_visible()
        _shot(phone, "opportunities-list-390-dark")
        row.get_by_role("link", name="Target %s" % suffix).click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("main")).to_contain_text("Target %s" % suffix)
    finally:
        _drop_opportunity(opportunity_id, target_id)


def _seed_named(org_id, names, shared_capability=None):
    """Applications for an organisation; with ``shared_capability`` they all support one capability, which is
    what makes the candidates pair them (a pair needs a shared capability, a shared function or a near-identical name)."""
    from app import create_app, db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    app = create_app("testing")
    with app.app_context():
        apps = [ApplicationComponent(name=name, organization_id=org_id) for name in names]
        db.session.add_all(apps)
        db.session.flush()
        if shared_capability:
            capability = BusinessCapability(name=shared_capability, organization_id=org_id, level=2,
                                            code="C" + uuid.uuid4().hex[:10])
            db.session.add(capability)
            db.session.flush()
            for application in apps:
                db.session.add(ApplicationCapabilityMapping(
                    organization_id=org_id, application_component_id=application.id,
                    business_capability_id=capability.id, is_active=True))
        db.session.commit()
        db.session.remove()


def _drop_candidates(org_id):
    """Remove only the candidates pairing this organisation's applications."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.consolidation import ConsolidationCandidate

    app = create_app("testing")
    with app.app_context():
        ids = [a.id for a in ApplicationComponent.query.filter_by(organization_id=org_id).all()]
        if ids:
            ConsolidationCandidate.query.filter(ConsolidationCandidate.primary_application_id.in_(ids)).delete(synchronize_session=False)
            db.session.commit()
        db.session.remove()


def test_empty_consolidation_candidates_say_why_and_the_analysis_fills_them(browser, server, request):
    """A portfolio manager opens candidates before any analysis: the page says why it is empty and offers the
    step that creates candidates; pressing it lands back on the list with the pairs it found, each linking to a
    real application; an organisation with one application is told why nothing can be compared."""
    suffix = uuid.uuid4().hex[:6]
    mine = create_fresh_org("enterprise_architect")
    alone = create_fresh_org("enterprise_architect")
    request.addfinalizer(lambda: _drop_candidates(mine["org_id"]))
    _seed_named(mine["org_id"], ["General Ledger %s" % suffix, "General Ledger %s Cloud" % suffix],
                shared_capability="Financial accounting %s" % suffix)
    _seed_named(alone["org_id"], ["Only application %s" % suffix])

    page = _context(browser).new_page()
    sign_in(page, server, mine["emails"]["enterprise_architect"])
    page.goto(server + "/enterprise/consolidation/candidates", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    if page.get_by_test_id("early-access-page").count():
        # the first release closes this screen: it says so and offers the waiting list
        expect(page.get_by_test_id("early-access-submit")).to_be_visible()
        assert page.get_by_role("heading", level=1).inner_text().strip()
        return
    expect(page.get_by_role("heading", name="Consolidation Candidates").first).to_be_visible()
    main = page.locator("main")
    expect(main).to_contain_text("No consolidation candidate has been found")
    expect(main).to_contain_text("similar names")
    assert "No consolidation candidates found." not in main.inner_text()
    _shot(page, "candidates-empty-1440-light")
    phone = _context(browser, 390, dark=True).new_page()
    sign_in(phone, server, mine["emails"]["enterprise_architect"])
    phone.goto(server + "/enterprise/consolidation/candidates", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(phone.get_by_role("button", name="Find consolidation candidates")).to_be_visible()
    _shot(phone, "candidates-empty-390-dark")

    page.get_by_role("button", name="Find consolidation candidates").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_role("heading", name="Consolidation Candidates").first).to_be_visible()
    rows = page.get_by_test_id("candidate-row")
    expect(rows.first).to_be_visible()
    expect(page.get_by_test_id("candidates-table")).to_contain_text("General Ledger %s" % suffix)
    link = rows.first.get_by_role("link").first
    name = link.inner_text().strip()
    link.click()
    page.wait_for_load_state("domcontentloaded")
    assert "/applications/" in page.url, page.url
    assert name in page.locator("main").inner_text()

    _drop_candidates(mine["org_id"])
    lone = _context(browser).new_page()
    sign_in(lone, server, alone["emails"]["enterprise_architect"])
    lone.goto(server + "/enterprise/consolidation/candidates", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    lone.get_by_role("button", name="Find consolidation candidates").click()
    lone.wait_for_load_state("domcontentloaded")
    expect(lone.locator("body")).to_contain_text("at least two applications")
    expect(lone.get_by_role("heading", name="Consolidation Candidates").first).to_be_visible()
    _shot(lone, "candidates-one-application-1440-light")


def _open_candidates(browser, server, email, width=1440, dark=False):
    page = _context(browser, width, dark).new_page()
    sign_in(page, server, email)
    page.goto(server + "/enterprise/consolidation/candidates", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return page


def test_a_reader_is_not_offered_the_comparison_and_is_told_who_can_run_it(browser, server, request):
    """A portfolio manager who only reads opens candidates in an organisation with applications: the action
    is shown disabled with a sentence naming who can run it, and nothing on the page invites them to press a
    button that would answer 403. An architect in the same organisation still gets the working button."""
    suffix = uuid.uuid4().hex[:6]
    mine = create_fresh_org("enterprise_architect")
    reader = create_fresh_org("portfolio_manager", platform_role="Viewer")
    other = create_fresh_org("enterprise_architect")
    request.addfinalizer(lambda: _drop_candidates(mine["org_id"]))
    request.addfinalizer(lambda: _drop_candidates(reader["org_id"]))
    _seed_named(mine["org_id"], ["Payroll %s" % suffix, "Payroll %s Cloud" % suffix])
    _seed_named(reader["org_id"], ["Payroll %s" % suffix, "Payroll %s Cloud" % suffix])
    _seed_named(other["org_id"], ["Unrelated %s" % suffix])

    page = _open_candidates(browser, server, reader["emails"]["portfolio_manager"])
    if page.get_by_test_id("early-access-page").count():
        return
    expect(page.get_by_role("heading", name="Consolidation Candidates").first).to_be_visible()
    main = page.locator("main")
    action = main.get_by_test_id("disabled-action")
    expect(action).to_be_visible()
    expect(action.get_by_role("button", name="Find consolidation candidates")).to_be_disabled()
    expect(action).to_contain_text("architect")
    assert main.locator("form[action$='/consolidation/candidates/find']").count() == 0
    _shot(page, "candidates-reader-empty-1440-light")
    phone = _open_candidates(browser, server, reader["emails"]["portfolio_manager"], 390, True)
    expect(phone.get_by_test_id("disabled-action")).to_be_visible()
    _shot(phone, "candidates-reader-empty-390-dark")

    creator = _open_candidates(browser, server, mine["emails"]["enterprise_architect"])
    expect(creator.get_by_role("button", name="Find consolidation candidates")).to_be_enabled()
    assert creator.locator("main").get_by_test_id("disabled-action").count() == 0
    _shot(creator, "candidates-architect-empty-1440-light")


def test_an_organisation_with_fewer_than_two_applications_is_told_to_add_applications(browser, server, request):
    """With no application to compare the empty page does not say 'Run the comparison'. It says at least two
    applications are needed and links to the applications list, which opens with content."""
    suffix = uuid.uuid4().hex[:6]
    bare = create_fresh_org("enterprise_architect")
    request.addfinalizer(lambda: _drop_candidates(bare["org_id"]))

    page = _open_candidates(browser, server, bare["emails"]["enterprise_architect"])
    if page.get_by_test_id("early-access-page").count():
        return
    main = page.locator("main")
    expect(main).to_contain_text("at least two applications")
    assert "Run the comparison" not in main.inner_text()
    _shot(page, "candidates-no-applications-1440-light")
    phone = _open_candidates(browser, server, bare["emails"]["enterprise_architect"], 390, True)
    expect(phone.locator("main")).to_contain_text("at least two applications")
    _shot(phone, "candidates-no-applications-390-dark")
    link = main.get_by_role("link", name="Add applications")
    expect(link).to_be_visible()
    link.click()
    page.wait_for_load_state("domcontentloaded")
    assert "/applications" in page.url, page.url
    expect(page.get_by_role("heading", level=1).first).to_be_visible()
    assert page.locator("main").inner_text().strip()
    _seed_named(bare["org_id"], ["First %s" % suffix, "Second %s" % suffix])
    again = _open_candidates(browser, server, bare["emails"]["enterprise_architect"])
    assert "at least two applications" not in again.locator("main").inner_text()
    expect(again.locator("main")).to_contain_text("Run the comparison")


def test_the_empty_consolidation_opportunities_page_offers_its_next_step_once(browser, server):
    empty = create_fresh_org("enterprise_architect")
    page = _context(browser).new_page()
    sign_in(page, server, empty["emails"]["enterprise_architect"])
    page.goto(server + "/enterprise/consolidation/opportunities", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    if page.get_by_test_id("early-access-page").count():
        # the first release closes this screen: it says so and offers the waiting list
        expect(page.get_by_test_id("early-access-submit")).to_be_visible()
        return
    expect(page.locator("main")).to_contain_text("No opportunity has been found")
    links = page.locator("main").get_by_role("link", name="Review consolidation candidates")
    assert links.count() == 1, links.count()
    links.first.click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_role("heading", name="Consolidation Candidates").first).to_be_visible()
