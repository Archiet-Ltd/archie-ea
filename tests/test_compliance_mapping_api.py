"""Mapping a control to an application stores status, notes and the evidence link, and reads them back.

Covers the two JSON endpoints a person's tooling uses to map a control and edit that mapping.
A second organisation can neither map onto nor edit the first organisation's records.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_work_package_round3 import _org_with_user

pytestmark = pytest.mark.usefixtures("db_session")


def _component(db_session, org, name="Billing"):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=f"{name} {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _control(db_session):
    from app.models.compliance_models import ComplianceControl, RegulatoryFramework

    framework = RegulatoryFramework(code=f"FW{uuid.uuid4().hex[:6]}", name="Test Framework", category="security")
    db_session.add(framework)
    db_session.flush()
    control = ComplianceControl(framework_id=framework.id, control_code="AC-2", title="Account management")
    db_session.add(control)
    db_session.flush()
    return control


def _map_url(component):
    return f"/dashboard/api/applications/{component.id}/compliance/map"


def test_mapping_is_created_edited_and_read_back(db_session, make_org, client, login_as):
    org, user = _org_with_user(db_session, make_org, "cm-a", role="admin")
    component, control = _component(db_session, org), _control(db_session)
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
    login_as(client, user)

    resp = client.post(_map_url(component), json={"control_id": control.id, "implementation_status": "bogus"})

    assert resp.status_code == 400


def test_another_organisation_gets_404(db_session, make_org, client, login_as):
    org_a, user_a = _org_with_user(db_session, make_org, "cm-b1", role="admin")
    org_b, user_b = _org_with_user(db_session, make_org, "cm-b2", role="admin")
    component, control = _component(db_session, org_a), _control(db_session)
    login_as(client, user_a)
    mapping = client.post(_map_url(component), json={"control_id": control.id, "notes": "mine"}).get_json()["mapping"]

    login_as(client, user_b)
    other_map = client.post(_map_url(component), json={"control_id": control.id})
    other_edit = client.put(f"/dashboard/api/compliance/mappings/{mapping['id']}", json={"notes": "stolen"})
    other_read = client.get(f"/dashboard/api/applications/{component.id}/compliance")

    assert other_map.status_code == 404
    assert other_edit.status_code == 404
    assert other_read.status_code == 404

    login_as(client, user_a)
    back = client.get(f"/dashboard/api/applications/{component.id}/compliance").get_json()
    assert back["mappings"][0]["notes"] == "mine"
