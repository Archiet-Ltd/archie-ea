"""The control evidence and control test routes, and the dashboard section, as real requests.

Two real organisations; the second organisation's rows are never reachable from the first.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from tests.test_control_evidence import _adopt, _application, _control, _framework


def _person(db_session, org_id, label, *, role="Architect", enterprise_role=None):
    from app.models.user import Role, User

    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.test",
        first_name=label,
        last_name="Person",
        organization_id=org_id,
        confirmed=True,
        role=Role.query.filter_by(name=role).first(),
        enterprise_role=enterprise_role,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _world(db_session, make_org):
    org_a, org_b = make_org("rt-a"), make_org("rt-b")
    framework = _framework(db_session)
    comp_a, _ = _application(db_session, org_a.id, "A app")
    comp_b, _ = _application(db_session, org_b.id, "B app")
    row_a = _adopt(db_session, org_a.id, _control(db_session, framework, "RT-1"), comp_a)
    row_b = _adopt(db_session, org_b.id, _control(db_session, framework, "RT-2"), comp_b)
    db_session.commit()
    return org_a, org_b, row_a, row_b


def test_a_compliance_officer_records_a_test_and_it_persists(app, db_session, make_org, client, login_as):
    org_a, _b, row_a, _rb = _world(db_session, make_org)
    officer = _person(db_session, org_a.id, "Officer", enterprise_role="compliance")
    login_as(client, officer)
    posted = client.post(
        f"/dashboard/api/compliance/controls/{row_a.id}/tests",
        json={"test_kind": "design", "result": "ineffective", "notes": "no review step"},
    )
    assert posted.status_code == 201, posted.get_data(as_text=True)
    login_as(client, officer)
    listed = client.get(f"/dashboard/api/compliance/controls/{row_a.id}/tests")
    body = listed.get_json()
    assert listed.status_code == 200 and body["effectiveness"] == "ineffective"
    assert [t["notes"] for t in body["tests"]] == ["no review step"]


def test_another_organisations_control_answers_404_for_every_route(app, db_session, make_org, client, login_as):
    org_a, _b, row_a, row_b = _world(db_session, make_org)
    officer = _person(db_session, org_a.id, "Officer", enterprise_role="compliance")
    for call in (
        lambda: client.get(f"/dashboard/api/compliance/controls/{row_b.id}/tests"),
        lambda: client.post(
            f"/dashboard/api/compliance/controls/{row_b.id}/tests", json={"test_kind": "design", "result": "effective"}
        ),
        lambda: client.put(f"/dashboard/api/compliance/mappings/{row_b.id}", json={"evidence_url": "https://x.example/e"}),
    ):
        login_as(client, officer)
        assert call().status_code == 404
    db_session.expire_all()
    assert row_b.evidence_url is None
    from app.models.control_test import ControlTest

    assert ControlTest.query.filter_by(application_control_id=row_b.id).count() == 0


def test_a_decoy_id_does_not_reach_the_other_organisations_row(app, db_session, make_org, client, login_as):
    """B's control row id equals one of A's application ids: naming that number as a control
    row must not reach B's row, and A's application must not be mistaken for a control row."""
    from app.models.application_compliance import ApplicationComplianceControl
    from app.models.application_portfolio import ApplicationComponent
    from app.models.control_test import ControlTest

    org_a, org_b = make_org("decoy-a"), make_org("decoy-b")
    framework = _framework(db_session)
    shared = 910_000 + uuid.uuid4().int % 50_000
    db_session.add(ApplicationComponent(id=shared, name="A decoy app", organization_id=org_a.id))
    comp_b, _ = _application(db_session, org_b.id, "B app")
    row_b = ApplicationComplianceControl(
        id=shared, organization_id=org_b.id, application_id=comp_b.id,
        control_id=_control(db_session, framework, "DC-1").id,
    )
    db_session.add(row_b)
    db_session.commit()
    officer = _person(db_session, org_a.id, "Officer", enterprise_role="compliance")

    login_as(client, officer)
    assert client.get(f"/dashboard/api/compliance/controls/{shared}/tests").status_code == 404
    login_as(client, officer)
    assert client.post(
        f"/dashboard/api/compliance/controls/{shared}/tests", json={"test_kind": "design", "result": "effective"}
    ).status_code == 404
    login_as(client, officer)
    assert client.put(
        f"/dashboard/api/compliance/mappings/{shared}", json={"evidence_owner_id": officer.id}
    ).status_code == 404
    db_session.expire_all()
    assert row_b.evidence_owner_id is None
    assert ControlTest.query.filter_by(application_control_id=shared).count() == 0


def test_a_reader_cannot_write(app, db_session, make_org, client, login_as):
    org_a, _b, row_a, _rb = _world(db_session, make_org)
    reader = _person(db_session, org_a.id, "Reader", role="Viewer", enterprise_role="compliance")
    login_as(client, reader)
    assert client.post(
        f"/dashboard/api/compliance/controls/{row_a.id}/tests", json={"test_kind": "design", "result": "effective"}
    ).status_code == 403
    login_as(client, reader)
    assert client.put(
        f"/dashboard/api/compliance/mappings/{row_a.id}", json={"evidence_url": "https://x.example/e"}
    ).status_code == 403


def test_bad_values_are_refused_with_a_message(app, db_session, make_org, client, login_as):
    org_a, _b, row_a, _rb = _world(db_session, make_org)
    officer = _person(db_session, org_a.id, "Officer", enterprise_role="compliance")
    login_as(client, officer)
    bad = client.post(
        f"/dashboard/api/compliance/controls/{row_a.id}/tests", json={"test_kind": "weekly", "result": "effective"}
    )
    assert bad.status_code == 400 and "test_kind" in bad.get_json()["error"]
    login_as(client, officer)
    date = client.put(f"/dashboard/api/compliance/mappings/{row_a.id}", json={"evidence_valid_until": "next year"})
    assert date.status_code == 400


def test_evidence_and_owner_are_saved_through_the_one_writer(app, db_session, make_org, client, login_as):
    org_a, org_b, row_a, _rb = _world(db_session, make_org)
    officer = _person(db_session, org_a.id, "Officer", enterprise_role="compliance")
    outsider = _person(db_session, org_b.id, "Outsider")
    login_as(client, officer)
    ok = client.put(
        f"/dashboard/api/compliance/mappings/{row_a.id}",
        json={"evidence_url": "https://e.example/a", "evidence_valid_until": "2027-03-01",
              "evidence_owner_id": officer.id},
    )
    assert ok.status_code == 200, ok.get_data(as_text=True)
    login_as(client, officer)
    refused = client.put(f"/dashboard/api/compliance/mappings/{row_a.id}", json={"evidence_owner_id": outsider.id})
    assert refused.status_code == 400
    db_session.expire_all()
    assert row_a.evidence_owner_id == officer.id and row_a.evidence_valid_until == datetime(2027, 3, 1)
    assert row_a.evidence_source == "person"


def test_the_dashboard_lists_the_gap_for_its_own_organisation_only(app, db_session, make_org, client, login_as):
    org_a, _b, row_a, row_b = _world(db_session, make_org)
    row_a.evidence_url = "https://e.example/old"
    row_a.evidence_valid_until = datetime.utcnow() - timedelta(days=2)
    db_session.commit()
    officer = _person(db_session, org_a.id, "Officer", enterprise_role="compliance")
    login_as(client, officer)
    page = client.get("/dashboard/compliance")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert 'data-testid="evidence-gaps"' in html
    assert f'data-control-row="{row_a.id}"' in html and f'data-control-row="{row_b.id}"' not in html
    assert "Expired" in html and "Record test" in html
    assert "No evidence is missing or out of date" not in html


def test_the_dashboard_says_so_when_nothing_is_missing(app, db_session, make_org, client, login_as):
    org = make_org("rt-empty")
    reader = _person(db_session, org.id, "Reader", role="Viewer", enterprise_role="compliance")
    login_as(client, reader)
    html = client.get("/dashboard/compliance").get_data(as_text=True)
    assert "No evidence is missing or out of date" in html
    assert "Record test" not in html
