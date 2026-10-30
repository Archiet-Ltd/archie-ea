"""Portfolio tracking: a portfolio manager reads the savings the benefits records hold, filters the
dependency risk, opens the import dialog and closes it; a new company is told why nothing is listed.

The two savings figures must equal the sums of the benefits records in the same organisation, and a
second company's records must not appear in them.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .overview_data import purge_org, seed_overview_org
from .fresh_org import create_fresh_org
from .test_try_sample_company_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

APPS_A = [{"name": "Ledger Core"}, {"name": "Payments Hub"}, {"name": "Old Billing"}]
APPS_B = [{"name": "Zeta Intake"}, {"name": "Zeta Archive"}]
SAVINGS_A = [(120000, 90000), (80000, 0), (25000, 5000)]


def _add_benefits(info, rows):
    from app import create_app, db
    from app.models.application_rationalization import RationalizationBenefitsTracker

    app = create_app("testing")
    with app.app_context():
        for app_id, (projected, actual) in zip(info["app_ids"].values(), rows):
            db.session.add(RationalizationBenefitsTracker(
                application_id=app_id, projected_annual_savings=projected, actual_annual_savings=actual))
        db.session.commit()
        db.session.remove()


def _purge_benefits(info):
    from sqlalchemy import text
    from app import create_app, db
    from app.models.application_rationalization import RationalizationBenefitsTracker

    app = create_app("testing")
    with app.app_context():
        ids = list(info["app_ids"].values())
        db.session.execute(text("DELETE FROM application_dependencies WHERE source_app_id = ANY(:i) OR target_app_id = ANY(:i)"), {"i": ids})
        db.session.execute(text("DELETE FROM " + RationalizationBenefitsTracker.__tablename__ + " WHERE application_id = ANY(:i)"), {"i": ids})
        db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def orgs(live_server):
    a = seed_overview_org("a", APPS_A, [], spread=False)
    b = seed_overview_org("b", APPS_B, [], spread=False)
    _add_benefits(a, SAVINGS_A)
    _add_benefits(b, [(999000, 777000), (1000, 0)])
    yield {"a": a, "b": b}
    for info in (a, b):
        _purge_benefits(info)
        purge_org(info["org_id"])


def _open(page, live_server, email):
    _login(page, live_server, email)
    page.goto(live_server + "/applications/rationalization/tracking", wait_until="networkidle", timeout=PAGE_TIMEOUT)


def _number(text):
    return int("".join(ch for ch in text if ch.isdigit()))


def test_savings_equal_the_benefit_records(live_server, orgs, page):
    _open(page, live_server, orgs["a"]["emails"]["portfolio_manager"])
    projected = _number(page.get_by_test_id("tracking-projected-value").inner_text())
    actual = _number(page.get_by_test_id("tracking-actual-value").inner_text())
    assert projected == sum(p for p, _ in SAVINGS_A)
    assert actual == sum(a for _, a in SAVINGS_A)
    # Three cards since the portfolio's application count joined the two savings figures (a stat row holds at most four).
    assert len(page.get_by_test_id("tracking-stats").locator("[data-testid$='-value']").all()) == 3


def test_dependency_filter_and_empty_states_explain_themselves(live_server, orgs, page):
    _open(page, live_server, orgs["a"]["emails"]["portfolio_manager"])
    expect(page.get_by_test_id("dependency-empty")).to_contain_text("Import dependencies")
    expect(page.get_by_test_id("retirement-empty")).to_contain_text("marked for retirement")
    page.get_by_test_id("dep-risk-filter").select_option("critical")
    page.wait_for_load_state("networkidle")
    expect(page.get_by_test_id("dependency-empty")).to_be_visible()


def test_import_dialog_opens_and_closes(live_server, orgs, page):
    _open(page, live_server, orgs["a"]["emails"]["portfolio_manager"])
    expect(page.get_by_test_id("import-dialog")).to_be_hidden()
    page.get_by_test_id("import-open").click()
    expect(page.get_by_test_id("import-dialog")).to_be_visible()
    expect(page.get_by_test_id("import-dialog")).to_contain_text("Upload JSON or CSV file")
    page.get_by_role("button", name="Close").click()
    expect(page.get_by_test_id("import-dialog")).to_be_hidden()


def test_second_company_figures_never_appear(live_server, orgs, page):
    _open(page, live_server, orgs["b"]["emails"]["portfolio_manager"])
    assert _number(page.get_by_test_id("tracking-projected-value").inner_text()) == 1000000
    assert "225" not in page.get_by_test_id("tracking-stats").inner_text()


def test_imported_dependencies_appear_in_the_risk_table(live_server, orgs, page, tmp_path):
    import json

    ids = list(orgs["a"]["app_ids"].values())
    path = tmp_path / "deps.json"
    path.write_text(json.dumps({"dependencies": [
        {"source_app_id": ids[0], "target_app_id": ids[1], "dependency_type": "data_feed", "dependency_strength": "high"}]}))
    _open(page, live_server, orgs["a"]["emails"]["portfolio_manager"])
    page.get_by_test_id("import-open").click()
    page.locator("#import-file").set_input_files(str(path))
    page.get_by_test_id("import-confirm").click()
    expect(page.get_by_test_id("import-result")).to_contain_text("Import complete")
    page.get_by_role("button", name="Close").click()
    expect(page.get_by_test_id("dependency-row").first).to_be_visible()
    expect(page.get_by_test_id("dependency-empty")).to_be_hidden()


def test_import_names_the_row_it_could_not_read(live_server, orgs, page, tmp_path):
    import json

    ids = list(orgs["a"]["app_ids"].values())
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"dependencies": [
        {"source_app_id": ids[0], "target_app_id": ids[1], "dependency_type": "carrier_pigeon"}]}))
    _open(page, live_server, orgs["a"]["emails"]["portfolio_manager"])
    page.get_by_test_id("import-open").click()
    page.locator("#import-file").set_input_files(str(path))
    page.get_by_test_id("import-confirm").click()
    expect(page.get_by_test_id("import-result")).to_contain_text("Row 1: Invalid dependency_type")


def _portfolio_count(page, live_server, path):
    page.goto(live_server + path, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    return _number(page.get_by_test_id("portfolio-applications-value").inner_text())


def test_application_count_agrees_on_workbench_planning_tracking_and_the_list(live_server, orgs, page):
    """A portfolio manager moves through workbench, planning and tracking; the application count is the
    applications list's, on every page of the family."""
    email = orgs["a"]["emails"]["portfolio_manager"]
    _login(page, live_server, email)
    page.goto(live_server + "/applications/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    listed = _number(page.get_by_test_id("total-count-badge").inner_text())
    assert listed == len(APPS_A)
    first_app = list(orgs["a"]["app_ids"].values())[0]
    for path in ("/applications/rationalization/workbench",
                 "/applications/rationalization/planning/%d" % first_app,
                 "/applications/rationalization/tracking"):
        assert _portfolio_count(page, live_server, path) == listed, path


def test_other_company_applications_never_count(live_server, orgs, page):
    _login(page, live_server, orgs["b"]["emails"]["portfolio_manager"])
    assert _portfolio_count(page, live_server, "/applications/rationalization/workbench") == len(APPS_B)
    assert _portfolio_count(page, live_server, "/applications/rationalization/tracking") == len(APPS_B)


def test_workbench_opens_with_figures_and_a_chart_that_explain_the_portfolio(live_server, orgs, page):
    _open(page, live_server, orgs["a"]["emails"]["portfolio_manager"])
    page.goto(live_server + "/applications/rationalization/workbench", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert len(page.get_by_test_id("workbench-stats").locator("[data-testid$='-value']").all()) <= 4
    # No application is scored in this organisation: the page says why and what to do next.
    expect(page.get_by_test_id("workbench-disposition-empty")).to_contain_text("Run detection and scoring")
    expect(page.get_by_test_id("workbench-scored-value")).to_have_text("0")


def test_tracking_risk_filter_is_whole_at_phone_width(live_server, orgs, page):
    page.set_viewport_size({"width": 390, "height": 844})
    _open(page, live_server, orgs["a"]["emails"]["portfolio_manager"])
    box = page.get_by_test_id("dep-risk-filter").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert box["width"] >= 150, "the filter is cut to %spx, too narrow to read 'All risk levels'" % box["width"]
    clipped = page.get_by_test_id("dep-risk-filter").evaluate("el => el.scrollWidth > el.clientWidth + 1")
    assert not clipped


APPS_C = [{"name": "Atlas CRM"}, {"name": "Beacon HR"}, {"name": "Cobalt Wiki"}]
# (health, disposition, decision ready, review state)
SCORES_C = [(82, "retain", True, "approved"), (35, "retire", True, "draft"), (55, "retire", False, "reviewed")]


@pytest.fixture(scope="module")
def scored_org(live_server):
    from app import create_app, db
    from app.models.application_rationalization import ApplicationRationalizationScore

    info = seed_overview_org("c", APPS_C, [], spread=False)
    app = create_app("testing")
    with app.app_context():
        for app_id, (health, disposition, ready, review) in zip(info["app_ids"].values(), SCORES_C):
            db.session.add(ApplicationRationalizationScore(
                organization_id=info["org_id"], application_component_id=app_id, overall_health_score=health, rationalization_action="MIGRATE",
                disposition_action=disposition, is_decision_ready=ready, review_status=review))
        db.session.commit()
        db.session.remove()
    yield info
    from sqlalchemy import text
    app = create_app("testing")
    with app.app_context():
        ids = list(info["app_ids"].values())
        db.session.execute(text("DELETE FROM application_rationalization_scores WHERE application_component_id = ANY(:i)"), {"i": ids})
        db.session.commit()
        db.session.remove()
    purge_org(info["org_id"])


def test_workbench_figures_chart_and_rows_agree_with_the_scores(live_server, scored_org, page):
    """A portfolio manager sees the same scored count in the stat row, the chart's figures and the table."""
    _login(page, live_server, scored_org["emails"]["portfolio_manager"])
    page.goto(live_server + "/applications/rationalization/workbench", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert _number(page.get_by_test_id("portfolio-applications-value").inner_text()) == len(APPS_C)
    assert _number(page.get_by_test_id("workbench-scored-value").inner_text()) == len(SCORES_C)
    assert _number(page.get_by_test_id("workbench-ready-value").inner_text()) == sum(1 for s in SCORES_C if s[2])
    assert _number(page.get_by_test_id("workbench-awaiting-value").inner_text()) == sum(1 for s in SCORES_C if s[3] != "approved")
    figures = {r.locator("td").nth(0).inner_text(): _number(r.locator("td").nth(1).inner_text())
               for r in page.get_by_test_id("workbench-disposition-row").all()}
    assert figures == {"Retain": 1, "Retire": 2}
    expect(page.get_by_test_id("workbench-row")).to_have_count(len(SCORES_C))


def test_workbench_row_opens_its_planning_page_and_the_breakdown_sheet(live_server, scored_org, page):
    _login(page, live_server, scored_org["emails"]["portfolio_manager"])
    page.goto(live_server + "/applications/rationalization/workbench", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    row = page.get_by_test_id("workbench-row").filter(has_text="Atlas CRM")
    row.get_by_role("button", name="View").click()
    expect(page.get_by_test_id("breakdown-sheet")).to_be_visible()
    expect(page.get_by_test_id("breakdown-dimension").first).to_be_visible()
    expect(page.get_by_test_id("breakdown-dimension")).to_have_count(4)
    page.get_by_test_id("breakdown-sheet").get_by_role("button", name="Close").first.click()
    expect(page.get_by_test_id("breakdown-sheet")).to_be_hidden()
    row.get_by_role("link", name="Atlas CRM").click()
    page.wait_for_url("**/planning/*")
    expect(page.get_by_test_id("planning-health-value")).to_have_text("82")
    expect(page.get_by_test_id("planning-review-value")).to_have_text("Approved")
    assert _number(page.get_by_test_id("portfolio-applications-value").inner_text()) == len(APPS_C)
    expect(page.get_by_test_id("planning-dimension").first).to_be_visible()
