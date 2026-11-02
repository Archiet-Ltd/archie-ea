"""A compliance officer finds one answer on every compliance page, and a finding to act on.

The goal: see how compliant one capability is, see the same figure everywhere it is shown, and open
a missing control from the findings list. Seeded with ISO-style controls mapped to two applications
(one capability assessed, one not), plus a second organisation whose records must never show.

* tracking, capability health and the compliance dashboard show the same figure for the capability;
* the unassessed capability reads "Not assessed" with a next step, never 100% or 0%;
* the missing control is listed as a finding and opens a page that names it and its application;
* recommendations are sentences, and no page prints a Python dictionary;
* the old enterprise compliance address lands on the dashboard for a browser;
* the other organisation's capability and application appear nowhere.
"""

import os
import re
import uuid
from datetime import datetime

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("SMOKE_WALK_DIR")


def _org(db, label, suffix):
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.services.billing_plans import set_contract_plan

    org = Organization(name="%s %s" % (label, suffix), slug="%s-%s" % (label.lower(), suffix))
    db.session.add(org)
    db.session.flush()
    set_contract_plan(org, "enterprise", None)
    db.session.commit()
    user = User(
        email="%s.%s@example.com" % (label.lower(), suffix), first_name=label, last_name="Officer",
        organization_id=org.id, enterprise_role="platform_admin", confirmed=True,
    )
    user.role = Role.query.filter_by(name="Administrator").one()
    user.is_platform_admin = True
    user.is_org_admin = True
    user.password = PASSWORD
    user.onboarding_completed_at = datetime.utcnow()
    db.session.add(user)
    db.session.commit()
    return org, user


def _estate(db, org, user, framework, controls, label, suffix, statuses):
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capability import BusinessCapability
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    ApplicabilityService.adopt_framework(org.id, framework.id, adopted_by_id=user.id)
    cap = BusinessCapability(name="%s Settlement %s" % (label, suffix), organization_id=org.id)
    bare = BusinessCapability(name="%s Archive %s" % (label, suffix), organization_id=org.id)
    app_row = ApplicationComponent(name="%s Ledger %s" % (label, suffix), organization_id=org.id)
    db.session.add_all([cap, bare, app_row])
    db.session.flush()
    db.session.add(ApplicationCapabilityMapping(
        organization_id=org.id, application_component_id=app_row.id, business_capability_id=cap.id,
        support_level="full", coverage_percentage=80, is_active=True,
    ))
    for control, status in zip(controls, statuses):
        ApplicabilityService.link_requirement(org.id, app_row.id, control.id, status)
    db.session.commit()
    return cap, bare, app_row


@pytest.fixture
def world(app, live_server):
    from app import db
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.models.user import Role

    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        Role.insert_roles()
        framework = RegulatoryFramework(code="J-%s" % suffix, name="Journey standard %s" % suffix, status="active")
        db.session.add(framework)
        db.session.flush()
        controls = [
            ComplianceControl(framework_id=framework.id, control_code="J.%d-%s" % (n, suffix), title="%s %s" % (t, suffix))
            for n, t in enumerate(("Access Control", "Cryptography", "Information Deletion"))
        ]
        db.session.add_all(controls)
        db.session.commit()
        org_a, user_a = _org(db, "Alpha", suffix)
        org_b, user_b = _org(db, "Bravo", suffix)
        cap, bare, app_a = _estate(db, org_a, user_a, framework, controls, "Alpha", suffix,
                                   ("implemented", "in_progress", "planned"))
        cap_b, _bare_b, app_b = _estate(db, org_b, user_b, framework, controls, "Bravo", suffix,
                                        ("planned", "planned", "planned"))
        return {
            "email": user_a.email, "cap": cap.name, "bare": bare.name, "app": app_a.name,
            "missing_code": controls[2].control_code, "missing_title": controls[2].title,
            "other": [cap_b.name, app_b.name],
        }


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _open(page, base, path):
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response is not None and response.status == 200, path
    page.wait_for_selector("main h1")
    page.wait_for_timeout(800)


def _figure(page, name):
    cell = page.locator("tr", has_text=name).locator('[data-testid="compliance-figure"]')
    assert cell.count() >= 1, "no compliance figure beside %s on %s" % (name, page.url)
    return " ".join(cell.first.inner_text().split())


def _shot(page, name, width, scheme):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, scheme, width)), full_page=True)


def test_one_figure_a_finding_and_no_raw_data(browser, live_server, world):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        pages = {"tracking": "/strategic/compliance-tracking", "health": "/strategic/capability-health",
                 "dashboard": "/dashboard/compliance"}
        figures = {}
        for key, path in pages.items():
            _open(page, live_server, path)
            figures[key] = _figure(page, world["cap"])
            body = page.locator("main").inner_text()
            assert not re.search(r"\{'|estimated_cost|'priority'", body), key
            for other in world["other"]:
                assert other not in body, key
            _shot(page, "item13-%s" % key, 1440, "light")
        assert set(figures.values()) == {"50%"}, figures

        _open(page, live_server, pages["tracking"])
        assert _figure(page, world["bare"]) == "Not assessed"
        row = " ".join(page.locator("tr", has_text=world["bare"]).filter(
            has=page.locator('[data-testid="compliance-figure"]')).first.inner_text().split())
        assert "Map the controls" in row
        text = page.locator('[data-testid="compliance-recommendations"]').inner_text()
        assert "Put the missing control in place for %s" % world["cap"] in text

        # The finding opens its control.
        _open(page, live_server, pages["dashboard"])
        finding = page.locator('[data-testid="compliance-finding-row"]', has_text=world["missing_code"])
        assert finding.count() == 1
        finding.locator("a").click()
        page.wait_for_selector('[data-testid="control-panel"]')
        panel = " ".join(page.locator('[data-testid="control-panel"]').inner_text().split())
        assert world["missing_title"] in panel and world["app"] in panel and "planned" in panel
        for other in world["other"]:
            assert other not in panel

        _open(page, live_server, "/frameworks/")
        _shot(page, "item13-frameworks", 1440, "light")

        # The old address lands on the dashboard, not on data.
        page.goto(live_server + "/enterprise/compliance", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.url.endswith("/dashboard/compliance")
        assert page.locator("main h1").inner_text().strip() == "Regulatory Compliance"
    finally:
        ctx.close()


def test_compliance_pages_hold_at_phone_width(browser, live_server, world):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        for key, path in (("tracking", "/strategic/compliance-tracking"), ("health", "/strategic/capability-health"),
                          ("dashboard", "/dashboard/compliance"), ("frameworks", "/frameworks/")):
            _open(page, live_server, path)
            assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1, key
            _shot(page, "item13-%s" % key, 390, "dark")
    finally:
        ctx.close()
