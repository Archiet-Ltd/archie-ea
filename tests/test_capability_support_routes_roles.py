"""Recording, changing and removing application support takes the same role as the other application writes."""
from __future__ import annotations

import uuid

import pytest

from tests.test_capability_support_writer import seed

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, role_name):
    from app.models.user import Role, User

    user = User(email=f"roles-{uuid.uuid4().hex[:8]}@example.com", first_name="R", last_name="O",
                organization_id=org.id, confirmed=True, enterprise_role="solution_architect")
    user.password = "Sup3rSecret!23"
    user.role = Role.query.filter_by(name=role_name).one()
    db_session.add(user)
    db_session.flush()
    return user


def _world(db_session, make_org):
    from app.services import capability_support as cs
    from tests.test_capability_support_existing_rows import _record_of

    org = make_org("roles")
    app_row, cap = seed(db_session, org)
    record = _record_of(org, cap)
    made = cs.record_capability_support(org.id, app_row.archimate_element_id, cap.archimate_element_id)
    return org, app_row, record, made


def _requests(app_row, record, row_id):
    base = f"/dashboard/applications/{app_row.id}"
    return {
        "create": (f"{base}/capability-mappings", {"capability_id": record.id}),
        "update": (f"{base}/capability-mapping/{row_id}/update", {"support_level": "partial"}),
        "delete": (f"{base}/capability-mappings/{row_id}/delete", {}),
    }


@pytest.mark.parametrize("action", ["create", "update", "delete"])
def test_a_viewer_cannot_change_support(client, login_as, db_session, make_org, action):
    from app.services import capability_support as cs

    org, app_row, record, made = _world(db_session, make_org)
    viewer = _user(db_session, org, "Viewer")
    url, data = _requests(app_row, record, made.row_id)[action]
    login_as(client, viewer)
    assert client.post(url, data=data).status_code == 403
    rows = cs.support_rows(org.id)
    assert len(rows) == 1 and rows[0]["support_level"] != "linked"


def test_an_architect_can_record_change_and_remove_support(client, login_as, db_session, make_org):
    from app.services import capability_support as cs

    org, app_row, record, made = _world(db_session, make_org)
    architect = _user(db_session, org, "Architect")
    requests = _requests(app_row, record, made.row_id)
    login_as(client, architect)
    url, data = requests["update"]
    assert client.post(url, data={"support_level": "legacy"}).status_code in (302, 303)
    assert [r["support_level"] for r in cs.support_rows(org.id)] == ["legacy"]
    url, data = requests["create"]
    assert client.post(url, data=data).status_code in (302, 303)
    assert len(cs.support_rows(org.id)) == 1
    url, data = requests["delete"]
    login_as(client, architect)
    assert client.post(url, data=data).status_code in (302, 303)
    assert cs.support_rows(org.id) == []
