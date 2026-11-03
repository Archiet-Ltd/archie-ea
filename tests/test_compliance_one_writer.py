"""Compliance has one writer of application-to-control links, one scorer and no invented figure.

Two real organisations: what one maps, scores or reads never reaches the other.
"""

from __future__ import annotations

import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

ROOT = Path(__file__).resolve().parents[1]


def _world(db_session, make_org, label, statuses=("implemented", "in_progress", "planned")):
    """An organisation with a standard adopted, three controls, one application mapped to a capability."""
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capability import BusinessCapability
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.modules.compliance.services.applicability_service import ApplicabilityService
    from tests.test_work_package_writer import _user

    org = make_org(label)
    user = _user(db_session, org, label, role="enterprise_architect")
    suffix = uuid.uuid4().hex[:6]
    framework = RegulatoryFramework(code=f"W-{label}-{suffix}", name=f"Writer standard {label}", status="active")
    db_session.add(framework)
    db_session.flush()
    controls = []
    for n in range(3):
        control = ComplianceControl(framework_id=framework.id, control_code=f"W.{n}-{suffix}", title=f"Control {n} {suffix}")
        db_session.add(control)
        controls.append(control)
    db_session.flush()
    ApplicabilityService.adopt_framework(org.id, framework.id, adopted_by_id=user.id)
    cap = BusinessCapability(name=f"Capability {label} {suffix}", organization_id=org.id)
    app_a = ApplicationComponent(name=f"Ledger {label} {suffix}", organization_id=org.id)
    db_session.add_all([cap, app_a])
    db_session.flush()
    db_session.add(ApplicationCapabilityMapping(
        organization_id=org.id, application_component_id=app_a.id, business_capability_id=cap.id,
        support_level="full", coverage_percentage=80, is_active=True,
    ))
    for control, status in zip(controls, statuses):
        link = ApplicabilityService.link_requirement(org.id, app_a.id, control.id)
        link.implementation_status = status
    db_session.flush()
    return org, user, cap, controls, app_a


def test_there_is_one_writer_of_application_links():
    found = subprocess.run(
        ["grep", "-rn", "def link_requirement", "app", "--include=*.py"], cwd=ROOT, capture_output=True, text=True
    ).stdout.splitlines()
    in_service = [line for line in found if "applicability_service.py" in line]
    assert len(in_service) == 1, found


def test_the_writer_refuses_another_organisations_application_and_an_unadopted_control(db_session, make_org):
    from app.models.application_compliance import ApplicationComplianceControl
    from app.modules.compliance.services.applicability_service import (
        ApplicabilityService, FrameworkNotAdopted, NotFoundInOrganisation,
    )

    org_a, _ua, _ca, controls_a, app_a = _world(db_session, make_org, "wra")
    org_b, _ub, _cb, controls_b, app_b = _world(db_session, make_org, "wrb")

    with pytest.raises(NotFoundInOrganisation):
        ApplicabilityService.link_requirement(org_a.id, app_b.id, controls_a[0].id)
    with pytest.raises(FrameworkNotAdopted):
        ApplicabilityService.link_requirement(org_a.id, app_a.id, controls_b[0].id)
    with pytest.raises(ValueError):
        ApplicabilityService.link_requirement(None, app_a.id, controls_a[0].id)
    assert ApplicationComplianceControl.query.filter_by(
        organization_id=org_a.id, application_id=app_b.id
    ).count() == 0


def test_enterprise_compliance_without_policies_has_no_invented_score(db_session, make_org, client, login_as):
    _org, user, *_ = _world(db_session, make_org, "nopol")
    login_as(client, user)
    body = client.get("/enterprise/compliance", headers={"Accept": "application/json"}).get_json()
    assert body["success"] is True
    assert body["data"]["total_policies"] == 0
    assert body["data"]["compliance_score"] is None


