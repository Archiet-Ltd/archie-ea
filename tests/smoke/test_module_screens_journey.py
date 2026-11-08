"""The screens the module pictures show, walked by the person each one is for, in an organisation with linked data.

One journey per screen, each from what the person needs to read: the board's figures and where its pending
reviews are, which contracts renew and when, the vendors in the order they come up for renewal with nothing
unset printed as a word, a business case whose labels name their currency and whose sections say they are
empty only when they are, the portfolio's line about objectives, and the compliance findings control by control.
A second organisation holds records that must not change any figure.
"""
import datetime
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("SMOKE_WALK_DIR")
TODAY = datetime.date.today()


def _page(browser, live_server, org, role, width=1440):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, org["emails"][role])
    return page


def _shot(page, name):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%d.png" % (name, page.viewport_size["width"])), full_page=True)


def _words(locator):
    return " ".join(locator.inner_text().split())


# ── the board ──────────────────────────────────────────────────────────────────────────────────────────────────

def _reviews(org_id, submitter_id, noun, statuses):
    from app import create_app, db
    from app.models.architecture_review_board import ARBReviewItem

    with create_app("testing").app_context():
        titles = []
        for number, status in enumerate(statuses):
            title = "%s %s review %d" % (noun, status.replace("_", " "), number)
            db.session.add(ARBReviewItem(review_number="%s-%d" % (noun.upper(), number), title=title,
                                         review_type="architecture_change", status=status, priority="medium",
                                         organization_id=org_id, submitter_id=submitter_id))
            titles.append(title)
        db.session.commit()
        db.session.remove()
    return titles


