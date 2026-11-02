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


def _shot(page, name):
    walk = os.environ.get("WALK_DIR")
    if walk:
        page.screenshot(path=os.path.join(walk, name + ".png"), full_page=True)


def _context(browser, width=1440, dark=False):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1000},
                                  color_scheme="dark" if dark else "light")
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context


def test_named_addresses_land_on_a_page_with_a_next_step(browser, server):
    mine = create_fresh_org("enterprise_architect")
    context = _context(browser)
    page = context.new_page()
    sign_in(page, server, mine["emails"]["enterprise_architect"])
    for path, title in NAMED:
        response = page.goto(server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200, "%s answered %s" % (path, response.status)
        expect(page.get_by_role("heading", level=1)).to_have_text(title)
        expect(page.get_by_test_id("early-access-page")).to_contain_text(title)
        expect(page.get_by_test_id("early-access-submit")).to_be_visible()
        assert "couldn't find that page" not in page.locator("body").inner_text().lower()
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
    page.get_by_role("link", name="Review consolidation candidates").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_role("heading", name="Consolidation Candidates").first).to_be_visible()
    assert page.locator("main").inner_text().strip()

    other = _context(browser).new_page()
    sign_in(other, server, empty["emails"]["enterprise_architect"])
    other.goto(server + "/enterprise/analysis/ai-architecture", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    text = other.locator("main").inner_text()
    assert "your model holds no applications" in text and "Add applications" in text


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
