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
    """Each organisation maps ``len(controls)`` controls to its applications, so the two organisations' counts differ."""
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
    # The ledger: one implemented control with a document, the rest still to do without.
    ApplicabilityService.link_requirement(org.id, ledger.id, controls[0].id, "implemented",
                                          evidence_url=EVIDENCE)
    for control in controls[1:]:
        ApplicabilityService.link_requirement(org.id, ledger.id, control.id, "planned")
    # Orders: every control implemented, no document recorded for any.
    for control in controls:
        ApplicabilityService.link_requirement(org.id, orders.id, control.id, "implemented")
    db.session.commit()
    return names, caps, orders


def _record_origins(db, org, settlement, orders):
    """Settlement was imported (its model element says so); the Orders controls came from a register, while the
    other records were entered by hand."""
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.archimate_core import ArchiMateElement

    capability_element = ArchiMateElement(name=settlement.name, type="BusinessFunction", layer="business",
                                          organization_id=org.id, source_kind="import",
                                          source_label="Capability workbook")
    controls_element = ArchiMateElement(name="Orders controls", type="BusinessFunction", layer="business",
                                        organization_id=org.id, source_kind="import",
                                        source_label="Controls register")
    db.session.add_all([capability_element, controls_element])
    db.session.flush()
    settlement.archimate_element_id = capability_element.id
    for mapping in ApplicationComplianceControl.query.filter_by(organization_id=org.id, application_id=orders.id):
        mapping.archimate_element_id = controls_element.id
    db.session.commit()


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
                                      title="Tracking control %d %s" % (n, suffix)) for n in range(3)]
        db.session.add_all(controls)
        db.session.commit()
        org_a, user_a = _org(db, "Alpha", suffix)
        org_b, user_b = _org(db, "Bravo", suffix)
        # Alpha maps two controls to each application, Bravo three: a count that crossed over would show.
        names_a, caps_a, orders_a = _estate(db, org_a, user_a, framework, controls[:2], "Alpha", suffix)
        names_b, _caps_b, _orders_b = _estate(db, org_b, user_b, framework, controls, "Bravo", suffix)
        _record_origins(db, org_a, caps_a[0], orders_a)
        return {"email": user_a.email, "email_other": user_b.email, "caps": names_a, "other": names_b}


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
        # Each fact states its own record's origin: the capability was imported, the controls were typed in.
        assert "Capability: imported from Capability workbook" in _part(row, "source")
        assert "Controls: entered by the team" in _part(row, "source")
        assert "Document: entered by the team" in _part(row, "source")
        confidence = _part(row, "confidence")
        assert confidence.startswith("Confidence: Medium Controls, Document were entered by hand")
        assert "Medium." not in confidence and "Medium ." not in confidence
        assert row.locator('[data-testid="result-footer"]').get_attribute("data-confidence") == "medium"
        assert "newest input recorded" in _part(row, "date")

        # Controls with no document say so, and the confidence drops to low.
        row = _row(page, fulfil)
        assert row.locator('[data-testid="compliance-figure"]').inner_text().strip() == "100%"
        assert "No evidence document is recorded for any of its 2 controls" in _part(row, "evidence")
        assert row.locator('[data-testid="result-footer"]').get_attribute("data-confidence") == "low"
        assert "Controls: imported from Controls register" in _part(row, "source")
        assert "Capability: entered by the team" in _part(row, "source")

        # A capability with no mapped control keeps its next step and names what is missing.
        row = _row(page, archive)
        assert "Not assessed" in row.inner_text()
        assert "Map the controls its applications must meet to assess it." in row.inner_text()
        assert "No control is mapped to the applications that support it" in _part(row, "evidence")
        assert row.locator('[data-testid="result-footer"]').get_attribute("data-confidence") == "low"
        _shot(page, "compliance-tracking-evidence", 1440, "light")

        # Nothing from the other organisation, and none of its counts: Alpha maps 2 controls to an application,
        # Bravo 3, so a count that crossed over would read 3, 4 or 5 here.
        body = page.locator("main").inner_text()
        for other in world["other"]:
            assert other not in body
        assert "3 controls mapped" not in body and "4 controls mapped" not in body
        assert "No evidence document is recorded for any of its 3 controls" not in body
        assert sorted(page.locator('[data-testid="compliance-figure"]').all_inner_texts()) == [
            "100%", "50%", "Not assessed"]

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