def test_the_board_reads_its_approval_rate_from_decided_reviews_and_says_where_the_pending_ones_are(
        live_server, browser):
    noun = "Board%s" % uuid.uuid4().hex[:6]
    org, other = create_fresh_org("enterprise_architect"), create_fresh_org("enterprise_architect")
    titles = _reviews(org["org_id"], org["user_ids"]["enterprise_architect"], noun, ["approved", "approved", "approved", "rejected", "submitted", "submitted",
                                            "under_review"])
    _reviews(other["org_id"], other["user_ids"]["enterprise_architect"], "Other" + noun, ["rejected"] * 5)
    page = _page(browser, live_server, org, "enterprise_architect")

    page.goto(live_server + "/arb/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    figures = _words(page.get_by_test_id("figure-cards"))
    assert "Total reviews 7" in figures and "Pending 3" in figures, figures
    # three approved of the four decided: 75.0%, not three of all seven, and the other board's five rejections stay out
    assert "Approval rate 75.0%" in figures, figures
    line = _words(page.get_by_test_id("arb-outside-queue"))
    assert line.startswith("3 of the 3 pending reviews are not yet in a review cycle, so they are not listed below."), line
    _shot(page, "board")

    page.get_by_test_id("arb-outside-queue").get_by_role("link", name="Open the full review ledger").click()
    page.wait_for_url(re.compile(r"/arb/reviews"), timeout=PAGE_TIMEOUT)
    ledger = page.locator("main").inner_text()
    for title in titles:
        if "submitted" in title or "under review" in title:
            assert title in ledger, title
    assert "Other" + noun not in ledger


def test_a_board_with_no_decisions_shows_a_dash_for_its_rate_and_no_outside_queue_line(live_server, browser):
    noun = "Quiet%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    page = _page(browser, live_server, org, "enterprise_architect")

    page.goto(live_server + "/arb/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    assert "Approval rate —" in _words(page.get_by_test_id("figure-cards")), noun
    assert page.get_by_test_id("arb-outside-queue").count() == 0


# ── the renewals and the vendors ───────────────────────────────────────────────────────────────────────────────

# (name, renewal date offset, end date offset): a renewal on each edge of the 30 and 90 day bands, one just past
# the 90 day edge, a contract whose renewal date passed 20 days ago but which runs another 160, and a far one.
CONTRACTS = (("edge30", 30, 213), ("edge90", 90, 273), ("past90", 91, 274), ("renewed", -20, 160), ("far", 400, 583))


def _vendors(org_id, noun):
    """One vendor, system and contract for each of CONTRACTS, and a vendor with a system but no contract."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    made = {}
    with create_app("testing").app_context():
        for name, renews, ends in CONTRACTS + (("nodeal", None, None),):
            vendor = VendorOrganization(name="%s %s" % (noun, name))
            db.session.add(vendor)
            db.session.flush()
            system = ApplicationComponent(name="%s %s system" % (noun, name), organization_id=org_id,
                                          lifecycle_status="operational", vendor_organization_id=vendor.id)
            db.session.add(system)
            db.session.flush()
            contract = None
            if renews is not None:
                contract = VendorContract(
                    contract_name="%s %s contract" % (noun, name), organization_id=org_id, vendor_id=vendor.id,
                    application_id=system.id, annual_cost=1000.0, status="active",
                    start_date=TODAY - datetime.timedelta(days=90),
                    renewal_date=TODAY + datetime.timedelta(days=renews),
                    end_date=TODAY + datetime.timedelta(days=ends))
                db.session.add(contract)
            db.session.commit()
            made[name] = {"vendor": vendor.id, "contract": contract.id if contract else None}
        db.session.remove()
    return made


def _lapsed(org_id, noun, vendor_id):
    """An active contract whose end date passed five days ago, on the vendor of the far contract."""
    from app import create_app, db
    from app.models.application_portfolio import VendorContract

    with create_app("testing").app_context():
        db.session.add(VendorContract(
            contract_name="%s lapsed contract" % noun, organization_id=org_id, vendor_id=vendor_id, annual_cost=500.0,
            status="active", start_date=TODAY - datetime.timedelta(days=400),
            renewal_date=TODAY - datetime.timedelta(days=30), end_date=TODAY - datetime.timedelta(days=5)))
        db.session.commit()
        db.session.remove()


def test_the_renewals_tiles_their_lists_and_the_contracts_behind_them_agree(live_server, browser):
    noun = "Renew%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect", extra_roles=("procurement",))
    other = create_fresh_org("enterprise_architect")
    made = _vendors(org["org_id"], noun)
    _vendors(other["org_id"], "Other" + noun)
    _lapsed(org["org_id"], noun, made["far"]["vendor"])
    page = _page(browser, live_server, org, "procurement")

    page.goto(live_server + "/procurement/renewals", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    def tile(testid):
        return _words(page.get_by_test_id(testid))

    # 30 days: the contract renewing on the 30th day; 90: that and the one on the 90th; 180: those, the one on
    # day 91 and the one whose renewal date passed but which runs 160 more days; the far one is in none of them
    assert re.search(r"\b1 Renew within 30 days", tile("renewals-critical")), tile("renewals-critical")
    assert re.search(r"\b2 Renew within 90 days", tile("renewals-warning")), tile("renewals-warning")
    assert re.search(r"\b4 Renew within 180 days", tile("renewals-upcoming")), tile("renewals-upcoming")
    _shot(page, "renewals")

    page.get_by_test_id("renewals-warning").click()
    page.wait_for_url(re.compile(r"days=90"), timeout=PAGE_TIMEOUT)
    rows = page.get_by_test_id("renewal-row")
    assert rows.count() == 2
    text = rows.all_inner_texts()
    assert "%s edge30 contract" % noun in text[0] and "%s edge90 contract" % noun in text[1]
    assert "Other" + noun not in page.locator("main").inner_text()

    page.goto(live_server + "/procurement/renewals?days=180", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    listed = page.get_by_test_id("renewal-row")
    assert listed.count() == 4
    renewed_row = listed.filter(has_text="renewed contract")
    cells = renewed_row.locator("td")
    # the renewal date passed, so the contract's next date is its end date: shown as that date, with its own status
    assert _words(cells.nth(3)) == (TODAY + datetime.timedelta(days=160)).strftime("%d %b %Y"), _words(cells.nth(3))
    assert _words(cells.nth(4)) == "In 160 days", _words(cells.nth(4))

    # a lapsed contract is in no tile and no list, and the page says so once
    assert "lapsed contract" not in page.locator("main table").inner_text()
    expect(page.get_by_test_id("renewals-lapsed-note")).to_contain_text("1 contract is still marked active")
    for days, testid in ((30, "renewals-critical"), (90, "renewals-warning"), (180, "renewals-upcoming")):
        page.goto(live_server + "/procurement/renewals?days=%d" % days, wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        figure = int(re.match(r"\s*(\d+)", _words(page.get_by_test_id(testid))).group(1))
        assert page.get_by_test_id("renewal-row").count() == figure, (days, figure)

    narrow = _page(browser, live_server, org, "procurement", width=390)
    narrow.evaluate("localStorage.setItem('theme', 'dark')")
    narrow.goto(live_server + "/procurement/renewals?days=180", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert narrow.get_by_test_id("renewal-row").count() == 4
    assert narrow.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    expect(narrow.get_by_test_id("renewals-lapsed-note")).to_be_visible()
    _shot(narrow, "renewals-narrow-dark")

    listed.filter(has_text="edge30 contract").get_by_test_id("renewal-contract-link").click()
    page.wait_for_url(re.compile(r"/procurement/contracts/%d" % made["edge30"]["contract"]), timeout=PAGE_TIMEOUT)
    expect(page.locator("main")).to_contain_text("%s edge30 contract" % noun)


def test_the_vendor_list_orders_by_renewal_says_what_is_missing_and_opens_a_vendor_with_the_same_date(
        live_server, browser):
    noun = "Vend%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect", org_admin=True)
    other = create_fresh_org("enterprise_architect")
    made = _vendors(org["org_id"], noun)
    _vendors(other["org_id"], "Other" + noun)
    page = _page(browser, live_server, org, "enterprise_architect")

    page.goto(live_server + "/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    names = page.get_by_test_id("vendor-link").all_inner_texts()
    mine = [n.replace(noun + " ", "") for n in names if n.startswith(noun)]
    # due within 90 days soonest first, then the vendors with a renewal ahead by name, then the vendor with no contract
    assert mine == ["edge30", "edge90", "far", "past90", "renewed", "nodeal"], mine
    assert "2 renew within 90 days" in _words(page.locator("main")), _words(page.locator("main"))[:600]
    table = _words(page.locator("main table").first)
    assert not re.search(r"\bNone\b", table), table
    nodeal = _words(page.locator("tr", has=page.get_by_test_id("vendor-link").filter(has_text="nodeal")))
    assert "No contract" in nodeal, nodeal
    renewed_row = page.locator("tr", has=page.get_by_test_id("vendor-link").filter(has_text="renewed"))
    ends = TODAY + datetime.timedelta(days=160)
    sort_keys = renewed_row.locator("td[data-sort]").evaluate_all("(cells) => cells.map(c => c.dataset.sort)")
    assert ends.isoformat() in sort_keys, sort_keys
    assert (TODAY - datetime.timedelta(days=20)).isoformat() not in sort_keys
    _shot(page, "vendors")

    page.get_by_test_id("vendor-link").filter(has_text="renewed").click()
    page.wait_for_url(re.compile(r"/applications/vendors/%d" % made["renewed"]["vendor"]), timeout=PAGE_TIMEOUT)
    systems = _words(page.locator("#vendor-systems"))
    assert "next renewal %s" % ends.strftime("%d %b %Y") in systems, systems
    assert (TODAY - datetime.timedelta(days=20)).strftime("%d %b %Y") not in page.locator("main").inner_text()
    expect(page.locator("#vendor-systems")).to_contain_text("%s renewed system" % noun)


# ── the business case ──────────────────────────────────────────────────────────────────────────────────────────

def test_a_business_case_names_the_currency_of_its_money_and_calls_only_its_empty_sections_empty(
        live_server, browser):
    from app import create_app, db
    from app.models.business_case import BusinessCase

    noun = "Case%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect")
    with create_app("testing").app_context():
        db.session.add(BusinessCase(title="%s move the event backbone" % noun, organization_id=org["org_id"],
                                    problem_statement="Queues lose messages.", capex=120000, opex_annual=45000,
                                    tco_3yr=255000))
        db.session.commit()
        db.session.remove()
    page = _page(browser, live_server, org, "enterprise_architect")

    page.goto(live_server + "/business-case/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name="%s move the event backbone" % noun).first.click()
    page.wait_for_url(re.compile(r"/business-case/\d+"), timeout=PAGE_TIMEOUT)

    main = _words(page.locator("main"))
    for label in (r"Capex \(\S+\)", r"Opex, annual \(\S+\)", r"3-year total cost \(\S+\)",
                  r"Annual financial benefit \(\S+\)"):
        assert re.search(label, main, re.I), (label, main[:400])
    assert "Capex ()" not in main
    assert page.locator("#bc-capex").input_value().replace(",", "").startswith("120000")
    # the section with costs in it is not called empty; the section with no risks in it is
    assert "Costs is empty" not in main and "Reasons (strategic context) is empty" not in main
    assert "Major risks is empty" in main, main
    _shot(page, "business-case")


# ── the portfolio ──────────────────────────────────────────────────────────────────────────────────────────────

def _initiatives(org_id, noun, linked):
    from app import create_app, db
    from app.models.motivation import Goal, initiative_goals
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    with create_app("testing").app_context():
        goal = Goal(name="%s grow retail" % noun, organization_id=org_id)
        served = EnterpriseInitiative(name="%s digital checkout" % noun, organization_id=org_id)
        db.session.add_all([goal, served])
        db.session.flush()
        db.session.execute(initiative_goals.insert().values(initiative_id=served.id, goal_id=goal.id))
        if not linked:
            db.session.add(EnterpriseInitiative(name="%s back office tidy" % noun, organization_id=org_id))
        db.session.commit()
        db.session.remove()


def test_the_portfolio_says_which_initiatives_serve_no_objective_and_lists_them(live_server, browser):
    noun = "Folio%s" % uuid.uuid4().hex[:6]
    org, tidy = create_fresh_org("portfolio_manager"), create_fresh_org("portfolio_manager")
    _initiatives(org["org_id"], noun, linked=False)
    _initiatives(tidy["org_id"], "Tidy" + noun, linked=True)

    page = _page(browser, live_server, org, "portfolio_manager")
    page.goto(live_server + "/portfolio/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    link = page.locator("#objective-filter").get_by_role("link", name="1 initiative serving no objective")
    expect(link).to_be_visible()
    assert "Every initiative serves an objective" not in page.locator("main").inner_text()
    _shot(page, "portfolio")
    link.click()
    page.wait_for_url(re.compile(r"filter=no-objective"), timeout=PAGE_TIMEOUT)
    expect(page.locator("#objective-filter")).to_contain_text("Showing initiatives serving no objective (1)")
    expect(page.locator("main table")).to_contain_text("%s back office tidy" % noun)
    assert "%s digital checkout" % noun not in page.locator("main table").inner_text()

    page = _page(browser, live_server, tidy, "portfolio_manager")
    page.goto(live_server + "/portfolio/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("every-initiative-has-objective")).to_have_text("Every initiative serves an objective")
    assert page.locator("#objective-filter a").count() == 0


def test_a_portfolio_with_no_initiatives_does_not_claim_they_all_serve_an_objective(live_server, browser):
    empty = create_fresh_org("portfolio_manager")
    page = _page(browser, live_server, empty, "portfolio_manager")
    page.goto(live_server + "/portfolio/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("main")).to_contain_text("No initiatives recorded yet")
    assert "Every initiative serves an objective" not in page.locator("main").inner_text()


# ── the compliance findings ────────────────────────────────────────────────────────────────────────────────────

def test_the_compliance_findings_run_control_by_control_with_several_applications_at_the_head(live_server, browser):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    suffix = uuid.uuid4().hex[:6]
    org = create_fresh_org("enterprise_architect", org_admin=True)
    with create_app("testing").app_context():
        framework = RegulatoryFramework(code="O-%s" % suffix, name="Order standard %s" % suffix, status="active")
        db.session.add(framework)
        db.session.flush()
        controls = [ComplianceControl(framework_id=framework.id, control_code="A.%d-%s" % (number, suffix),
                                      title="Control %d %s" % (number, suffix)) for number in (10, 2)]
        db.session.add_all(controls)
        apps = [ApplicationComponent(name="App %s %s" % (letter, suffix), organization_id=org["org_id"])
                for letter in "ABC"]
        db.session.add_all(apps)
        db.session.flush()
        ApplicabilityService.adopt_framework(org["org_id"], framework.id, adopted_by_id=org["user_ids"]["enterprise_architect"])
        for application in apps:
            for control in controls:
                ApplicabilityService.link_requirement(org["org_id"], application.id, control.id, "planned")
        db.session.commit()
        db.session.remove()
    page = _page(browser, live_server, org, "enterprise_architect")

    page.goto(live_server + "/dashboard/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    rows = [_words(r) for r in page.get_by_test_id("compliance-finding-row").all()]
    shown = [(re.search(r"A\.(\d+)-", r).group(1), re.search(r"App ([ABC]) ", r).group(1)) for r in rows]
    # control 2 before control 10 (by number, not by letters), each with every application that misses it
    assert shown == [("2", "A"), ("2", "B"), ("2", "C"), ("10", "A"), ("10", "B"), ("10", "C")], shown
    _shot(page, "compliance")
    page.get_by_test_id("compliance-finding-row").first.locator("a").click()
    page.wait_for_selector('[data-testid="control-panel"]', timeout=PAGE_TIMEOUT)
    panel = _words(page.get_by_test_id("control-panel"))
    assert "Control 2 %s" % suffix in panel and "App A %s" % suffix in panel, panel
