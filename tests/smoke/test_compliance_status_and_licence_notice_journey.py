"""A capability with controls not in place is never called Compliant, and the licence notice fits its reader.

The goal of an enterprise architect: read the compliance standing of a capability that scores well but
still has a control not in place, and see the same honest status on Compliance Tracking and Capability
Health; then, on Regulatory Compliance, learn how to reach License Compliance. Seeded with one capability
at 90% with a control still in progress, one with every control in place, an administrator and an ordinary
architect in the same organisation, and a second organisation whose records must never show.

* a capability at 90% with a control not in place reads "Partly compliant" on both pages, with its count;
* a capability with every control in place reads "Compliant" on both pages;
* the administrator is told to choose Procurement on a linked role page; following it and saving opens License Compliance; the architect is told to ask
  an administrator; the administrator is never told to ask an administrator;
* an administrator invited into another organisation, whose home role is an architect or an administrator,
  is not sent to a role page that would refuse them: the notice keeps the ask-an-administrator wording;
* the notice names every role that may open the page and ends with a full stop;
* the administrator who opens License Compliance after the change sees the licence recorded in their organisation, with its figure;
* an administrator who switches organisation opens Team, and "Set enterprise role" opens the member's role page in that organisation;
  a person invited as an architect is not offered the item;
* the notice sits inside the viewport at 390 wide.
"""

import os
import uuid
from datetime import datetime

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .contract_records import add_contract_and_licence, remove_records

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("SMOKE_WALK_DIR")


def _org(db, label, suffix):
    from app.models.organization import Organization
    from app.services.billing_plans import set_contract_plan

    org = Organization(name="%s %s" % (label, suffix), slug="%s-%s" % (label.lower(), suffix))
    db.session.add(org)
    db.session.flush()
    set_contract_plan(org, "enterprise", None)
    db.session.commit()
    return org


def _user(db, org, label, suffix, admin):
    from app.models.user import Role, User

    user = User(
        email="%s.%s@example.com" % (label.lower(), suffix), first_name=label, last_name="Reader",
        organization_id=org.id, enterprise_role="enterprise_architect", confirmed=True,
    )
    user.role = Role.query.filter_by(name="Administrator" if admin else "Architect").one()
    user.is_org_admin = admin
    user.password = PASSWORD
    user.onboarding_completed_at = datetime.utcnow()
    db.session.add(user)
    db.session.commit()
    return user


def _estate(db, org, user, framework, controls, label, suffix):
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capability import BusinessCapability
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    ApplicabilityService.adopt_framework(org.id, framework.id, adopted_by_id=user.id)
    names = {"partly": "%s Settlement %s" % (label, suffix), "full": "%s Archive %s" % (label, suffix)}
    caps = {key: BusinessCapability(name=name, organization_id=org.id) for key, name in names.items()}
    apps = {key: ApplicationComponent(name="%s %s app" % (label, key), organization_id=org.id) for key in names}
    db.session.add_all(list(caps.values()) + list(apps.values()))
    db.session.flush()
    for key in names:
        db.session.add(ApplicationCapabilityMapping(
            organization_id=org.id, application_component_id=apps[key].id, business_capability_id=caps[key].id,
            support_level="full", coverage_percentage=80, is_active=True,
        ))
    for n, control in enumerate(controls):
        # Settlement: four verified and one in progress is 90%, with one control not in place.
        status = "in_progress" if n == len(controls) - 1 else "verified"
        link = ApplicabilityService.link_requirement(org.id, apps["partly"].id, control.id, status)
        if status == "verified":
            link.verified_date = datetime(2026, 9, 14, 10, 0)
        link = ApplicabilityService.link_requirement(org.id, apps["full"].id, control.id, "verified")
        link.verified_date = datetime(2026, 9, 14, 10, 0)
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
        framework = RegulatoryFramework(code="S-%s" % suffix, name="Status standard %s" % suffix, status="active")
        db.session.add(framework)
        db.session.flush()
        controls = [
            ComplianceControl(framework_id=framework.id, control_code="S.%d-%s" % (n, suffix), title="Control %d %s" % (n, suffix))
            for n in range(5)
        ]
        db.session.add_all(controls)
        db.session.commit()
        org_a = _org(db, "Alpha", suffix)
        org_b = _org(db, "Bravo", suffix)
        admin = _user(db, org_a, "Admin", suffix, True)
        member = _user(db, org_a, "Member", suffix, False)
        other_admin = _user(db, org_b, "Other", suffix, True)
        names = _estate(db, org_a, admin, framework, controls, "Alpha", suffix)
        other = _estate(db, org_b, other_admin, framework, controls, "Bravo", suffix)
        return {"admin": admin.email, "member": member.email, "names": names, "other": list(other.values()), "org_id": org_a.id}


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


