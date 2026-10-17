"""The compliance officer's evidence journey, in a real browser: open the compliance
dashboard, see the control whose evidence has expired, record a failed test, assign an
evidence owner, reload, and see both still there.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed_expired_control(org_id):
    from app import create_app, db
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_portfolio import ApplicationComponent
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    app = create_app("testing")
    code = "EG-%s" % uuid.uuid4().hex[:5].upper()
    with app.app_context():
        framework = RegulatoryFramework(code="EG%s" % uuid.uuid4().hex[:5], name="Evidence framework %s" % code, category="security")
        component = ApplicationComponent(name="Evidence ledger %s" % code, organization_id=org_id)
        db.session.add_all([framework, component])
        db.session.flush()
        control = ComplianceControl(framework_id=framework.id, control_code=code, title="Reviewed access", priority="high")
        db.session.add(control)
        db.session.flush()
        row = ApplicationComplianceControl(
            organization_id=org_id, application_id=component.id, control_id=control.id,
            implementation_status="implemented", evidence_url="https://evidence.example.test/old",
            evidence_valid_until=datetime.utcnow() - timedelta(days=3),
        )
        db.session.add(row)
        db.session.commit()
        return {"code": code, "row_id": row.id}


def _owner_id(row_id):
    from app import create_app, db
    from app.models.application_compliance import ApplicationComplianceControl

    with create_app("testing").app_context():
        return db.session.execute(
            db.select(ApplicationComplianceControl.evidence_owner_id).where(ApplicationComplianceControl.id == row_id)
        ).scalar_one()


@pytest.fixture(scope="module")
def expired_control(seeded, live_server):
    return _seed_expired_control(seeded["ids"]["org"])


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


def test_the_officer_sees_the_gap_records_a_test_assigns_an_owner_and_both_persist(
    page, live_server, seeded, expired_control
):
    _login(page, live_server, seeded["emails"]["compliance"])
    page.goto(live_server + "/dashboard/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.locator('[data-control-row="%d"]' % expired_control["row_id"])
    expect(row).to_be_visible()
    expect(row).to_contain_text(expired_control["code"])
    expect(row).to_contain_text("Expired")

    # The framework rows on the same page expand to their controls.
    page.get_by_role("button", name="Show controls for Evidence framework %s" % expired_control["code"]).click()
    expect(page.locator("td[colspan='7'] table").get_by_text(expired_control["code"])).to_be_visible()

    row.get_by_role("button", name="Record test").click()
    panel = page.locator('[data-testid="evidence-action-panel"]')
    expect(panel).to_be_visible()
    page.select_option("#control-test-result", "ineffective")
    page.fill("#control-test-notes", "Access reviews were skipped")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        panel.get_by_role("button", name="Save").click()

    row = page.locator('[data-control-row="%d"]' % expired_control["row_id"])
    expect(row.locator('[data-testid="latest-test"]')).to_have_text("Failed")

    row.get_by_role("button", name="Assign owner").click()
    page.fill("#evidence-owner-search", seeded["emails"]["compliance"].split("@")[0][:6])
    option = page.locator("[data-testid=evidence-action-panel] button", has_text="@").first
    expect(option).to_be_visible()
    option.click()
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.locator('[data-testid="evidence-action-panel"]').get_by_role("button", name="Save").click()

    page.goto(live_server + "/dashboard/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.locator('[data-control-row="%d"]' % expired_control["row_id"])
    expect(row.locator('[data-testid="latest-test"]')).to_have_text("Failed")
    # The seeded person has no recorded name, so the column shows a dash (never an
    # email address); what persisted is the owner id on the control row.
    assert _owner_id(expired_control["row_id"]) is not None


def _seed_untested_control(org_id):
    """Current evidence and no tests, under a control code that would break a naive handler."""
    from app import create_app, db
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_portfolio import ApplicationComponent
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    with create_app("testing").app_context():
        tag = uuid.uuid4().hex[:5].upper()
        framework = RegulatoryFramework(code="UT%s" % tag, name="Untested framework %s" % tag, category="security")
        component = ApplicationComponent(name="Untested ledger %s" % tag, organization_id=org_id)
        db.session.add_all([framework, component])
        db.session.flush()
        code = "UT'%s" % tag
        control = ComplianceControl(framework_id=framework.id, control_code=code, title="Quarterly review", priority="low")
        db.session.add(control)
        db.session.flush()
        row = ApplicationComplianceControl(
            organization_id=org_id, application_id=component.id, control_id=control.id,
            implementation_status="implemented", evidence_url="https://evidence.example.test/fresh",
            evidence_valid_until=datetime.utcnow() + timedelta(days=90),
        )
        db.session.add(row)
        db.session.commit()
        return {"code": code, "row_id": row.id}


def _test_results(row_id):
    from app import create_app, db
    from app.models.control_test import ControlTest

    with create_app("testing").app_context():
        return [
            (t.test_kind, t.result)
            for t in db.session.execute(db.select(ControlTest).where(ControlTest.application_control_id == row_id)).scalars()
        ]


def test_the_officer_records_the_first_test_for_a_control_that_is_not_on_the_gap_list(
    page, live_server, seeded
):
    control = _seed_untested_control(seeded["ids"]["org"])
    _login(page, live_server, seeded["emails"]["compliance"])
    page.goto(live_server + "/dashboard/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    row = page.locator('[data-testid="untested-control-row"][data-control-row="%d"]' % control["row_id"])
    expect(row).to_be_visible()
    row.get_by_role("button", name="Record test").click()
    panel = page.locator('[data-testid="evidence-action-panel"]')
    expect(panel).to_contain_text(control["code"])  # the code reached the handler intact
    page.select_option("#control-test-result", "exception")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        panel.get_by_role("button", name="Save").click()

    # The recorded exception persisted and the control now ranks on the gap list.
    assert _test_results(control["row_id"]) == [("design", "exception")]
    listed = page.locator('[data-testid="evidence-gap-row"][data-control-row="%d"]' % control["row_id"])
    expect(listed.locator('[data-testid="latest-test"]')).to_have_text("Exception")
