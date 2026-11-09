"""A compliance officer asks "how many controls are not in place?" and "what backs each finding?".

The goal: every page that states the count of controls not in place states the same number with the
same words, and every finding says what it rests on and opens its application and its capability.

Seeded: one organisation whose single application serves two capabilities (so a count summed per
capability would double it) with two planned, one in-progress and one verified control, one finding
carrying an evidence document; a second organisation whose controls must never be counted.

* the dashboard and Compliance Tracking both say 3 controls are not in place (2 not started, 1 in progress);
* the findings list has one row for each of the 3, each with evidence, source, confidence and date;
* the application link opens that application; the capability link opens that capability;
* the evidence document is linked, and a finding without one says so;
* at phone width neither page scrolls sideways.
"""

import os
import re
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_compliance_one_answer_journey import _login, _open, _org, _shot

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

EVIDENCE = "https://evidence.example.com/access-review"


@pytest.fixture
def world(app, live_server):
    from app import db
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capability import BusinessCapability
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.models.user import Role
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        Role.insert_roles()
        framework = RegulatoryFramework(code="C7-%s" % suffix, name="Seven standard %s" % suffix, status="active")
        db.session.add(framework)
        db.session.flush()
        controls = [
            ComplianceControl(framework_id=framework.id, control_code="C7.%d-%s" % (n, suffix), title="%s %s" % (t, suffix))
            for n, t in enumerate(("Access Control", "Cryptography", "Backups", "Logging"))
        ]
        db.session.add_all(controls)
        db.session.commit()
        built = {}
        for label, statuses in (("Alpha", ("planned", "planned", "in_progress", "verified")),
                                ("Bravo", ("planned", "planned", "planned", "planned"))):
            org, user = _org(db, label, suffix)
            ApplicabilityService.adopt_framework(org.id, framework.id, adopted_by_id=user.id)
            caps = [BusinessCapability(name="%s %s %s" % (label, n, suffix), organization_id=org.id)
                    for n in ("Settlement", "Reporting")]
            application = ApplicationComponent(name="%s Ledger %s" % (label, suffix), organization_id=org.id)
            db.session.add_all(caps + [application])
            db.session.flush()
            for cap in caps:
                db.session.add(ApplicationCapabilityMapping(
                    organization_id=org.id, application_component_id=application.id,
                    business_capability_id=cap.id, support_level="full", coverage_percentage=80, is_active=True))
            for index, (control, status) in enumerate(zip(controls, statuses)):
                link = ApplicabilityService.link_requirement(org.id, application.id, control.id, status)
                if label == "Alpha" and index == 0:
                    link.evidence_url = EVIDENCE
            db.session.commit()
            built[label] = {"email": user.email, "app": application.name, "caps": [c.name for c in caps],
                            "codes": [c.control_code for c in controls]}
        return built


def _footer_text(row):
    return " ".join(row.locator('[data-testid="result-footer"]').inner_text().split())


def test_one_count_and_findings_with_evidence(browser, live_server, world):
    alpha = world["Alpha"]
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, alpha["email"])

        # Blocker 13: the same count, in the same words, on both pages.
        _open(page, live_server, "/dashboard/compliance")
        said = " ".join(page.locator('[data-testid="not-in-place-count"]').inner_text().split())
        assert said.startswith("3 controls not in place: 2 not started and 1 in progress"), said
        rows = page.locator('[data-testid="compliance-finding-row"]')
        assert rows.count() == 3
        _shot(page, "acc7comp-dashboard", 1440, "light")

        _open(page, live_server, "/strategic/compliance-tracking")
        tile = page.get_by_text("Controls Not In Place").first.locator("xpath=ancestor::*[contains(@class,'rounded')][1]")
        tile_text = " ".join(tile.inner_text().split())
        assert re.search(r"Controls Not In Place\s*3\b", tile_text), tile_text
        assert "2 not started, 1 in progress" in tile_text, tile_text
        _shot(page, "acc7comp-tracking", 1440, "light")

        # Blocker 14: every finding says what it rests on.
        _open(page, live_server, "/dashboard/compliance")
        rows = page.locator('[data-testid="compliance-finding-row"]')
        for i in range(rows.count()):
            footer = _footer_text(rows.nth(i))
            for word in ("Evidence", "Source", "Confidence", "Date"):
                assert word in footer, footer
        with_evidence = rows.filter(has_text=alpha["codes"][0])
        assert with_evidence.locator('[data-testid="result-footer-evidence"] a[href="%s"]' % EVIDENCE).count() == 1
        without = rows.filter(has_text=alpha["codes"][1])
        assert "No evidence document is recorded" in _footer_text(without)
        assert without.locator('[data-testid="result-footer"]').get_attribute("data-confidence") == "low"

        # The application opens its record.
        link = rows.first.locator('[data-testid="finding-application"]')
        assert link.inner_text().strip() == alpha["app"]
        link.click()
        page.wait_for_selector("main h1")
        assert alpha["app"] in page.locator("main").inner_text()
        # Each capability opens its record.
        for name in alpha["caps"]:
            _open(page, live_server, "/dashboard/compliance")
            page.locator('[data-testid="compliance-finding-row"]').first.locator(
                '[data-testid="finding-capability"]', has_text=name).click()
            page.wait_for_selector("main h1")
            assert name in page.locator("main").inner_text(), name
        body = ""
        _open(page, live_server, "/dashboard/compliance")
        body = page.locator("main").inner_text()
        for other in (world["Bravo"]["app"], *world["Bravo"]["caps"]):
            assert other not in body
    finally:
        ctx.close()


def test_count_pages_hold_at_phone_width(browser, live_server, world):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["Alpha"]["email"])
        for key, path in (("dashboard", "/dashboard/compliance"), ("tracking", "/strategic/compliance-tracking")):
            _open(page, live_server, path)
            assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1, key
            _shot(page, "acc7comp-%s" % key, 390, "dark")
    finally:
        ctx.close()
