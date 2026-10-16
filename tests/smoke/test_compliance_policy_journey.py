"""The compliance officer's policy journey, in a real browser: write a policy, bind a clause to a
control, publish it, see the application the clause governs and the violation in the findings
list, reload and see them again; then record the impact-assessment status of a processing
activity and see it after a reload.
"""

import datetime
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id):
    from app import create_app, db
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_portfolio import ApplicationComponent
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.models.regulatory_framework import FrameworkAdoption
    from app.modules.enterprise_genome.services.ai_system_profile import AI_SYSTEM_MARKER

    app = create_app("testing")
    label = uuid.uuid4().hex[:5].upper()
    with app.app_context():
        framework = RegulatoryFramework(code="PJ%s" % label, name="Policy journey framework", category="security")
        db.session.add(framework)
        db.session.flush()
        control = ComplianceControl(framework_id=framework.id, control_code="PJ-%s" % label, title="Reviewed access")
        component = ApplicationComponent(name="Policy ledger %s" % label, organization_id=org_id)
        db.session.add_all([control, component])
        db.session.flush()
        db.session.add(FrameworkAdoption(organization_id=org_id, scope="tenant", framework_id=framework.id, status="active"))
        db.session.add(ApplicationComplianceControl(
            organization_id=org_id, application_id=component.id, control_id=control.id,
            implementation_status="implemented", evidence_url="https://evidence.example.test/old",
            evidence_valid_until=datetime.datetime.utcnow() - datetime.timedelta(days=5),
        ))
        activity = ArchiMateElement(
            name="Patient records %s" % label, type="DataObject", layer="application", organization_id=org_id,
            custom_properties={"data_categories": ["Health data"]},
        )
        model = ArchiMateElement(
            name="Triage model %s" % label, type="ApplicationComponent", layer="application", organization_id=org_id,
            custom_properties={AI_SYSTEM_MARKER: {"provider": "test", "model_id": "m"}},
        )
        db.session.add_all([activity, model])
        db.session.flush()
        db.session.add(ArchiMateRelationship(source_id=model.id, target_id=activity.id, type="Access", organization_id=org_id))
        db.session.commit()
        return {"label": label, "control": control.control_code, "application": component.name,
                "activity": activity.name, "activity_id": activity.id}


@pytest.fixture(scope="module")
def policy_world(seeded, live_server):
    return _seed(seeded["ids"]["org"])


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def _reload_after(page, action):
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        action()


def test_the_officer_publishes_a_policy_sees_the_violation_and_records_an_assessment(
    page, live_server, seeded, policy_world
):
    name = "Access policy %s" % policy_world["label"]
    _login(page, live_server, seeded["emails"]["compliance"])
    page.goto(live_server + "/dashboard/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    page.get_by_role("button", name="New policy").click()
    page.fill("#new-policy-name", name)
    _reload_after(page, lambda: page.get_by_role("button", name="Create draft").click())

    policy = page.locator('[data-testid="policy"]', has_text=name)
    expect(policy).to_be_visible()
    expect(policy.locator('[data-testid="policy-state"]')).to_contain_text("Draft")
    policy.locator("input[id^=clause-ref-]").fill("4.2")
    policy.locator("input[id^=clause-text-]").fill("Access is reviewed every quarter")
    option_value = policy.locator("select[id^=clause-control-] option", has_text=policy_world["control"]).first.get_attribute("value")
    policy.locator("select[id^=clause-control-]").select_option(option_value)
    _reload_after(page, lambda: policy.get_by_role("button", name="Bind clause").click())

    policy = page.locator('[data-testid="policy"]', has_text=name)
    expect(policy.locator('[data-testid="clause-row"]')).to_have_count(1)
    expect(policy.locator('[data-testid="governed-application"]')).to_contain_text(policy_world["application"])
    _reload_after(page, lambda: policy.get_by_role("button", name="Publish").click())

    def assert_published_and_flagged():
        policy = page.locator('[data-testid="policy"]', has_text=name)
        expect(policy.locator('[data-testid="policy-state"]')).to_contain_text("Version 1, in force")
        expect(policy.locator('[data-testid="governed-application"]')).to_contain_text(policy_world["application"])
        violation = page.locator('[data-testid="policy-violation-row"]', has_text=policy_world["application"])
        expect(violation).to_be_visible()
        expect(violation).to_contain_text("4.2")
        expect(violation).to_contain_text(policy_world["control"])
        expect(violation).to_contain_text("evidence has expired")

    assert_published_and_flagged()
    page.goto(live_server + "/dashboard/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert_published_and_flagged()

    # Impact-assessment flag: health data used by an AI system names both criteria.
    row = page.locator('[data-testid="privacy-row"]', has_text=policy_world["activity"])
    expect(row).to_be_visible()
    expect(row.locator('[data-testid="privacy-reading"]')).to_have_text("Assessment likely required")
    expect(row).to_contain_text("Special-category data")
    expect(row).to_contain_text("Automated evaluation by an AI system")
    _reload_after(page, lambda: row.locator("select").select_option("completed"))
    row = page.locator('[data-testid="privacy-row"]', has_text=policy_world["activity"])
    expect(row.locator("select")).to_have_value("completed")
