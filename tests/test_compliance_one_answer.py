"""Compliance gives one answer on every page, lists findings a person can act on, and prints no raw data.

Two real organisations: the controls mapped in one never show in the other.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

RAW = re.compile(r"\{'|\[\{'|estimated_cost|'priority'|&#39;priority&#39;")


def _visible(html):
    """The text a person reads: no scripts, no markup."""
    html = re.sub(r"<(script|style|template).*?</\1>", "", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def _world(db_session, make_org, label):
    """An organisation with ISO 27001 adopted, three controls, two applications, two capabilities
    (one with mapped controls, one without) and a user."""
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capability import BusinessCapability
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.modules.compliance.services.applicability_service import ApplicabilityService
    from tests.test_work_package_writer import _user

    org = make_org(label)
    user = _user(db_session, org, label, role="enterprise_architect")
    suffix = uuid.uuid4().hex[:6]
    framework = RegulatoryFramework(code=f"T-{label}-{suffix}", name=f"Test standard {label}", status="active")
    db_session.add(framework)
    db_session.flush()
    controls = []
    for n, title in enumerate(("Access Control", "Cryptography", "Deletion")):
        control = ComplianceControl(framework_id=framework.id, control_code=f"X.{n}-{suffix}", title=f"{title} {suffix}")
        db_session.add(control)
        controls.append(control)
    db_session.flush()
    ApplicabilityService.adopt_framework(org.id, framework.id, adopted_by_id=user.id)

    cap_mapped = BusinessCapability(name=f"Mapped {label} {suffix}", organization_id=org.id)
    cap_bare = BusinessCapability(name=f"Bare {label} {suffix}", organization_id=org.id)
    app_a = ApplicationComponent(name=f"Ledger {label} {suffix}", organization_id=org.id)
    db_session.add_all([cap_mapped, cap_bare, app_a])
    db_session.flush()
    db_session.add(
        ApplicationCapabilityMapping(
            organization_id=org.id, application_component_id=app_a.id, business_capability_id=cap_mapped.id,
            support_level="full", coverage_percentage=80, is_active=True,
        )
    )
    # implemented, in progress, planned -> (1 + 0.5 + 0) / 3 = 50%; one control missing
    for control, status in zip(controls, ("implemented", "in_progress", "planned")):
        ApplicabilityService.link_requirement(org.id, app_a.id, control.id, status)
    db_session.flush()
    return org, user, cap_mapped, cap_bare, controls, app_a


def _figure_in(html, name):
    row = re.search(re.escape(name) + r".*?data-testid=\"compliance-figure\"[^>]*>\s*([^<]+?)\s*<", html, re.S)
    assert row, "no compliance figure beside %s" % name
    return row.group(1)


def test_one_capability_shows_one_figure_on_every_page(db_session, make_org, client, login_as):
    org, user, cap, bare, controls, _app = _world(db_session, make_org, "one")
    login_as(client, user)

    tracking = client.get("/strategic/compliance-tracking").get_data(as_text=True)
    login_as(client, user)
    health = client.get("/strategic/capability-health").get_data(as_text=True)
    login_as(client, user)
    dashboard = client.get("/dashboard/compliance").get_data(as_text=True)
    login_as(client, user)
    frameworks_page = client.get("/frameworks/")
    assert frameworks_page.status_code == 200

    assert _figure_in(tracking, cap.name) == "50%"
    assert _figure_in(health, cap.name) == "50%"
    assert _figure_in(dashboard, cap.name) == "50%"
    # No mapped control: not assessed, never 100% and never 0%.
    for html in (tracking, dashboard):
        assert _figure_in(html, bare.name) == "Not assessed"
    assert bare.name not in health.split("Capability Compliance", 1)[1].split("Top At-Risk", 1)[0]


def test_a_missing_control_is_a_finding_that_opens_its_control(db_session, make_org, client, login_as):
    org, user, cap, bare, controls, app_a = _world(db_session, make_org, "find")
    login_as(client, user)
    html = client.get("/dashboard/compliance").get_data(as_text=True)

    missing = controls[2]
    link = re.search(r'href="([^"]*control=%d)"[^>]*>\s*%s' % (missing.id, re.escape(missing.control_code)), html)
    assert link, "the missing control is not listed as a finding"
    findings = html.split("Missing controls", 1)[-1].split("Capability compliance", 1)[0]
    assert controls[0].control_code not in findings, "an implemented control is listed as a finding"

    login_as(client, user)
    landed = client.get(link.group(1).replace("&amp;", "&"))
    page = landed.get_data(as_text=True)
    assert landed.status_code == 200
    assert 'data-testid="control-panel"' in page
    assert missing.title in page and app_a.name in page and "planned" in page


def test_no_compliance_page_shows_raw_data(db_session, make_org, client, login_as):
    org, user, *_ = _world(db_session, make_org, "raw")
    for path in ("/strategic/compliance-tracking", "/strategic/capability-health", "/dashboard/compliance", "/frameworks/"):
        login_as(client, user)
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert not RAW.search(_visible(resp.get_data(as_text=True))), path

    login_as(client, user)
    tracking = _visible(client.get("/strategic/compliance-tracking").get_data(as_text=True))
    assert "Put the missing control in place for Mapped raw" in tracking
    assert "450000" not in tracking and "Unknown 0%" not in tracking and "Never" not in tracking


def test_a_browser_asking_for_the_enterprise_compliance_address_is_sent_to_the_dashboard(
    db_session, make_org, client, login_as
):
    _org, user, *_ = _world(db_session, make_org, "addr")
    login_as(client, user)
    resp = client.get("/enterprise/compliance", headers={"Accept": "text/html,application/xhtml+xml"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/dashboard/compliance")
    login_as(client, user)
    assert client.get("/enterprise/compliance").get_json()["success"] is True


def test_another_organisations_mappings_never_show(db_session, make_org, client, login_as):
    org_a, user_a, cap_a, _bare_a, controls_a, app_a = _world(db_session, make_org, "isoa")
    org_b, user_b, cap_b, _bare_b, controls_b, app_b = _world(db_session, make_org, "isob")

    for user, own, other_cap, other_app, other_control in (
        (user_a, cap_a, cap_b, app_b, controls_b[2]),
        (user_b, cap_b, cap_a, app_a, controls_a[2]),
    ):
        for path in ("/dashboard/compliance", "/strategic/compliance-tracking", "/strategic/capability-health"):
            login_as(client, user)
            html = client.get(path).get_data(as_text=True)
            assert own.name in html, path
            assert other_cap.name not in html, path
            assert other_app.name not in html, path
        login_as(client, user)
        panel = client.get("/dashboard/compliance?control=%d" % other_control.id).get_data(as_text=True)
        assert other_app.name not in panel


def test_the_demonstration_seed_adopts_iso_27001_and_leaves_findings(db_session, make_org, client, login_as):
    from app.commands.seed_demo_company import _seed_compliance
    from app.models.application_portfolio import ApplicationComponent
    from app.models.compliance_models import RegulatoryFramework
    from app.models.regulatory_framework import FrameworkAdoption
    from tests.test_work_package_writer import _user

    org = make_org("demo")
    user = _user(db_session, org, "demo", role="enterprise_architect")
    db_session.add(ApplicationComponent(name="Calibration Ledger", organization_id=org.id))
    db_session.flush()

    assert _seed_compliance(org.id) > 0
    assert _seed_compliance(org.id) == 0, "running the seed again must add nothing"
    framework = RegulatoryFramework.query.filter_by(code="ISO - 27001").one()
    assert FrameworkAdoption.query.filter_by(organization_id=org.id, framework_id=framework.id).count() == 1

    login_as(client, user)
    html = client.get("/dashboard/compliance").get_data(as_text=True)
    assert 'data-testid="compliance-finding-row"' in html, "the demonstration shows no finding"