def test_enterprise_compliance_with_policies_reads_the_one_scorer(db_session, make_org, client, login_as):
    from app.models.compliance_models import CompliancePolicy

    org, user, *_ = _world(db_session, make_org, "pol")
    db_session.add(CompliancePolicy(
        organization_id=org.id, name="Retention %s" % uuid.uuid4().hex[:6], policy_type="data"))
    db_session.flush()
    login_as(client, user)
    body = client.get("/enterprise/compliance", headers={"Accept": "application/json"}).get_json()
    # (1 + 0.5 + 0) / 3 controls on the one mapped capability
    assert body["data"]["compliance_score"] == 50
    source = (ROOT / "app/modules/capabilities/routes/enterprise_crud_routes.py").read_text()
    assert "100 - (critical" not in source


def test_the_second_tracking_service_is_gone():
    assert not (ROOT / "app/services/compliance_tracking_service.py").exists()


def test_the_dashboard_reads_only_its_own_organisations_capability_mappings(db_session, make_org, client, login_as):
    from sqlalchemy import event

    from app import db
    from app.models.application_capability import ApplicationCapabilityMapping

    org_a, user_a, cap_a, _controls_a, app_a = _world(db_session, make_org, "fnca")
    org_b, user_b, cap_b, _controls_b, app_b = _world(db_session, make_org, "fncb")
    table = ApplicationCapabilityMapping.__tablename__

    seen = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if re.search(r"FROM\s+%s\b" % table, statement):
            seen.append((statement, parameters))

    engine = db.session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        login_as(client, user_a)
        html = client.get("/dashboard/compliance").get_data(as_text=True)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert cap_a.name in html and cap_b.name not in html and app_b.name not in html
    assert seen, "the dashboard did not read the capability mappings"
    for statement, parameters in seen:
        assert "organization_id" in statement, statement
        assert org_a.id in (parameters.values() if isinstance(parameters, dict) else parameters)