def _row(page, name):
    return " ".join(page.locator("tr", has_text=name).first.inner_text().split())


def _shot(page, name, width, scheme):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%s-%d.png" % (name, scheme, width)), full_page=True)


def _context(browser, width, scheme):
    ctx = browser.new_context(viewport={"width": width, "height": 900 if width > 500 else 844}, color_scheme=scheme)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    return ctx


def test_capability_with_controls_not_in_place_is_not_compliant(browser, live_server, world):
    ctx = _context(browser, 1440, "light")
    page = ctx.new_page()
    try:
        _login(page, live_server, world["member"])
        for key, path in (("tracking", "/strategic/compliance-tracking"), ("health", "/strategic/capability-health")):
            _open(page, live_server, path)
            partly = _row(page, world["names"]["partly"])
            full = _row(page, world["names"]["full"])
            assert "Partly compliant" in partly and "Compliant" not in partly.replace("Partly compliant", ""), (key, partly)
            assert "90" in partly, (key, partly)
            if key == "tracking":
                assert "1 control not in place" in partly, (key, partly)
            else:
                assert partly.split("Partly compliant")[-1].split()[-1] == "1", (key, partly)
            assert "Partly" not in full and "Compliant" in full, (key, full)
            body = page.locator("main").inner_text()
            for other in world["other"]:
                assert other not in body, key
            _shot(page, "status-%s" % key, 1440, "light")
    finally:
        ctx.close()


def test_licence_notice_names_who_can_open_it_for_this_reader(browser, live_server, world):
    ctx = _context(browser, 1440, "light")
    page = ctx.new_page()
    try:
        _login(page, live_server, world["member"])
        _open(page, live_server, "/dashboard/compliance")
        notice = page.locator("#license-compliance-reason")
        assert notice.count() == 1
        text = " ".join(notice.inner_text().split())
        assert "Ask an administrator in Team settings" in text and "role page" not in text, text
        assert text.startswith("Only the Procurement and Portfolio Manager roles can open License Compliance."), text
        assert text.endswith("."), text
        assert notice.locator("a").count() == 0
        _shot(page, "licence-notice-member", 1440, "light")
    finally:
        ctx.close()


def test_administrator_follows_the_notice_and_opens_licence_compliance(browser, live_server, world):
    records = add_contract_and_licence(world["org_id"], "Alpha" + uuid.uuid4().hex[:6])
    try:
        _follow_the_notice(browser, live_server, world)
    finally:
        remove_records(records)


def _follow_the_notice(browser, live_server, world):
    ctx = _context(browser, 390, "dark")
    page = ctx.new_page()
    try:
        _login(page, live_server, world["admin"])
        _open(page, live_server, "/dashboard/compliance")
        notice = page.locator("#license-compliance-reason")
        assert notice.count() == 1
        text = " ".join(notice.inner_text().split())
        assert "Choose Procurement on your role page" in text and "Ask an administrator" not in text, text
        assert text.startswith("Only the Procurement and Portfolio Manager roles can open License Compliance."), text
        assert text.endswith("your role page."), text
        box = notice.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 391, box
        assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1
        _shot(page, "licence-notice-admin", 390, "dark")

        notice.locator("a").click()
        page.wait_for_selector("input[name=enterprise_role]", state="attached")
        assert "/role" in page.url
        page.locator("label", has_text="Procurement").first.click()
        page.get_by_role("button", name="Save Changes").click()
        page.wait_for_url(lambda url: "/role" not in url, timeout=PAGE_TIMEOUT)
        saved = page.content()
        assert "Role updated: Enterprise Architect → Procurement" in saved, "save confirmation not in display names"
        assert "enterprise_architect" not in saved.split("Role updated:")[1][:80]

        _open(page, live_server, "/dashboard/compliance")
        assert page.locator("#license-compliance-reason").count() == 0
        page.get_by_role("link", name="License Compliance").first.click()
        page.wait_for_selector("main h1")
        assert page.url.endswith("/procurement/compliance"), page.url
        assert "license" in page.locator("main h1").first.inner_text().lower()
        shown = " ".join(page.locator("main").inner_text().split())
        assert "31 / 40 licenses" in shown, shown
        assert "78%" in shown, shown
        assert "Overall Utilization —" not in shown, shown
        _shot(page, "licence-compliance-opened", 390, "dark")
    finally:
        ctx.close()


