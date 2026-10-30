"""Journeys for form screens rebuilt on the field set.

Each test starts from a person's goal, fills the form with the real controls, submits it and asserts the
result the person needs on the page it lands on. A missing required field is refused in the browser.
"""
import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _open(page, base, path):
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)


def test_platform_admin_registers_an_agent_and_sees_it(browser, live_server):
    org = create_fresh_org("platform_admin", org_admin=True)
    page = browser.new_page()
    try:
        sign_in(page, live_server, org["emails"]["platform_admin"])
        _open(page, live_server, "/admin/agent-registry/new")
        form = page.locator("[data-testid=new-agent-form]")
        expect(form.get_by_label("Name")).to_be_visible()
        # A missing name is refused in the browser and nothing is sent.
        form.get_by_role("button", name="Register").click()
        assert "/new" in page.url
        assert form.get_by_label("Name").evaluate("e => e.validity.valueMissing")
        form.get_by_label("Name").fill("Support triage agent")
        form.get_by_label("Purpose").fill("Sorts incoming tickets by urgency")
        form.get_by_label("Name").press("Enter")
        page.wait_for_url(lambda u: "/new" not in u, timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("Support triage agent")
        _open(page, live_server, "/admin/agent-registry/")
        expect(page.locator("body")).to_contain_text("Support triage agent")
    finally:
        page.close()


def test_data_architect_raises_a_data_issue_and_it_is_listed(browser, live_server):
    org = create_fresh_org("data_architect", org_admin=True)
    from app import create_app, db
    from app.models.process_data import DataDomain, DataEntity

    app = create_app("testing")
    with app.app_context():
        domain = DataDomain(name="Customer", organization_id=org["org_id"])
        db.session.add(domain)
        db.session.flush()
        entity = DataEntity(name="Customer record", domain_id=domain.id, organization_id=org["org_id"])
        db.session.add(entity)
        db.session.commit()
        entity_id = entity.id
        db.session.remove()
    page = browser.new_page()
    try:
        sign_in(page, live_server, org["emails"]["data_architect"])
        _open(page, live_server, "/data-governance/issues/new?data_entity_id=%d" % entity_id)
        form = page.locator("[data-testid=new-data-issue-form]")
        expect(form.get_by_label("Data entity ID")).to_have_value(str(entity_id))
        form.get_by_label("Title").fill("Duplicate customer ids")
        form.get_by_label("Description").fill("Two systems issue ids for the same customer")
        form.get_by_role("button", name="Raise issue").click()
        page.wait_for_url("**/data-governance/issues", timeout=PAGE_TIMEOUT)
        issues = page.locator("[data-testid=data-issues]")
        expect(issues).to_contain_text("Duplicate customer ids")
        expect(issues).to_contain_text("Customer record")
        # A title is required: the browser refuses an empty one.
        _open(page, live_server, "/data-governance/issues/new?data_entity_id=%d" % entity_id)
        form.get_by_role("button", name="Raise issue").click()
        assert form.get_by_label("Title").evaluate("e => e.validity.valueMissing")
    finally:
        page.close()


def test_architect_records_a_review_outcome_and_it_shows_on_the_decision(browser, live_server):
    org = create_fresh_org("enterprise_architect", org_admin=True)
    from datetime import date

    from app import create_app, db
    from app.models.architecture_decision import ArchitectureDecision

    app = create_app("testing")
    with app.app_context():
        d = ArchitectureDecision(title="Adopt managed Postgres", status="accepted", organization_id=org["org_id"],
                                 review_date=date(2026, 9, 1), rationale="Fewer operational chores")
        db.session.add(d)
        db.session.commit()
        decision_id = d.id
        db.session.remove()
    page = browser.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        _open(page, live_server, "/architecture/decisions/%d/record-outcome" % decision_id)
        form = page.locator("[data-testid=record-outcome-form]")
        expect(form).to_contain_text("Review date")
        form.get_by_role("button", name="Record Outcome").click()
        assert form.locator("#review_outcome").evaluate("e => e.validity.valueMissing")
        form.locator("#review_outcome").fill("The decision still holds; renewed for a year.")
        form.locator("#next_review_date").fill("2027-09-01")
        form.get_by_role("button", name="Record Outcome").click()
        page.wait_for_url("**/architecture/decisions/%d" % decision_id, timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).to_contain_text("The decision still holds; renewed for a year.")
    finally:
        page.close()