def test_the_other_organisation_sees_its_own_counts(browser, live_server, world):
    """Bravo maps three controls to each application; its page shows three, never Alpha's two."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email_other"])
        _open(page, live_server, "/strategic/compliance-tracking")
        settle, fulfil, _archive = world["other"]
        row = _row(page, settle)
        assert "3 controls mapped, 1 in place" in _part(row, "evidence")
        assert row.locator('[data-testid="compliance-figure"]').inner_text().strip() == "33.3%"
        row = _row(page, fulfil)
        assert "3 controls mapped, 3 in place" in _part(row, "evidence")
        assert "No evidence document is recorded for any of its 3 controls" in _part(row, "evidence")
        body = page.locator("main").inner_text()
        assert "2 controls mapped" not in body
        for other in world["caps"]:
            assert other not in body
        # Bravo's records state no origin of their own: every fact reads as entered by hand.
        assert "imported" not in _part(row, "source")
    finally:
        ctx.close()


def _headers(page):
    """Each header's label, and whether it is the topmost thing at its own centre inside the scroll box."""
    return page.evaluate("""() => {
        const table = document.querySelector('[data-testid="compliance-tracking-table"]');
        const box = table.closest('[data-slot="table-container"]').getBoundingClientRect();
        return [...table.querySelectorAll('thead th')].map((th) => {
            const r = th.getBoundingClientRect();
            const x = Math.min(Math.max(r.left + r.width / 2, box.left + 1), box.right - 1);
            const top = document.elementFromPoint(x, r.top + r.height / 2);
            return {label: th.innerText.trim(), inside: r.left >= box.left - 1 && r.right <= box.right + 1,
                    uncovered: !!top && th.contains(top)};
        });
    }""")


DECLARED = ["Capability", "Compliance Status", "Score", "Next step", "Last Review", "Evidence, source and date"]


def test_every_declared_column_is_visible_at_desktop_width(browser, live_server, world):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        _open(page, live_server, "/strategic/compliance-tracking")
        headers = _headers(page)
        assert [h["label"] for h in headers] == DECLARED
        for h in headers:
            assert h["inside"] and h["uncovered"], h
        row = _row(page, world["caps"][0])
        assert row.locator("td").nth(4).inner_text().strip() == "Not reviewed yet"
        assert "Evidence" in row.locator("td").nth(5).inner_text()
        _shot(page, "compliance-tracking-columns", 1440, "light")
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
        # The table scrolls sideways inside its card; each column, the last included, can be brought into view
        # and is then the topmost thing at its own centre.
        box = page.locator('[data-testid="compliance-tracking-table"]').locator(
            "xpath=ancestor::*[@data-slot='table-container']")
        assert box.evaluate("e => e.scrollWidth > e.clientWidth")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        for label in DECLARED:
            th = page.locator('[data-testid="compliance-tracking-table"] thead th', has_text=label)
            th.scroll_into_view_if_needed()
            page.wait_for_timeout(150)
            state = next(h for h in _headers(page) if h["label"] == label)
            assert state["inside"] and state["uncovered"], state
        box.evaluate("e => { e.scrollLeft = e.scrollWidth; }")
        page.wait_for_timeout(200)
        last = row.locator("td").nth(5)
        assert last.is_visible() and "Evidence" in last.inner_text()
        _shot(page, "compliance-tracking-evidence", 390, scheme)
    finally:
        ctx.close()
