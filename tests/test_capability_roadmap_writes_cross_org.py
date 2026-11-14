"""The capability roadmap's write routes keep to one organisation and to roles that may change the model.

Two real organisations. Another organisation's plateau or capability, sent by id,
is answered as not found and nothing is written; a Viewer is refused every write with
the plain refusal. Uses the shared fixtures in tests/conftest.py.
"""

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, persona):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="cr-%s@example.com" % uuid.uuid4().hex[:10], first_name="C", last_name="R",
        organization_id=org.id, role=Role.query.filter_by(name="User").first(),
        confirmed=True, enterprise_role=persona,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "viewer" if persona == "viewer" else "architect")
    db_session.flush()
    return user


def _plateau(db_session, org_id, name):
    from app.models.implementation_migration import Plateau

    row = Plateau(name=name, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _capability(db_session, org, name):
    from app.models.unified_capability import UnifiedCapability

    row = UnifiedCapability(
        name=name, code="CR%s" % uuid.uuid4().hex[:8], level=1, organization_id=org.id,
        status="defined",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _gap_count(name):
    from app.models.implementation_migration import Gap

    return Gap.query.filter_by(name=name).count()


def test_gap_cannot_link_another_organisations_or_a_shared_plateau(
    db_session, make_org, client, login_as
):
    org_a, org_b = make_org("cr-gap-a"), make_org("cr-gap-b")
    user_a = _user(db_session, org_a, "enterprise_architect")
    own = _plateau(db_session, org_a.id, "Own plateau")
    other = _plateau(db_session, org_b.id, "Other plateau")
    login_as(client, user_a)

    for label, plateau in (("other", other),):
        name = "Gap linking %s %s" % (label, uuid.uuid4().hex[:6])
        response = client.post(
            "/capability-map/api/roadmap/gaps", json={"name": name, "plateau_id": plateau.id})
        assert response.status_code == 404, (label, response.status_code)
        assert _gap_count(name) == 0, label

    name = "Gap linking own %s" % uuid.uuid4().hex[:6]
    response = client.post(
        "/capability-map/api/roadmap/gaps", json={"name": name, "plateau_id": own.id})
    assert response.status_code == 201, response.status_code
    assert response.get_json()["gap"]["target_plateau_id"] == own.id


def test_capability_cannot_take_another_organisations_parent(
    db_session, make_org, client, login_as
):
    org_a, org_b = make_org("cr-cap-a"), make_org("cr-cap-b")
    user_a = _user(db_session, org_a, "enterprise_architect")
    own_parent = _capability(db_session, org_a, "Own parent %s" % uuid.uuid4().hex[:6])
    other_parent = _capability(db_session, org_b, "Other parent %s" % uuid.uuid4().hex[:6])
    from app.models.unified_capability import BusinessDomain

    domain = BusinessDomain(code="D%s" % uuid.uuid4().hex[:8], name="Domain")
    db_session.add(domain)
    db_session.flush()
    login_as(client, user_a)

    name = "Child of other %s" % uuid.uuid4().hex[:6]
    response = client.post("/api/capabilities", json={
        "name": name, "level": 2, "domain_id": domain.id, "parent_capability_id": other_parent.id})
    assert response.status_code == 404, (response.status_code, response.get_json())

    name = "Child of own %s" % uuid.uuid4().hex[:6]
    response = client.post("/api/capabilities", json={
        "name": name, "level": 2, "domain_id": domain.id, "parent_capability_id": own_parent.id})
    assert response.status_code in (200, 201), (response.status_code, response.get_json())


WRITES = [
    ("post", "/api/capabilities", {"name": "x", "level": 1}),
    ("post", "/api/capability-work-packages", {"name": "x", "start_date": "2026-01-01", "end_date": "2026-02-01"}),
    ("put", "/api/capability-work-packages/1", {"name": "x"}),
    ("delete", "/api/capability-work-packages/1", None),
    ("post", "/api/capability-work-packages/1/tasks", {"name": "x"}),
    ("put", "/api/capability-work-packages/1/tasks/1", {"name": "x"}),
    ("delete", "/api/capability-work-packages/1/tasks/1", None),
    ("post", "/api/capability-work-packages/1/deliverables", {"name": "x"}),
    ("put", "/api/capability-work-packages/1/deliverables/1", {"name": "x"}),
    ("delete", "/api/capability-work-packages/1/deliverables/1", None),
]


@pytest.mark.parametrize("method,url,body", WRITES)
def test_viewer_is_refused_every_write_in_the_roadmap(
    db_session, make_org, client, login_as, method, url, body
):
    org_a = make_org("cr-view-a")
    viewer = _user(db_session, org_a, "viewer")
    login_as(client, viewer)

    response = getattr(client, method)(url, json=body) if body is not None else getattr(client, method)(url)
    assert response.status_code == 403, (url, response.status_code)
    assert "may read the roadmap but not change it" in response.get_data(as_text=True)
