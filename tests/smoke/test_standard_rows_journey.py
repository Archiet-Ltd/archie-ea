"""A security lead opens ISO 27001, SOC 2 and GDPR on the compliance screen and finds each requirement under the
right code, with where it comes from and no rating nobody gave it.

The person: a security lead who has just signed up (or whose organisation adopted these standards earlier and holds
the earlier rows). They read the criteria for retiring assets and for unauthorised disclosure, check the reference
column, and look for the transfer articles the regulation places on a controller. Every row has a reference; the
priority column reads "Not set", never a rating.
"""

import re

import pytest
from playwright.sync_api import expect

from tests.smoke.test_signup_brings_the_company_in_journey import (  # noqa: F401  (the module's fixtures)
    new_page,
    people,
    provider,
    server,
    shoot,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

KEYS = ["iso27001", "soc2", "gdpr"]


def _wait_adopted(page, base, label):
    import time

    deadline = time.monotonic() + 120
    while True:
        page.goto(base + "/dashboard/compliance", wait_until="domcontentloaded")
        if page.locator("tr", has_text=re.compile(label)).filter(has_text="Adopted").count():
            return
        assert time.monotonic() < deadline, "%s never loaded: %s" % (label, page.locator("main").inner_text()[:300])
        page.wait_for_timeout(3000)


def _open(page, label):
    page.locator("tr", has_text=re.compile(label)).filter(has_text="Adopted").first.click()
    panel = page.locator("td[colspan='7']:visible").first
    expect(panel.locator("tbody tr").first).to_be_visible()
    return panel


def _cells(panel, position):
    return [row.locator("td").nth(position).inner_text().strip() for row in panel.locator("tbody tr").all()]


def _line(panel, code):
    return next(t for t in panel.locator("tbody tr").all_inner_texts() if re.match(r"\s*%s\b" % re.escape(code), t))


def _read_all_three(page, width):
    iso = _open(page, r"ISO/IEC 27001|ISO - 27001")
    lines = iso.locator("tbody tr").all_inner_texts()
    assert len(lines) == 93
    a82 = _line(iso, "A.8.2")
    assert "Tight control of administrator-level access" in a82 and "ISO/IEC 27001:2022 Annex A.8.2" in a82
    assert "Not set" in a82 and "Access Control" not in a82
    assert set(_cells(iso, 4)) == {"Not set"} and "—" not in _cells(iso, 3) and "Not recorded" not in _cells(iso, 3)
    _shoot(page, "iso", width)
    page.locator("tr", has_text=re.compile(r"ISO/IEC 27001|ISO - 27001")).filter(has_text="Adopted").first.click()

    soc = _open(page, r"SOC")
    lines = soc.locator("tbody tr").all_inner_texts()
    assert len(lines) == 61
    assert "Retiring assets" in _line(soc, "CC6.5") and "TSC 2017 CC6.5" in _line(soc, "CC6.5")
    assert "malicious software" in _line(soc, "CC6.8")
    assert "unauthorised disclosures" in _line(soc, "P6.3")
    assert "Telling people and regulators about breaches" in _line(soc, "P6.6")
    assert set(_cells(soc, 4)) == {"Not set"} and "Not recorded" not in _cells(soc, 3)
    _shoot(page, "soc2", width)
    page.locator("tr", has_text=re.compile(r"SOC")).filter(has_text="Adopted").first.click()

    gdpr = _open(page, r"General Data Protection|GDPR")
    lines = gdpr.locator("tbody tr").all_inner_texts()
    assert len(lines) == 40
    for article in (45, 47, 49):
        assert "Regulation (EU) 2016/679, Article %d" % article in _line(gdpr, "Article - %d" % article)
    expect(page.get_by_test_id("scope-note")).to_contain_text("duty on a controller or processor")
    assert set(_cells(gdpr, 4)) == {"Not set"} and "Not recorded" not in _cells(gdpr, 3)
    _shoot(page, "gdpr", width)
    expect(page.locator("main")).not_to_contain_text(re.compile(r"compliant|certified", re.I))


def _shoot(page, name, width):
    import os

    shots = os.environ.get("SIGNUP_JOURNEY_SHOTS")
    if shots:
        os.makedirs(shots, exist_ok=True)
        page.screenshot(path=os.path.join(shots, "rows-%s-%d.png" % (name, width)), full_page=True)


@pytest.mark.parametrize("width", [1440, 390], ids=["1440", "390"])
def test_a_security_lead_who_just_signed_up_reads_the_right_rows(browser, server, people, width):
    base = str(server)
    context, page = new_page(browser, width)
    try:
        sign_up(page, base, people)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        page.get_by_role("button", name="Show all frameworks").click()
        for label in ("ISO 27001", "SOC 2", "GDPR"):
            page.get_by_label(label, exact=True).check()
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")
        _wait_adopted(page, base, r"GDPR|General Data")
        _wait_adopted(page, base, r"SOC")
        _wait_adopted(page, base, r"ISO/IEC 27001|ISO - 27001")
        _read_all_three(page, width)
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390], ids=["1440", "390"])
def test_a_security_lead_whose_organisation_holds_the_earlier_rows_sees_them_corrected(browser, server, people, app, width):
    from tests.test_standard_catalogues_published import OLD_GDPR, OLD_ISO, OLD_SOC

    context, page = new_page(browser, width)
    try:
        email, _started = sign_up(page, str(server), people)
        with app.app_context():
            from app import db
            from app.models.compliance_models import ComplianceControl, RegulatoryFramework
            from app.models.user import User
            from app.modules.account.services import onboarding_service as onboarding

            org_id = User.query.filter_by(email=email).one().organization_id
            onboarding._adopt_frameworks(org_id, KEYS)
            for code, old in (("ISO - 27001", OLD_ISO), ("SOC-2", OLD_SOC), ("GDPR", OLD_GDPR)):
                framework = RegulatoryFramework.query.filter_by(code=code).one()
                for control, (title, heading, description, priority) in old.items():
                    row = ComplianceControl.query.filter_by(framework_id=framework.id, control_code=control).one()
                    row.title, row.category, row.description, row.priority = title, heading, description, priority
                    row.official_reference = None
            db.session.commit()
            db.session.remove()
            onboarding._adopt_frameworks(org_id, KEYS)
            db.session.remove()
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        page.goto(str(server) + "/dashboard/compliance", wait_until="domcontentloaded")
        _read_all_three(page, width)
    finally:
        context.close()
