"""Architecture Health Scorecard: a CTO reads figures that agree with the rest of the product.

The scorecard's health score must be the one the Overview and the executive summary show for the same
organisation; the headline figures sit in the library's stat row (at most four cards, no icons); the
open-risk figure equals the four severity figures under it; the ArchiMate total equals the layer table; the
ADM table accounts for every solution; and every link on the page lands on a page with content.
"""
import re

import pytest
from playwright.sync_api import expect
from sqlalchemy import text

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org
from .overview_data import purge_org
from .test_try_sample_company_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PHASES = ["A", "C", "C", "F", "Z"]  # Z is no ADM phase: that solution is unclassified
# (likelihood, impact): scores 25, 9, 6, 1 are critical, high, medium and low
RISKS = [(5, 5), (3, 3), (2, 3), (1, 1)]
APPS = ["Ledger Core", "Payments Hub", "Old Billing", "Claims Portal", "Intake Form", "Order Desk"]


@pytest.fixture(scope="module")
def org(live_server):
    info = create_fresh_org("cto")
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.risk import Risk
    from app.models.solution_models import Solution

    app = create_app("testing")
    with app.app_context():
        org_id = info["org_id"]
        for index, phase in enumerate(PHASES):
            db.session.add(Solution(name="Scorecard solution %d %s" % (index, info["suffix"]), organization_id=org_id,
                                    adm_phase=phase, maturity_current=0))
        for name in APPS:
            db.session.add(ApplicationComponent(name="%s %s" % (name, info["suffix"]), organization_id=org_id,
                                                lifecycle_status="operational"))
        for likelihood, impact in RISKS:
            db.session.add(Risk(title="Scorecard risk %d%d %s" % (likelihood, impact, info["suffix"]),
                                likelihood=likelihood, impact=impact, organization_id=org_id))
        db.session.commit()
        db.session.remove()
    yield info
    app = create_app("testing")
    with app.app_context():
        for table in ("risks", "solutions"):
            db.session.execute(text("DELETE FROM %s WHERE organization_id = :o" % table), {"o": org_id})
        db.session.commit()
        db.session.remove()
    purge_org(org_id)


def _open(page, live_server, org, path="/dashboard/health"):
    _login(page, live_server, org["emails"]["cto"])
    response = page.goto(live_server + path, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert response.status == 200
    page.wait_for_load_state("networkidle")


def _int(page, testid):
    return int(page.get_by_test_id(testid).inner_text().strip())


def test_health_score_equals_the_executive_summary_and_the_overview(live_server, org, page):
    _open(page, live_server, org)
    score = page.get_by_test_id("health-score-value").inner_text().strip()
    assert re.fullmatch(r"\d+\.\d", score), "the score reads as one decimal, got %r" % score
    summary = page.request.get(live_server + "/dashboard/api/executive-summary")
    assert summary.status == 200
    assert summary.json()["data"]["Health Score"] == score

    page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    shown = page.get_by_test_id("health-score-value")
    if shown.count():
        assert shown.first.inner_text().strip() == score


def test_headline_figures_are_a_stat_row_without_icons(live_server, org, page):
    _open(page, live_server, org)
    stats = page.get_by_test_id("health-stats")
    cards = stats.locator("[data-slot='card']")
    assert cards.count() == 4, "the scorecard has four headline figures"
    assert stats.locator("svg").count() == 0, "stat cards carry no icons"
    expect(stats).to_contain_text("Avg Solution Maturity")
    assert page.get_by_test_id("health-total-solutions").inner_text().strip() == str(len(PHASES))
    elements = _int(page, "health-total-archimate")
    assert elements >= len(APPS), "every application is a catalogue element"


def test_open_risks_equal_the_severity_figures_under_them(live_server, org, page):
    _open(page, live_server, org)
    open_risks = _int(page, "health-open-risks")
    levels = [_int(page, "health-risk-%s" % level) for level in ("critical", "high", "medium", "low")]
    assert levels == [1, 1, 1, 1]
    assert open_risks == sum(levels) == len(RISKS)


def test_layer_table_adds_up_to_the_element_total(live_server, org, page):
    _open(page, live_server, org)
    total = _int(page, "health-total-archimate")
    counts = [int(row.locator("td").nth(1).inner_text().strip()) for row in page.get_by_test_id("health-layer-row").all()]
    assert counts and sum(counts) == total
    # The chart above the table is drawn: it has height, and its svg is on the page.
    expect(page.get_by_test_id("health-layer-chart")).to_be_visible()
    expect(page.locator("[data-testid=health-layer-chart] svg").first).to_be_visible()


def test_adm_table_accounts_for_every_solution(live_server, org, page):
    _open(page, live_server, org)
    counts = [int(row.locator("td").nth(1).inner_text().strip()) for row in page.get_by_test_id("health-adm-row").all()]
    assert sum(counts) == len(PHASES) == _int(page, "health-total-solutions")
    expect(page.get_by_test_id("health-adm-table")).to_contain_text("Unclassified")


def test_every_link_on_the_page_lands_on_content(live_server, org, page):
    _open(page, live_server, org)
    hrefs = []
    for link in page.get_by_role("main").get_by_role("link").all():
        href = link.get_attribute("href")
        if href and href.startswith("/") and href not in hrefs and not href.startswith("/dashboard/health"):
            hrefs.append(href)
    assert len(hrefs) >= 3
    for href in hrefs:
        response = page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200, "%s answered %s" % (href, response.status)
        assert len(page.inner_text("main").strip()) > 60, "%s opened an empty page" % href


def test_the_briefing_is_still_offered(live_server, org, page):
    _open(page, live_server, org)
    expect(page.get_by_role("button", name="Generate briefing")).to_be_visible()


@pytest.fixture(scope="module")
def unclassified_org(live_server):
    """An organisation whose only solution has no recorded ADM phase (the case the pipeline journey covers)."""
    info = create_fresh_org("cto")
    from app import create_app, db
    from app.models.solution_models import Solution

    app = create_app("testing")
    with app.app_context():
        db.session.add(Solution(name="Unphased %s" % info["suffix"], organization_id=info["org_id"], adm_phase="Z",
                                maturity_current=0))
        db.session.commit()
        db.session.remove()
    yield info
    app = create_app("testing")
    with app.app_context():
        db.session.execute(text("DELETE FROM solutions WHERE organization_id = :o"), {"o": info["org_id"]})
        db.session.commit()
        db.session.remove()
    purge_org(info["org_id"])


def test_only_unclassified_solutions_leave_maturity_unmeasured(live_server, unclassified_org, page):
    _open(page, live_server, unclassified_org)
    expect(page.get_by_test_id("health-maturity").locator("[data-slot='card-title']")).to_have_text("—")
    distribution = page.get_by_role("heading", name="ADM Phase Distribution", exact=True).locator("../..")
    expect(distribution.get_by_text("Unclassified", exact=True).locator("..")).to_contain_text("100.0%")
    expect(distribution.get_by_text("A: Vision", exact=True).locator("..")).to_contain_text("0.0%")
