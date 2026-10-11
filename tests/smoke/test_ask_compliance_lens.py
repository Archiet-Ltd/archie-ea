"""Compliance (under L6), "which regulations and controls apply to <element>, and which controls
have no evidence of being met?": the Compliance question on the Ask page, in a real browser.

A seeded application has two mapped controls, one with evidence and one with none, and an open
policy violation. The answer shows both controls with the missing evidence stated, the violation,
and "no scan recorded" (nothing is shown as a percentage or a zero). The evidence URL never
appears. It shares one picker with the other questions.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, type_and_wait

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_compliance_graph(org_id):
    from app import create_app, db
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.models.policy_monitoring import ArchitecturePolicy, PolicyViolation

    app = create_app("testing")
    noun = "Ledgerc %s" % uuid.uuid4().hex[:6]
    with app.app_context():
        service = ArchiMateElement(
            name="%s Service" % noun, type="ApplicationComponent", layer="application", organization_id=org_id,
        )
        target = ArchiMateElement(
            name="%s Target" % noun, type="ApplicationComponent", layer="application", organization_id=org_id,
        )
        db.session.add_all([service, target])
        db.session.commit()
        component = ApplicationComponent(name=service.name, organization_id=org_id, archimate_element_id=service.id)
        framework = RegulatoryFramework(code="SM%s" % uuid.uuid4().hex[:5], name="Smoke Framework", category="security")
        db.session.add_all([component, framework])
        db.session.flush()
        with_evidence = ComplianceControl(framework_id=framework.id, control_code="EV-1", title="Has evidence")
        without = ComplianceControl(framework_id=framework.id, control_code="NE-1", title="Lacks evidence")
        policy = ArchitecturePolicy(name="Smoke policy", organization_id=org_id)
        db.session.add_all([with_evidence, without, policy])
        db.session.flush()
        db.session.add(ApplicationComplianceControl(
            organization_id=org_id, application_id=component.id, control_id=with_evidence.id,
            implementation_status="implemented", evidence_url="https://secret.example/evidence",
        ))
        db.session.add(ApplicationComplianceControl(
            organization_id=org_id, application_id=component.id, control_id=without.id,
            implementation_status="planned",
        ))
        db.session.add(PolicyViolation(
            policy_id=policy.id, entity_type="application", entity_id=component.id,
            severity="high", status="open", organization_id=org_id,
        ))
        db.session.add(ArchiMateRelationship(
            type="Serving", source_id=service.id, target_id=target.id, organization_id=org_id,
        ))
        db.session.commit()
    return {"noun": noun}


@pytest.fixture(scope="module")
def compliance_graph(seeded, live_server):
    return _seed_compliance_graph(seeded["ids"]["org"])


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


def _ready(page, factory):
    page.wait_for_function(
        "(f) => { const el = document.querySelector('[x-data=\"' + f + '()\"]');"
        " return !!(el && el._x_dataStack); }",
        arg=factory,
    )


def _pick(page, noun, option_text):
    type_and_wait(page, "ask", noun)
    page.locator("#ask-picker-listbox [role=option]", has_text=option_text).click()


def test_the_compliance_question_states_missing_evidence_and_never_a_percentage(
    page, live_server, seeded, compliance_graph
):
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")

    card = page.locator("#ask-question-compliance")
    expect(card).to_be_visible()
    expect(page.get_by_text("Which regulations and controls apply to")).to_be_visible()
    expect(page.get_by_text("and which controls have no evidence of being met?")).to_be_visible()

    card.click()
    expect(page.locator("#ask-picker-input")).to_be_focused()
    _pick(page, compliance_graph["noun"], "Service")

    rows = page.locator("[data-ask-compliance-row]")
    expect(rows).to_have_count(2)
    expect(rows.filter(has_text="EV-1")).to_contain_text("Evidence recorded")
    expect(rows.filter(has_text="NE-1")).to_contain_text("No evidence recorded and not verified")
    expect(page.locator("[data-ask-compliance-violation]")).to_contain_text("Smoke policy")
    expect(page.get_by_text("No policy scan is recorded for this element.")).to_be_visible()

    body = page.locator("#ask-compliance-results").inner_text()
    assert "secret.example" not in body and "%" not in body


def test_an_application_with_no_mapping_says_so_and_the_questions_stay_separate(
    page, live_server, seeded, compliance_graph
):
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto(live_server + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _ready(page, "askSurface")

    page.locator("#ask-question-compliance").click()
    _pick(page, compliance_graph["noun"], "Target")
    expect(page.get_by_text("No compliance controls are recorded for this element yet.")).to_be_visible()
    assert page.locator("[data-ask-compliance-row]").count() == 0

    page.locator("#ask-question-impact").click()
    _pick(page, compliance_graph["noun"], "Service")
    page.wait_for_selector("[data-ask-row]")
    expect(page.locator("#ask-results")).to_be_visible()
    expect(page.locator("#ask-compliance-results")).to_be_hidden()
