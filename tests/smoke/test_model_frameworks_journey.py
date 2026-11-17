"""An enterprise architect finds the frameworks from the sidebar, and a portfolio manager finds the
business cases, each in one or two clicks.

The people: an enterprise architect in an organisation that has adopted one compliance framework,
and a portfolio manager in the same organisation, which holds a business case. A second organisation
adopts a different framework; it must never show as the first organisation's choice.

Their goals, each asserted as the outcome they need:

- Frameworks opens one gallery: the organisation's own framework first, the industry frameworks as
  cards that give their real process count, then the compliance frameworks it can still adopt; no
  other sidebar entry names a framework;
- an industry card opens that industry's page and the page shows the processes the card counted;
- a compliance card opens the compliance page, which lists that framework by name;
- Changes, then Business cases, opens the list; the business case the organisation holds is on it
  and opens; Business cases sits right after Options analysis.

Played at 1440 wide in the light theme and at 390 wide in the dark theme. Set JOURNEY_SHOT_DIR to
keep the screenshots.
"""

import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PROCESSES = ["Plan fleet capacity", "Dispatch a vehicle", "Settle a freight invoice"]


@pytest.fixture(scope="module")
def estate(live_server):
    """Two organisations. The first has adopted Alpha and holds a business case; the second adopted Beta."""
    from app import create_app, db
    from app.models.business_case import BusinessCase
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.models.industry_apqc import IndustryAPQCFramework, IndustryAPQCProcess
    from app.models.regulatory_framework import FrameworkAdoption

    first = create_fresh_org("enterprise_architect", extra_roles=("portfolio_manager",))
    second = create_fresh_org("enterprise_architect")
    suffix = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        alpha = RegulatoryFramework(code="AL%s" % suffix, name="Alpha Assurance %s" % suffix,
                                    description="Controls for service assurance.", category="security",
                                    jurisdiction="Global", status="active")
        beta = RegulatoryFramework(code="BE%s" % suffix, name="Beta Privacy %s" % suffix,
                                   description="Controls for personal data.", category="privacy",
                                   jurisdiction="EU", status="active")
        db.session.add_all([alpha, beta])
        db.session.flush()
        for number in (1, 2, 3):
            db.session.add(ComplianceControl(framework_id=alpha.id, control_code="A-%d" % number,
                                             title="Alpha control %d" % number))
        for organisation, framework in ((first, alpha), (second, beta)):
            db.session.add(FrameworkAdoption(organization_id=organisation["org_id"], framework_id=framework.id,
                                             scope="tenant", status="active"))
        industry = IndustryAPQCFramework(industry_code="FL%s" % suffix.upper(),
                                         industry_name="Freight and logistics %s" % suffix,
                                         description="Moving goods for other companies.", is_active=True)
        db.session.add(industry)
        db.session.flush()
        for number, name in enumerate(PROCESSES, 1):
            db.session.add(IndustryAPQCProcess(industry_framework_id=industry.id,
                                               industry_process_code="1.%d" % number,
                                               industry_process_name=name,
                                               industry_category_1="Operate", is_active=True))
        title = "Retire the legacy dispatch system %s" % suffix
        db.session.add(BusinessCase(title=title, description="Move dispatch to the shared platform.",
                                    organization_id=first["org_id"]))
        db.session.commit()
        out = {"first": first, "second": second, "alpha": alpha.name, "beta": beta.name,
               "industry": industry.industry_name, "industry_code": industry.industry_code, "case": title}
        db.session.remove()
    return out


@pytest.fixture(scope="module", params=[(1440, "light"), (390, "dark")], ids=["desktop-light", "phone-dark"])
def walk(request, browser, live_server):
    width, theme = request.param
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(30000)
    context.set_default_navigation_timeout(60000)
    if theme == "dark":
        context.add_init_script(
            "try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
            "document.documentElement.classList.add('dark');"
        )
    page = context.new_page()
    root = os.environ.get("JOURNEY_SHOT_DIR") or os.path.join(
        os.path.dirname(__file__), "..", "..", "design-audit", "journeys", "model-frameworks"
    )
    shots = os.path.abspath(os.path.join(root, "%d-%s" % (width, theme)))
    os.makedirs(shots, exist_ok=True)

    class Walk:
        phone = width < 768
        base = live_server
        count = 0

        def shot(self, name):
            self.count += 1
            page.screenshot(path=os.path.join(shots, "%02d-%s.png" % (self.count, name)))

    Walk.page = page
    yield Walk()
    context.close()


def _sign_in_as(walk, estate, organisation, role):
    from app import create_app
    from app.models.user import User

    email = estate[organisation]["emails"][role]
    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == role, user.enterprise_role
        assert not user.is_platform_admin and not user.is_org_admin
    walk.page.context.clear_cookies()
    sign_in(walk.page, walk.base, email)
    walk.page.goto(walk.base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)


