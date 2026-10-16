"""The policy, compliance-check and privacy-assessment routes, and the dashboard sections, as
real requests. Two real organisations; the second organisation's rows are never reachable
from the first, including with ids that coincide.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from tests.test_control_assurance_routes import _person
from tests.test_control_evidence import _adopt, _application, _control, _framework
from tests.test_policy_clauses import _adopted_framework, _info_object


def _officer(db_session, org):
    return _person(db_session, org.id, "Officer", enterprise_role="compliance")


def _world(db_session, make_org):
    org = make_org("pr-a")
    framework = _adopted_framework(db_session, org.id)
    control = _control(db_session, framework, "PR-1")
    component, _ = _application(db_session, org.id, "Routed app")
    _adopt(
        db_session, org.id, control, component, implementation_status="implemented",
        evidence_url="https://e.test/old", evidence_valid_until=datetime.utcnow() - timedelta(days=3),
    )
    officer = _officer(db_session, org)
    db_session.commit()
    return org, control, component, officer


def _post(client, login_as, user, path, **kwargs):
    login_as(client, user)
    return client.post(path, **kwargs)


def test_a_compliance_officer_writes_binds_publishes_and_sees_the_violation(app, db_session, make_org, client, login_as):
    org, control, component, officer = _world(db_session, make_org)

    created = _post(client, login_as, officer, "/dashboard/api/compliance/policies", json={"name": "Access policy"})
    assert created.status_code == 201, created.get_data(as_text=True)
    policy_id = created.get_json()["policy"]["id"]
    assert created.get_json()["policy"]["is_active"] is False

    bound = _post(client, login_as, officer, f"/dashboard/api/compliance/policies/{policy_id}/clauses",
                  json={"clause_ref": "4.2", "clause_text": "Access is reviewed", "control_id": control.id})
    assert bound.status_code == 201, bound.get_data(as_text=True)

    published = _post(client, login_as, officer, f"/dashboard/api/compliance/policies/{policy_id}/publish", json={})
    assert published.status_code == 200 and published.get_json()["check"]["raised"] == 1

    login_as(client, officer)
    shown = client.get(f"/dashboard/api/compliance/policies/{policy_id}").get_json()
    assert shown["policy"]["version_number"] == 1
    assert [a["application_name"] for a in shown["clauses"][0]["applications"]] == [component.name]

    login_as(client, officer)
    html = client.get("/dashboard/compliance").get_data(as_text=True)
    assert 'data-testid="policy-violation-row"' in html and "evidence has expired" in html
    assert 'data-testid="policy"' in html and "Version 1, in force" in html and component.name in html

    # Reload the same list through a second check: no duplicate.
    again = _post(client, login_as, officer, "/dashboard/api/compliance/policies/check", json={})
    assert again.status_code == 200 and again.get_json()["raised"] == 0 and again.get_json()["open"] == 1


def test_another_organisations_policy_answers_404_for_every_route_even_with_a_matching_id(
    app, db_session, make_org, client, login_as
):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.policy_monitoring import ArchitecturePolicy

    org_a, control, _component, officer = _world(db_session, make_org)
    org_b = make_org("pr-b")
    shared = 920_000 + uuid.uuid4().int % 50_000
    # B's policy has the same number as one of A's applications.
    db_session.add(ApplicationComponent(id=shared, name="A decoy app", organization_id=org_a.id))
    db_session.add(ArchitecturePolicy(id=shared, name="B policy", organization_id=org_b.id, is_active=False))
    db_session.commit()
    base = f"/dashboard/api/compliance/policies/{shared}"

    for call in (
        lambda: client.get(base),
        lambda: client.post(base + "/clauses", json={"clause_ref": "1", "control_id": control.id}),
        lambda: client.post(base + "/publish", json={}),
        lambda: client.post(base + "/versions", json={}),
        lambda: client.delete(base + "/clauses/1"),
    ):
        login_as(client, officer)
        assert call().status_code == 404
    from sqlalchemy import text

    published = db_session.execute(
        text("SELECT published_at FROM architecture_policies WHERE id = :i"), {"i": shared}
    ).scalar_one()
    assert published is None


def test_binding_a_control_the_organisation_has_not_adopted_is_refused(app, db_session, make_org, client, login_as):
    org, _control_row, _component, officer = _world(db_session, make_org)
    foreign = _control(db_session, _framework(db_session), "NOPE-1")
    policy_id = _post(client, login_as, officer, "/dashboard/api/compliance/policies", json={"name": "P"}).get_json()["policy"]["id"]
    refused = _post(client, login_as, officer, f"/dashboard/api/compliance/policies/{policy_id}/clauses",
                    json={"clause_ref": "1", "control_id": foreign.id})
    assert refused.status_code == 409 and "adopted" in refused.get_json()["error"]
    missing = _post(client, login_as, officer, f"/dashboard/api/compliance/policies/{policy_id}/clauses", json={"clause_ref": "1"})
    assert missing.status_code == 400
    empty = _post(client, login_as, officer, f"/dashboard/api/compliance/policies/{policy_id}/publish", json={})
    assert empty.status_code == 409


def test_a_read_only_account_cannot_write_or_run_a_scan(app, db_session, make_org, client, login_as):
    org, control, component, _officer_row = _world(db_session, make_org)
    reader = _person(db_session, org.id, "Reader", role="Viewer", enterprise_role="compliance")
    for path, body in (
        ("/dashboard/api/compliance/policies", {"name": "x"}),
        ("/dashboard/api/compliance/policies/check", {}),
        ("/dashboard/api/compliance/privacy-assessments/1/status", {"status": "completed"}),
        ("/enterprise/api/policy-monitoring/scan", {"application_id": component.id}),
        ("/enterprise/api/policy-monitoring/scan", {}),
    ):
        assert _post(client, login_as, reader, path, json=body).status_code == 403, path


def test_the_scan_route_is_open_to_the_compliance_roles_and_fenced_to_the_organisation(
    app, db_session, make_org, client, login_as
):
    org_a, _control_row, component, officer = _world(db_session, make_org)
    other_org = make_org("pr-other")
    foreign, _ = _application(db_session, other_org.id, "Foreign app")
    db_session.commit()
    ok = _post(client, login_as, officer, "/enterprise/api/policy-monitoring/scan", json={"application_id": component.id})
    assert ok.status_code == 200 and ok.get_json()["success"] is True
    foreign_scan = _post(client, login_as, officer, "/enterprise/api/policy-monitoring/scan", json={"application_id": foreign.id})
    assert foreign_scan.get_json() == {"success": False, "error": "Application not found"}


def test_the_assessment_status_route_sets_it_and_refuses_another_organisations_element(
    app, db_session, make_org, client, login_as
):
    org, _c, _a, officer = _world(db_session, make_org)
    other = make_org("pr-priv-b")
    mine = _info_object(db_session, org.id, "Patient records", ["Health data"])
    theirs = _info_object(db_session, other.id, "Their records", ["Health data"])
    db_session.commit()
    ok = _post(client, login_as, officer, f"/dashboard/api/compliance/privacy-assessments/{mine.id}/status", json={"status": "completed"})
    assert ok.status_code == 200
    assert _post(client, login_as, officer, f"/dashboard/api/compliance/privacy-assessments/{theirs.id}/status", json={"status": "completed"}).status_code == 404
    assert _post(client, login_as, officer, f"/dashboard/api/compliance/privacy-assessments/{mine.id}/status", json={"status": "soon"}).status_code == 400
    db_session.expire_all()
    assert mine.acm_properties["privacy_assessment_status"]["value"] == "completed"
    assert not theirs.acm_properties
    login_as(client, officer)
    html = client.get("/dashboard/compliance").get_data(as_text=True)
    assert "Patient records" in html and "Their records" not in html


def test_the_dashboard_empty_states_and_a_reader_sees_no_actions(app, db_session, make_org, client, login_as):
    org = make_org("pr-empty")
    reader = _person(db_session, org.id, "Reader", role="Viewer", enterprise_role="compliance")
    login_as(client, reader)
    html = client.get("/dashboard/compliance").get_data(as_text=True)
    assert "No policies yet" in html
    assert "No processing activity meets an assessment criterion" in html
    assert 'data-testid="check-now"' not in html and 'data-testid="new-policy-form"' not in html
    assert "Record test" not in html and "@click=\"startPolicy()\"" not in html