def test_mapping_a_control_writes_through_the_one_writer_with_real_fields(db_session, make_org, client, login_as):
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_portfolio import ApplicationComponent
    from app.models.compliance_models import ComplianceControl

    org_a, user_a, _ca, controls_a, _app_a = _world(db_session, make_org, "mapa")
    org_b, _ub, _cb, _controls_b, app_b = _world(db_session, make_org, "mapb")
    fresh = ApplicationComponent(name="Fresh %s" % uuid.uuid4().hex[:6], organization_id=org_a.id)
    db_session.add(fresh)
    db_session.flush()

    login_as(client, user_a)
    resp = client.post(
        "/dashboard/api/applications/%d/compliance/map" % fresh.id,
        json={"control_id": controls_a[0].id, "implementation_status": "in_progress",
              "notes": "Reviewed with security", "evidence_url": "https://example.com/evidence"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    row = ApplicationComplianceControl.query.filter_by(
        organization_id=org_a.id, application_id=fresh.id, control_id=controls_a[0].id).one()
    assert row.implementation_status == "in_progress"
    assert row.notes == "Reviewed with security"
    assert row.adoption_id is not None

    login_as(client, user_a)
    again = client.post("/dashboard/api/applications/%d/compliance/map" % fresh.id, json={"control_id": controls_a[0].id})
    assert again.status_code == 409

    login_as(client, user_a)
    foreign = client.post("/dashboard/api/applications/%d/compliance/map" % app_b.id, json={"control_id": controls_a[0].id})
    assert foreign.status_code == 404
    assert ApplicationComplianceControl.query.filter_by(organization_id=org_b.id, application_id=app_b.id,
                                                       control_id=controls_a[0].id).count() == 0


def test_last_review_reads_the_verified_date_and_one_number_format(db_session, make_org, client, login_as):
    org, user, cap, controls, app_a = _world(
        db_session, make_org, "rev", statuses=("verified", "in_progress", "planned"))
    from app.models.application_compliance import ApplicationComplianceControl

    link = ApplicationComplianceControl.query.filter_by(
        organization_id=org.id, application_id=app_a.id, control_id=controls[0].id).one()
    link.verified_date = datetime(2026, 9, 14, 10, 0)
    db_session.flush()

    login_as(client, user)
    html = client.get("/strategic/compliance-tracking").get_data(as_text=True)
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style|template).*?</\1>", "", html, flags=re.S))
    assert "14 Sep 2026" in text
    # (1 + 0.5 + 0) / 3 = 50 on the capability row and, as the only capability, on the average
    assert "50.0%" not in text
    assert len(re.findall(r"\b50%", text)) >= 2


def test_the_demonstration_seed_dates_the_controls_it_marks_verified(db_session, make_org):
    from app.commands.seed_demo_company import _seed_compliance
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_portfolio import ApplicationComponent

    org = make_org("sdv")
    db_session.add(ApplicationComponent(name="Dispatch Planner", organization_id=org.id))
    db_session.flush()
    assert _seed_compliance(org.id) > 0
    verified = ApplicationComplianceControl.query.filter_by(
        organization_id=org.id, implementation_status="verified").all()
    assert verified
    assert all(v.verified_date is not None for v in verified)


def test_harmonised_evidence_is_borrowed_only_from_the_same_organisation(db_session, make_org):
    """Called with no request and no tenant filter: only the organisation named in the call separates them."""
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    suffix = uuid.uuid4().hex[:6]
    first = RegulatoryFramework(code=f"HA-{suffix}", name=f"Harmony one {suffix}", status="active")
    second = RegulatoryFramework(code=f"HB-{suffix}", name=f"Harmony two {suffix}", status="active")
    db_session.add_all([first, second])
    db_session.flush()
    c1 = ComplianceControl(framework_id=first.id, control_code=f"H1-{suffix}", title="One")
    c2 = ComplianceControl(framework_id=second.id, control_code=f"H2-{suffix}", title="Two")
    db_session.add_all([c1, c2])
    db_session.flush()
    c1.harmonized_control_id, c1.harmonization_status = c2.id, "confirmed"

    org_a, org_b = make_org("hma"), make_org("hmb")
    adoptions = {}
    # The other organisation's rows are written first, so an unscoped lookup finds them first.
    for org in (org_b, org_a):
        adoptions[org.id] = (
            ApplicabilityService.adopt_framework(org.id, first.id, adopted_by_id=None),
            ApplicabilityService.adopt_framework(org.id, second.id, adopted_by_id=None),
        )
    db_session.flush()

    def held(org, control):
        return ApplicationComplianceControl.query.filter_by(
            organization_id=org.id, control_id=control.id).one()

    held(org_b, c2).evidence_url = "https://evidence.example/b-two"
    db_session.flush()

    def source_of(org, adoption, control):
        rows = ApplicabilityService.evidence_status(org.id, adoption.id)
        return next(r for r in rows if r["control_id"] == control.id)

    # Each organisation borrows only from its own partner row. One has evidence on the partner and borrows it;
    # the other has none and borrows nothing, whichever organisation's row a lookup would reach first.
    borrowed = source_of(org_b, adoptions[org_b.id][0], c1)
    assert borrowed["evidence_source"]["control_id"] == c2.id and borrowed["evidence_url"].endswith("/b-two")
    assert source_of(org_a, adoptions[org_a.id][0], c1)["evidence_source"] is None

    # The same the other way round: the partner points back at the control.
    held(org_b, c2).evidence_url = None
    held(org_b, c1).evidence_url = "https://evidence.example/b-one"
    db_session.flush()
    borrowed = source_of(org_b, adoptions[org_b.id][1], c2)
    assert borrowed["evidence_source"]["control_id"] == c1.id and borrowed["evidence_url"].endswith("/b-one")
    assert source_of(org_a, adoptions[org_a.id][1], c2)["evidence_source"] is None
