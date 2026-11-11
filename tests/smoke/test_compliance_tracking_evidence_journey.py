"""A compliance officer reads each capability's figure on Compliance Tracking and sees what it rests on.

The goal: every assessed row carries its evidence, source, confidence and date, the links in it open pages
with content, and a capability with no mapped control still says what to do next. The figures on the page
are the ones the page showed before.
Seeded with two organisations; the second one's records must never show in the first one's rows.
"""

import uuid

import pytest

from .conftest import PAGE_TIMEOUT
from .test_compliance_one_answer_journey import _login, _open, _org, _shot

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

EVIDENCE = "https://evidence.example.com/tracking/access-review"


def _estate(db, org, user, framework, controls, label, suffix):
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capability import BusinessCapability
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    ApplicabilityService.adopt_framework(org.id, framework.id, adopted_by_id=user.id)
    names = ["%s %s %s" % (label, n, suffix) for n in ("Settlement", "Fulfilment", "Archive")]
    caps = [BusinessCapability(name=n, organization_id=org.id) for n in names]
    ledger = ApplicationComponent(name="%s Ledger %s" % (label, suffix), organization_id=org.id)
    orders = ApplicationComponent(name="%s Orders %s" % (label, suffix), organization_id=org.id)
    db.session.add_all(caps + [ledger, orders])
    db.session.flush()
    for cap, app_row in ((caps[0], ledger), (caps[1], orders)):
        db.session.add(ApplicationCapabilityMapping(
            organization_id=org.id, application_component_id=app_row.id, business_capability_id=cap.id,
            support_level="full", coverage_percentage=80, is_active=True))
    # The ledger: one implemented control with a document, one still to do without.
    ApplicabilityService.link_requirement(org.id, ledger.id, controls[0].id, "implemented",
                                          evidence_url=EVIDENCE)
    ApplicabilityService.link_requirement(org.id, ledger.id, controls[1].id, "planned")
    # Orders: two controls implemented, no document recorded for either.
    for control in controls[:2]:
        ApplicabilityService.link_requirement(org.id, orders.id, control.id, "implemented")
    db.session.commit()
    return names


@pytest.fixture
def world(app, live_server):
    from app import db
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.models.user import Role

    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        Role.insert_roles()
        framework = RegulatoryFramework(code="T-%s" % suffix, name="Tracking standard %s" % suffix, status="active")
        db.session.add(framework)
        db.session.flush()
        controls = [ComplianceControl(framework_id=framework.id, control_code="T.%d-%s" % (n, suffix),
                                      title="Tracking control %d %s" % (n, suffix)) for n in range(2)]
        db.session.add_all(controls)
        db.session.commit()
        org_a, user_a = _org(db, "Alpha", suffix)
        org_b, user_b = _org(db, "Bravo", suffix)
        names_a = _estate(db, org_a, user_a, framework, controls, "Alpha", suffix)
        names_b = _estate(db, org_b, user_b, framework, controls, "Bravo", suffix)
        return {"email": user_a.email, "caps": names_a, "other": names_b}


def _row(page, name):
    return page.locator('[data-testid="compliance-tracking-row"]', has_text=name)


def _part(row, part):
    return " ".join(row.locator('[data-testid="result-footer-%s"]' % part).inner_text().split())


def test_each_row_carries_evidence_source_confidence_and_date(browser, live_server, world):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        _open(page, live_server, "/strategic/compliance-tracking")
        settle, fulfil, archive = world["caps"]
        assert page.locator('[data-testid="compliance-tracking-row"]').count() == 3

        # Every row, assessed or not, carries the four parts.
        for name in world["caps"]:
            row = _row(page, name)
            assert row.locator('[data-testid="result-footer"]').count() == 1, name
            for part in ("evidence", "source", "confidence", "date"):
                assert _part(row, part), (name, part)

        # An assessed row: the figure is unchanged and the evidence is stated.
        row = _row(page, settle)
        assert row.locator('[data-testid="compliance-figure"]').inner_text().strip() == "50%"
        assert "2 controls mapped, 1 in place" in _part(row, "evidence")
        assert "Evidence document recorded for 1 of 2 controls" in _part(row, "evidence")
        assert "entered by the team" in _part(row, "source")
        assert row.locator('[data-testid="result-footer"]').get_attribute("data-confidence") == "medium"
        assert "newest input recorded" in _part(row, "date")

        # Controls with no document say so, and the confidence drops to low.
        row = _row(page, fulfil)
        assert row.locator('[data-testid="compliance-figure"]').inner_text().strip() == "100%"
        assert "No evidence document is recorded for any of its 2 controls" in _part(row, "evidence")
        assert row.locator('[data-testid="result-footer"]').get_attribute("data-confidence") == "low"

        # A capability with no mapped control keeps its next step and names what is missing.
        row = _row(page, archive)
        assert "Not assessed" in row.inner_text()
        assert "Map the controls its applications must meet to assess it." in row.inner_text()
        assert "No control is mapped to the applications that support it" in _part(row, "evidence")
        assert row.locator('[data-testid="result-footer"]').get_attribute("data-confidence") == "low"
        _shot(page, "compliance-tracking-evidence", 1440, "light")

        # Nothing from the other organisation.
        body = page.locator("main").inner_text()
        for other in world["other"]:
            assert other not in body

        # The controls link lands on the dashboard that lists them, with content.
        _row(page, settle).locator('[data-testid="result-footer-evidence"] a', has_text="controls mapped").click()
        page.wait_for_selector('[data-testid="compliance-findings-table"]')
        assert "Alpha Ledger" in page.locator("main").inner_text()

        # The capability link lands on that capability.
        _open(page, live_server, "/strategic/compliance-tracking")
        _row(page, fulfil).locator('[data-testid="result-footer-evidence"] a', has_text=fulfil).click()
        page.wait_for_selector("main h1")
        assert fulfil in page.locator("main").inner_text()
    finally:
        ctx.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_rows_read_at_phone_width(browser, live_server, world, scheme):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme=scheme)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        _open(page, live_server, "/strategic/compliance-tracking")
        row = _row(page, world["caps"][0])
        assert row.locator('[data-testid="result-footer"]').count() == 1
        assert "Evidence" in row.inner_text()
        _shot(page, "compliance-tracking-evidence", 390, scheme)
    finally:
        ctx.close()
