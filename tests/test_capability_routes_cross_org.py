"""A capability id from another organisation answers 404 on every route that reads, changes or deletes it."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, *, admin):
    from app.models.user import Role, User

    role_name = "Administrator" if admin else "User"
    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name=role_name).first()
    user = User(
        email=f"capfence-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(), last_name="Tester", organization_id=org.id, confirmed=True,
        enterprise_role="enterprise_architect", role=role)
    user.password = "correct-horse-battery"
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def world(db_session, make_org):
    from app.models.business_capabilities import BusinessCapability

    tag = uuid.uuid4().hex[:6]
    org_a, org_b = make_org(f"capfence-a-{tag}"), make_org(f"capfence-b-{tag}")
    parent_b = BusinessCapability(name=f"Ledger parent {tag}", code=f"LP-{tag}", organization_id=org_b.id, level=1)
    db_session.add(parent_b)
    db_session.flush()
    cap_a = BusinessCapability(name=f"Alpha billing {tag}", code=f"AB-{tag}", organization_id=org_a.id, level=1)
    cap_b = BusinessCapability(
        name=f"Bravo secret ledger {tag}", code=f"BS-{tag}", organization_id=org_b.id, level=2,
        parent_capability_id=parent_b.id)
    db_session.add_all([cap_a, cap_b])
    db_session.flush()
    return {
        "cap_a": cap_a, "cap_b": cap_b, "parent_b": parent_b,
        "architect_a": _user(db_session, org_a, "architecta", admin=False),
        "admin_a": _user(db_session, org_a, "admina", admin=True),
        "admin_b": _user(db_session, org_b, "adminb", admin=True),
    }


def _b_unchanged(db_session, w):
    from app.models.business_capabilities import BusinessCapability

    db_session.expire_all()
    row = db_session.get(BusinessCapability, w["cap_b"].id)
    assert row is not None, "organisation B's capability was deleted"
    assert row.name == w["cap_b"].name
    assert row.parent_capability_id == w["parent_b"].id


def _assert_hidden(resp, w):
    assert resp.status_code == 404
    body = resp.get_data(as_text=True)
    assert w["cap_b"].name not in body
    assert w["parent_b"].name not in body


def test_read_foreign_capability_is_404(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    _assert_hidden(client.get(f"/enterprise/capabilities/{world['cap_b'].id}"), world)
    _b_unchanged(db_session, world)


def test_fact_sheet_foreign_capability_is_404(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    _assert_hidden(client.get(f"/enterprise/capabilities/{world['cap_b'].id}/fact-sheet"), world)
    _b_unchanged(db_session, world)


def test_update_foreign_capability_is_404(db_session, client, login_as, world):
    login_as(client, world["architect_a"])
    resp = client.put(f"/enterprise/capabilities/{world['cap_b'].id}",
                      json={"name": "Taken over by A"})
    _assert_hidden(resp, world)
    _b_unchanged(db_session, world)


def test_delete_foreign_capability_is_404(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    _assert_hidden(client.delete(f"/enterprise/capabilities/{world['cap_b'].id}"), world)
    _b_unchanged(db_session, world)


def test_add_foreign_capability_to_roadmap_is_404(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    resp = client.post("/capability-map/api/roadmap/gaps/add-from-capability", json={
        "capability_id": world["cap_b"].id, "capability_type": "business",
        "capability_name": "Bravo borrowed"})
    assert resp.status_code == 404
    assert world["cap_b"].name not in resp.get_data(as_text=True)
    _b_unchanged(db_session, world)


def test_traceability_foreign_capability_is_404(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    _assert_hidden(client.get(
        f"/capability-map/api/capabilities/{world['cap_b'].id}/traceability"), world)
    _b_unchanged(db_session, world)


def test_own_capability_read_and_fact_sheet(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    assert world["cap_a"].name in client.get(f"/enterprise/capabilities/{world['cap_a'].id}").get_data(as_text=True)
    assert client.get(f"/enterprise/capabilities/{world['cap_a'].id}/fact-sheet").status_code == 200


def test_own_capability_update_by_architect_and_admin(db_session, client, login_as, world):
    login_as(client, world["architect_a"])
    resp = client.put(f"/enterprise/capabilities/{world['cap_a'].id}", json={"name": "Alpha renamed once"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    login_as(client, world["admin_a"])
    resp = client.put(f"/enterprise/capabilities/{world['cap_a'].id}", json={"name": "Alpha renamed twice"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    db_session.expire_all()
    assert world["cap_a"].name == "Alpha renamed twice"


def test_own_capability_delete_by_admin(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    cap_id = world["cap_a"].id
    resp = client.delete(f"/enterprise/capabilities/{cap_id}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert client.get(f"/enterprise/capabilities/{cap_id}").status_code == 404


def test_own_capability_roadmap_add_and_traceability(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    resp = client.post("/capability-map/api/roadmap/gaps/add-from-capability", json={
        "capability_id": world["cap_a"].id, "capability_type": "business",
        "capability_name": world["cap_a"].name})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    resp = client.get(f"/capability-map/api/capabilities/{world['cap_a'].id}/traceability")
    assert resp.status_code == 200, resp.get_data(as_text=True)