def _sidebar(walk):
    """The sidebar; on a phone the drawer, opened first with the trigger."""
    page = walk.page
    if walk.phone:
        drawer = page.get_by_role("dialog", name="Navigation")
        if not drawer.is_visible():
            page.get_by_test_id("sidebar-trigger").click()
        expect(drawer).to_be_visible()
        return drawer
    return page.locator('[data-slot="sidebar"]')


def _open(walk, item):
    toggle = _sidebar(walk).locator('[data-slot="sidebar-menu-button"][data-nav-key="%s"]' % item)
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")


def _open_frameworks(walk):
    _sidebar(walk).locator('a[data-nav-key="frameworks"]').click()
    walk.page.wait_for_url(re.compile(r"/frameworks/?$"), timeout=PAGE_TIMEOUT)


def test_01_the_model_group_has_one_frameworks_entry_and_no_other_names_one(estate, walk):
    _sign_in_as(walk, estate, "first", "enterprise_architect")
    side = _sidebar(walk)
    expect(side.locator('[data-testid="nav-model"] a[data-nav-key="frameworks"]')).to_contain_text("Frameworks")
    assert side.locator('[data-nav-key*="framework"]').count() == 1, "one framework entry, no more"
    walk.shot("sidebar")


def test_02_frameworks_shows_the_organisations_own_first_then_every_industry_then_the_rest(estate, walk):
    _sign_in_as(walk, estate, "first", "enterprise_architect")
    _open_frameworks(walk)
    main = walk.page.locator("main").first
    yours = main.get_by_test_id("frameworks-yours")
    expect(yours).to_contain_text(estate["alpha"])
    expect(yours).to_contain_text("Adopted")
    expect(yours).to_contain_text("3 controls")
    expect(yours).not_to_contain_text(estate["beta"])
    card = main.get_by_test_id("frameworks-industry").locator('[data-framework-card="%s"]' % estate["industry_code"])
    expect(card).to_contain_text(estate["industry"])
    expect(card).to_contain_text("%d processes" % len(PROCESSES))
    other = main.get_by_test_id("frameworks-compliance")
    expect(other).to_contain_text(estate["beta"])
    expect(other).not_to_contain_text("Adopted")
    order = main.locator("h2").all_inner_texts()
    assert order == ["Your frameworks", "Industry process frameworks", "Compliance frameworks you can adopt"], order
    walk.shot("gallery")


def test_03_the_other_organisation_sees_its_own_choice_not_the_first_ones(estate, walk):
    _sign_in_as(walk, estate, "second", "enterprise_architect")
    walk.page.goto(walk.base + "/frameworks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    main = walk.page.locator("main").first
    expect(main.get_by_test_id("frameworks-yours")).to_contain_text(estate["beta"])
    expect(main.get_by_test_id("frameworks-yours")).not_to_contain_text(estate["alpha"])
    expect(main.get_by_test_id("frameworks-compliance")).to_contain_text(estate["alpha"])


def test_04_an_industry_card_opens_the_processes_it_counted(estate, walk):
    _sign_in_as(walk, estate, "first", "enterprise_architect")
    _open_frameworks(walk)
    walk.page.locator('[data-framework-card="%s"]' % estate["industry_code"]).click()
    walk.page.wait_for_url(re.compile(r"/industry-apqc/framework/%s" % estate["industry_code"]), timeout=PAGE_TIMEOUT)
    main = walk.page.locator("main").first
    expect(main).to_contain_text(estate["industry"])
    for name in PROCESSES:
        expect(main).to_contain_text(name)
    walk.shot("industry-page")


def test_05_a_compliance_card_opens_a_page_that_lists_that_framework(estate, walk):
    _sign_in_as(walk, estate, "first", "enterprise_architect")
    _open_frameworks(walk)
    walk.page.get_by_test_id("frameworks-yours").locator("a[data-framework-card]").first.click()
    walk.page.wait_for_url(re.compile(r"/compliance"), timeout=PAGE_TIMEOUT)
    expect(walk.page.locator("main").first).to_contain_text(estate["alpha"])
    walk.shot("compliance-page")


def test_06_a_portfolio_manager_opens_changes_then_business_cases_and_finds_the_case(estate, walk):
    _sign_in_as(walk, estate, "first", "portfolio_manager")
    _open(walk, "changes")
    names = _sidebar(walk).locator('[data-nav-item="changes"] a[data-slot="sidebar-menu-sub-button"]').all_inner_texts()
    names = [n.strip() for n in names]
    assert names.index("Business cases") == names.index("Options analysis") + 1, names
    _sidebar(walk).locator('a[data-nav-key="business_cases"]').click()
    walk.page.wait_for_url(re.compile(r"/business-case/?$"), timeout=PAGE_TIMEOUT)
    main = walk.page.locator("main").first
    expect(main).to_contain_text(estate["case"])
    walk.shot("business-cases")
    main.get_by_text(estate["case"]).first.click()
    walk.page.wait_for_url(re.compile(r"/business-case/\d+"), timeout=PAGE_TIMEOUT)
    expect(walk.page.locator("main").first).to_contain_text(estate["case"])
    expect(walk.page.locator("main").first).to_contain_text("Move dispatch to the shared platform")
    walk.shot("business-case-page")