def test_team_page_links_each_member_to_their_role_page(browser, live_server, world):
    ctx = _context(browser, 1440, "light")
    page = ctx.new_page()
    try:
        _login(page, live_server, world["admin"])
        _open(page, live_server, "/admin/team")
        row = page.locator("[data-testid=member-row]", has_text=world["member"]).first
        row.get_by_role("button", name="Actions for").click()
        page.locator("[data-testid=member-enterprise-role]:visible").first.click()
        page.wait_for_selector("input[name=enterprise_role]", state="attached")
        assert world["member"] in page.locator("main").inner_text()
        assert page.locator("input[name=enterprise_role]").count() > 5
        _shot(page, "team-role-page", 1440, "light")
    finally:
        ctx.close()


def _switch(page, base, org_id):
    _open(page, base, "/account/manage")
    token = page.evaluate("document.querySelector('meta[name=csrf-token]')?.content || document.querySelector('input[name=csrf_token]')?.value")
    switched = page.request.post(base + "/account/switch-organization", form={"organization_id": str(org_id), "csrf_token": token})
    assert switched.status == 200, switched.status


def _invited(app, world, administrator_at_home):
    from app import db
    from app.models.org_role import OrgRole
    from app.models.user import User

    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        alpha = User.query.filter_by(email=world["admin"]).one().organization_id
        home = _org(db, "Home", suffix)
        person = _user(db, home, "Invited", suffix, administrator_at_home)
        OrgRole.set_role(alpha, person.id, "org_admin", granted_by_id=person.id)
        db.session.commit()
        return person.email, alpha


@pytest.mark.parametrize("administrator_at_home", [False, True], ids=["architect-at-home", "administrator-at-home"])
def test_invited_administrator_is_not_sent_to_a_page_that_refuses_them(browser, live_server, app, world, administrator_at_home):
    email, alpha = _invited(app, world, administrator_at_home)
    ctx = _context(browser, 1440, "light")
    page = ctx.new_page()
    try:
        _login(page, live_server, email)
        _switch(page, live_server, alpha)
        _open(page, live_server, "/dashboard/compliance")
        notice = page.locator("#license-compliance-reason")
        assert notice.count() == 1
        text = " ".join(notice.inner_text().split())
        assert "Ask an administrator in Team settings" in text and "role page" not in text, text
        assert notice.locator("a").count() == 0
        _shot(page, "licence-notice-invited-%s" % ("admin" if administrator_at_home else "architect"), 1440, "light")
    finally:
        ctx.close()


@pytest.mark.parametrize("administrator_at_home", [False, True], ids=["architect-at-home", "administrator-at-home"])
def test_team_offers_the_role_page_only_where_it_opens_after_switching_organisation(browser, live_server, app, world, administrator_at_home):
    email, alpha = _invited(app, world, administrator_at_home)
    ctx = _context(browser, 1440, "light")
    page = ctx.new_page()
    try:
        _login(page, live_server, email)
        _switch(page, live_server, alpha)
        _open(page, live_server, "/admin/team")
        body = page.locator("main").inner_text()
        assert world["member"] in body, "Team should list the organisation the person is working in"
        assert email not in "".join(page.locator("[data-testid=member-row]").all_inner_texts()), "Team listed the home organisation"
        row = page.locator("[data-testid=member-row]", has_text=world["member"]).first
        row.get_by_role("button", name="Actions for").click()
        item = page.locator("[data-testid=member-enterprise-role]:visible")
        if not administrator_at_home:
            assert item.count() == 0
            _shot(page, "team-switched-architect", 1440, "light")
            return
        item.first.click()
        page.wait_for_selector("input[name=enterprise_role]", state="attached")
        assert "/role" in page.url
        assert world["member"] in page.locator("main").inner_text()
        assert page.locator("input[name=enterprise_role]").count() > 5
        _shot(page, "team-switched-role-page", 1440, "light")
    finally:
        ctx.close()
