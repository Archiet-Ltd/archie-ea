"""Mapping a control to an application stores status, notes and the evidence link, and reads them back.

Covers the two JSON endpoints a person's tooling uses to map a control and edit that mapping.
A second organisation can neither map onto nor edit the first organisation's records.
"""

from __future__ import annotations

import uuid

import pytest

from app import db

from tests.test_work_package_round3 import _org_with_user

pytestmark = pytest.mark.usefixtures("db_session")


def _component(db_session, org, name="Billing"):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _controls(db_session, count=1):
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    framework = RegulatoryFramework(code=f"FW{uuid.uuid4().hex[:6]}", name="Test Framework", category="security")
    db_session.add(framework)
    db_session.flush()
    controls = [
        ComplianceControl(framework_id=framework.id, control_code=f"AC-{n + 1}", title=f"Control {n + 1}")
        for n in range(count)
    ]
    db_session.add_all(controls)
    db_session.flush()
    return controls


def _control(db_session):
    return _controls(db_session)[0]


def _adopt(db_session, org, user, control):
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    return ApplicabilityService.adopt_framework(org.id, control.framework_id, user.id)


def _rows(db_session, control):
    """Every stored link of the control, whichever organisation holds it, as plain tuples."""
    return sorted(
        tuple(r)
        for r in db_session.execute(
            db.text(
                "SELECT id, organization_id, application_id, implementation_status, notes, evidence_url "
                "FROM application_compliance_controls WHERE control_id = :c"
            ),
            {"c": control.id},
        )
    )


def _map_url(component):
    return f"/dashboard/api/applications/{component.id}/compliance/map"


def test_mapping_is_created_edited_and_read_back(db_session, make_org, client, login_as):
    org, user = _org_with_user(db_session, make_org, "cm-a", role="admin")
    component, control = _component(db_session, org), _control(db_session)
    _adopt(db_session, org, user, control)
    login_as(client, user)

    created = client.post(
        _map_url(component),
        json={
            "control_id": control.id,
            "implementation_status": "in_progress",
            "notes": "Rolling out MFA",
            "evidence_url": "https://evidence.example/mfa",
        },
    )
    assert created.status_code == 200, created.get_data(as_text=True)
    mapping = created.get_json()["mapping"]
    assert (mapping["implementation_status"], mapping["notes"], mapping["evidence_url"]) == (
        "in_progress", "Rolling out MFA", "https://evidence.example/mfa",
    )

    edited = client.put(
        f"/dashboard/api/compliance/mappings/{mapping['id']}",
        json={"implementation_status": "implemented", "notes": "MFA live", "evidence_url": "https://evidence.example/mfa-live"},
    )
    assert edited.status_code == 200, edited.get_data(as_text=True)

    listed = client.get(f"/dashboard/api/applications/{component.id}/compliance").get_json()
    row = listed["mappings"][0]
    assert (row["implementation_status"], row["notes"], row["evidence_url"]) == (
        "implemented", "MFA live", "https://evidence.example/mfa-live",
    )
    assert listed["summary"]["implemented"] == 1
    assert listed["summary"]["total_controls"] == 1


def test_unknown_status_is_refused(db_session, make_org, client, login_as):
    org, user = _org_with_user(db_session, make_org, "cm-s", role="admin")
    component, control = _component(db_session, org), _control(db_session)
    _adopt(db_session, org, user, control)
    login_as(client, user)

    resp = client.post(_map_url(component), json={"control_id": control.id, "implementation_status": "bogus"})

    assert resp.status_code == 400


def test_another_organisation_gets_404_and_nothing_changes(db_session, make_org, client, login_as):
    org_a, user_a = _org_with_user(db_session, make_org, "cm-b1", role="admin")
    org_b, user_b = _org_with_user(db_session, make_org, "cm-b2", role="admin")
    component, control = _component(db_session, org_a), _control(db_session)
    _adopt(db_session, org_a, user_a, control)
    _adopt(db_session, org_b, user_b, control)
    login_as(client, user_a)
    mapping = client.post(_map_url(component), json={"control_id": control.id, "notes": "mine"}).get_json()["mapping"]
    before = _rows(db_session, control)

    login_as(client, user_b)
    url = f"/dashboard/api/compliance/mappings/{mapping['id']}"
    answers = [
        client.post(_map_url(component), json={"control_id": control.id, "notes": "theirs"}),
        client.put(url, json={"notes": "stolen", "implementation_status": "waived"}),
        client.get(f"/dashboard/api/applications/{component.id}/compliance"),
        client.delete(url),
    ]

    assert [a.status_code for a in answers] == [404, 404, 404, 404]
    assert _rows(db_session, control) == before


def test_mapping_carries_the_adoption_it_belongs_to(db_session, make_org, client, login_as):
    from app.models.application_compliance import ApplicationComplianceControl

    org, user = _org_with_user(db_session, make_org, "cm-ad", role="admin")
    component, control = _component(db_session, org), _control(db_session)
    adoption = _adopt(db_session, org, user, control)
    login_as(client, user)

    mapping = client.post(_map_url(component), json={"control_id": control.id}).get_json()["mapping"]

    row = db_session.get(ApplicationComplianceControl, mapping["id"])
    assert (row.adoption_id, row.implementation_status) == (adoption.id, "planned")


def test_framework_not_adopted_answers_409_and_writes_nothing(db_session, make_org, client, login_as):
    org, user = _org_with_user(db_session, make_org, "cm-na", role="admin")
    component, control = _component(db_session, org), _control(db_session)
    login_as(client, user)

    resp = client.post(_map_url(component), json={"control_id": control.id})

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "Adopt this framework first."
    assert _rows(db_session, control) == []


@pytest.mark.parametrize("bad", ["abc", "3.5", None, True, [1]])
def test_control_id_that_is_not_a_whole_number_answers_400(db_session, make_org, client, login_as, bad):
    org, user = _org_with_user(db_session, make_org, "cm-bad", role="admin")
    component = _component(db_session, org)
    login_as(client, user)

    resp = client.post(_map_url(component), json={"control_id": bad})

    assert resp.status_code == 400


def test_summary_counts_every_status_and_adds_up_to_the_total(db_session, make_org, client, login_as):
    org, user = _org_with_user(db_session, make_org, "cm-sum", role="admin")
    component = _component(db_session, org)
    controls = _controls(db_session, 6)
    _adopt(db_session, org, user, controls[0])
    statuses = ["planned", "in_progress", "implemented", "verified", "not_applicable", "waived"]
    login_as(client, user)
    for control, status in zip(controls, statuses):
        resp = client.post(_map_url(component), json={"control_id": control.id, "implementation_status": status})
        assert resp.status_code == 200, resp.get_data(as_text=True)

    summary = client.get(f"/dashboard/api/applications/{component.id}/compliance").get_json()["summary"]

    assert summary["total_controls"] == 6
    assert all(summary[status] == 1 for status in statuses)
    assert sum(summary[status] for status in statuses) == summary["total_controls"]
