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
    assert len(page.get_by_test_id("tracking-stats").locator("[data-testid$='-value']").all()) == 2


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
