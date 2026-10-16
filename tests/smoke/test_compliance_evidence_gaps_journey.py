"""Evidence gaps on the compliance dashboard, driven in a real browser as the compliance persona.

A seeded application has an adopted control with no evidence and another whose evidence ran out
yesterday. The persona opens /dashboard/compliance, sees both in the list, records a test as not
effective on one and assigns an evidence owner on the other, reloads, and sees both persisted.
A control with evidence valid next month is not on the list.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_gaps(org_id):
    from datetime import datetime, timedelta

    from app import create_app, db
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    app = create_app("testing")
    noun = "Evidencer %s" % uuid.uuid4().hex[:6]
    with app.app_context():
        element = ArchiMateElement(
            name=noun, type="ApplicationComponent", layer="application", organization_id=org_id,
        )
        db.session.add(element)
        db.session.commit()
        component = ApplicationComponent(name=noun, organization_id=org_id, archimate_element_id=element.id)
        framework = RegulatoryFramework(code="EG%s" % uuid.uuid4().hex[:5], name="Evidence Framework", category="security")
        db.session.add_all([component, framework])
        db.session.flush()
        controls = {
            code: ComplianceControl(framework_id=framework.id, control_code=code, title=title, priority="critical")
            for code, title in (("EG-MISS", "Access review"), ("EG-OLD", "Backup restore"), ("EG-OK", "Patching"))
        }
        db.session.add_all(controls.values())
        db.session.flush()
        rows = {}
        for code, fields in (
            ("EG-MISS", {}),
            ("EG-OLD", {
                "evidence_url": "https://evidence.example/backup",
                "evidence_valid_until": datetime.utcnow() - timedelta(days=1),
            }),
            ("EG-OK", {
                "evidence_url": "https://evidence.example/patching",
                "evidence_valid_until": datetime.utcnow() + timedelta(days=30),
            }),
        ):
            row = ApplicationComplianceControl(
                organization_id=org_id, application_id=component.id, control_id=controls[code].id,
                implementation_status="implemented", **fields,
            )
            db.session.add(row)
            db.session.flush()
            rows[code] = row.id
        db.session.commit()
    return {"noun": noun, "rows": rows}


@pytest.fixture(scope="module")
def evidence_gaps(seeded, live_server):
    return _seed_gaps(seeded["ids"]["org"])


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


def _row(page, row_id):
    return page.locator('[data-evidence-gap-row="%d"]' % row_id)


def test_the_compliance_persona_sees_the_gaps_records_a_test_assigns_an_owner_and_both_persist(
    page, live_server, seeded, evidence_gaps
):
    rows = evidence_gaps["rows"]
    _login(page, live_server, seeded["emails"]["compliance"])
    page.goto(live_server + "/dashboard/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    section = page.locator("#evidence-gaps")
    expect(section).to_be_visible()
    expect(_row(page, rows["EG-MISS"])).to_contain_text("No evidence")
    expect(_row(page, rows["EG-OLD"])).to_contain_text("Expired")
    expect(_row(page, rows["EG-MISS"])).to_contain_text(evidence_gaps["noun"])
    assert _row(page, rows["EG-OK"]).count() == 0, "a control with current evidence is not a gap"

    # Record a design test and then a not-effective operating test on the expired control.
    _row(page, rows["EG-OLD"]).get_by_role("button", name="Record test").click()
    panel = section.locator("form:visible")
    panel.locator("select").nth(0).select_option("design")
    panel.locator("select").nth(1).select_option("effective")
    with page.expect_navigation(wait_until="domcontentloaded"):
        panel.get_by_role("button", name="Save test").click()

    _row(page, rows["EG-OLD"]).get_by_role("button", name="Record test").click()
    panel = section.locator("form:visible")
    panel.locator("select").nth(0).select_option("operating")
    panel.locator("select").nth(1).select_option("ineffective")
    panel.locator("input[type=text]").fill("Restore failed on the second sample")
    with page.expect_navigation(wait_until="domcontentloaded"):
        panel.get_by_role("button", name="Save test").click()

    # Assign an evidence owner on the missing control, picked from the live user search.
    _row(page, rows["EG-MISS"]).get_by_role("button", name="Assign evidence owner").click()
    panel = section.locator("form:visible")
    panel.locator("input[type=text]").fill("Smoke cto")
    panel.locator("[role=option]", has_text="Smoke cto").click()
    with page.expect_navigation(wait_until="domcontentloaded"):
        panel.get_by_role("button", name="Assign", exact=True).click()

    # After a reload the same list, test and owner are there.
    page.goto(live_server + "/dashboard/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(_row(page, rows["EG-OLD"])).to_contain_text("Not effective")
    expect(_row(page, rows["EG-OLD"])).to_contain_text("Restore failed on the second sample")
    expect(page.locator('[data-evidence-owner="%d"]' % rows["EG-MISS"])).to_have_text("Smoke cto")
    expect(_row(page, rows["EG-MISS"])).to_contain_text("No evidence")
    assert _row(page, rows["EG-OK"]).count() == 0
