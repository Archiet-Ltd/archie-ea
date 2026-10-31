"""EA workflows dashboard: the counts are the instances list's own.

An enterprise architect opens the workflow dashboard to see what is running, waiting for them, finished or
failed. Each count must equal the total the instances list reports for that status (not a count of the
latest few), the recent runs must open the run, the cards are the library's stat row, and an organisation
that has run nothing says why and shows where to start.
"""
import datetime as dt
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in
from .overview_data import purge_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# More instances than the dashboard's recent list holds, with the unfinished ones the oldest.
COUNTS = {"completed": 21, "running": 1, "waiting_approval": 1, "failed": 1}


@pytest.fixture(scope="module")
def populated(live_server):
    from app import create_app, db
    from app.models.workflow_models import EAWorkflowDefinition, EAWorkflowInstance
    from app.services.ea_workflow_engine import EAWorkflowEngine

    info = create_fresh_org("enterprise_architect")
    suffix = uuid.uuid4().hex[:6]
    app = create_app("testing")
    with app.app_context():
        EAWorkflowEngine().seed_default_workflows()
        definition = EAWorkflowDefinition(workflow_code="JOURNEY_%s" % suffix, workflow_name="Journey workflow %s" % suffix,
                                          workflow_category="architecture", steps=[{"step_id": "1", "name": "Start", "type": "start"}])
        db.session.add(definition)
        db.session.commit()
        definition_id = definition.id
        now = dt.datetime.utcnow()
        order = ["waiting_approval", "running", "failed"] + ["completed"] * COUNTS["completed"]
        ids = []
        for n, status in enumerate(order):
            row = EAWorkflowInstance(instance_code="JOURNEY_%s_%d" % (suffix, n), workflow_definition_id=definition_id,
                                     status=status, organization_id=info["org_id"],
                                     created_at=now - dt.timedelta(hours=len(order) - n))
            db.session.add(row)
            db.session.flush()
            ids.append(row.id)
        db.session.commit()
        db.session.remove()
    info.update(suffix=suffix, definition=definition_id, instances=ids)
    yield info
    with app.app_context():
        db.session.execute(db.text("DELETE FROM ea_workflow_instances WHERE id = ANY(:ids)"), {"ids": ids})
        db.session.execute(db.text("DELETE FROM ea_workflow_definitions WHERE id = :d"), {"d": definition_id})
        db.session.commit()
        db.session.remove()
    purge_org(info["org_id"])


@pytest.fixture(scope="module")
def empty(live_server):
    info = create_fresh_org("enterprise_architect")
    yield info
    purge_org(info["org_id"])


def _open(browser, live_server, info, width=1400, url="/ea-workflows"):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, info["emails"]["enterprise_architect"])
    response = page.goto(live_server + url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert response.status == 200
    return page


def _figure(page, key):
    return page.get_by_test_id("wf-%s-value" % key).inner_text().strip()


def test_counts_equal_the_instances_list(browser, live_server, populated):
    page = _open(browser, live_server, populated)
    for key, status in (("running", "running"), ("waiting", "waiting_approval"), ("completed", "completed"),
                        ("failed", "failed")):
        code, body = api(page, "GET", "/api/ea-workflows/instances?status=%s&per_page=1" % status)
        assert code == 200 and body["success"]
        assert int(_figure(page, key)) == body["total"] == COUNTS[status], (key, _figure(page, key), body["total"])


def test_recent_runs_open_the_run(browser, live_server, populated):
    page = _open(browser, live_server, populated)
    runs = page.locator('#recent-activity a[href*="/ea-workflows/instance/"]')
    assert 1 <= runs.count() <= 10
    expect(runs.first).to_be_visible()
    href = runs.first.get_attribute("href")
    page.goto(live_server + href, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert "Journey workflow %s" % populated["suffix"] in page.locator("main").inner_text()


def test_waiting_card_leads_to_the_approvals_queue(browser, live_server, populated):
    page = _open(browser, live_server, populated)
    page.get_by_test_id("wf-waiting").get_by_role("link", name="Review the queue").click()
    page.wait_for_url("**/ea-workflows/approvals*", timeout=PAGE_TIMEOUT)
    assert "JOURNEY_%s" % populated["suffix"] in page.locator("main").inner_text() or "Journey workflow" in page.locator("main").inner_text()


def test_the_look_is_the_librarys(browser, live_server, populated):
    page = _open(browser, live_server, populated)
    row = page.get_by_test_id("wf-stat-row")
    cards = row.locator("[data-slot=card]")
    assert cards.count() == 4
    assert row.locator("svg, i[data-lucide]").count() == 0, "stat cards carry no icons"
    assert not re.search(r"border-(2|l-|t-)|bg-(destructive|warning|success|info)\b", row.inner_html()), "no coloured borders"
    for i in range(cards.count()):
        assert cards.nth(i).locator("p").count() >= 1, "each card says what its figure counts"
    markup = re.sub(r"<(script|template)[\s\S]*?</\1>", "", page.locator("main").inner_html())
    assert not re.search(r"border-2", markup), "no thick borders in the page"
    assert not re.search(r"\bbg-(destructive|warning|success|info)\b[^/]", markup), "no filled colour tiles"


def test_a_scenario_start_opens_its_dialog(browser, live_server, populated):
    page = _open(browser, live_server, populated)
    page.locator('[data-scenario-card="APP_DISPOSITION"]').get_by_role("button", name="Start").click()
    dialog = page.locator("#startWorkflowModal")
    expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
    assert "Application Disposition" in dialog.inner_text() or "Disposition" in dialog.inner_text()
    dialog.get_by_role("button", name="Cancel").click()
    expect(dialog).to_be_hidden(timeout=PAGE_TIMEOUT)


def test_an_organisation_with_no_runs_says_why_and_where_to_start(browser, live_server, empty):
    page = _open(browser, live_server, empty)
    for key in ("running", "waiting", "completed", "failed"):
        assert _figure(page, key) == "0"
    state = page.get_by_test_id("wf-no-runs")
    expect(state).to_be_visible()
    assert "No workflow has run in this organisation yet" in state.inner_text()
    link = state.get_by_role("link", name="Choose a workflow to start")
    assert link.get_attribute("href").startswith("#")


def test_nothing_overflows_on_a_phone(browser, live_server, populated):
    page = _open(browser, live_server, populated, width=390)
    expect(page.get_by_test_id("wf-stat-row")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
