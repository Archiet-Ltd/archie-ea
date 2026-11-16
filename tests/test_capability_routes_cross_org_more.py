"""One test per capability route that takes another organisation's capability id: it answers 404 and names nothing of B."""
from __future__ import annotations

import uuid

import pytest

from app import db

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, *, admin):
    from app.models.user import Role, User

    role_name = "Administrator" if admin else "User"
    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name=role_name).first()
    user = User(
        email=f"capfence2-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(), last_name="Tester", organization_id=org.id, confirmed=True,
        enterprise_role="enterprise_architect", role=role)
    user.password = "correct-horse-battery"
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def world(db_session, make_org):
    from app.models.apqc_process import APQCProcess, CapabilityProcessMapping
    from app.models.business_capabilities import BusinessCapability

    tag = uuid.uuid4().hex[:6]
    org_a, org_b = make_org(f"capfence2-a-{tag}"), make_org(f"capfence2-b-{tag}")
    cap_a = BusinessCapability(name=f"Alpha billing {tag}", code=f"AB-{tag}", organization_id=org_a.id, level=1)
    cap_a2 = BusinessCapability(name=f"Alpha payroll {tag}", code=f"AP-{tag}", organization_id=org_a.id, level=1)
    cap_b = BusinessCapability(name=f"Bravo secret ledger {tag}", code=f"BS-{tag}", organization_id=org_b.id, level=1)
    process = APQCProcess(process_code=f"9.{tag}", process_name=f"Shared close process {tag}")
    db_session.add_all([cap_a, cap_a2, cap_b, process])
    db_session.flush()
    mapping = CapabilityProcessMapping(capability_id=cap_b.id, apqc_process_id=process.id)
    db_session.add(mapping)
    db_session.flush()
    world = {
        "cap_a": cap_a, "cap_a2": cap_a2, "cap_b": cap_b, "process": process,
        "b_name": cap_b.name, "b_id": cap_b.id, "b_code": cap_b.code,
        "architect_a": _user(db_session, org_a, "architecta", admin=False),
        "admin_a": _user(db_session, org_a, "admina", admin=True),
    }
    # A request starts with an empty identity map; the test must not hand it B's row for free.
    db_session.expunge_all()
    return world


def _assert_hidden(resp, w):
    assert resp.status_code == 404, resp.get_data(as_text=True)[:300]
    body = resp.get_data(as_text=True)
    assert w["b_name"] not in body
    assert w["b_code"] not in body


def _b_unchanged(db_session, w):
    db_session.expire_all()
    name = db_session.execute(
        db.text("SELECT name FROM business_capability WHERE id = :i"), {"i": w["b_id"]}).scalar()
    assert name is not None, "organisation B's capability was removed"
    assert name == w["b_name"]


def test_capability_request_foreign_capability_is_404(db_session, client, login_as, world):
    from app.models.adm_kanban import KanbanCard

    login_as(client, world["architect_a"])
    before = KanbanCard.query.count()
    resp = client.post("/api/enterprise/capabilities/request", json={
        "capability_id": world["b_id"], "business_need": "Need it", "business_driver": "Growth"})
    _assert_hidden(resp, world)
    assert KanbanCard.query.count() == before


def test_archimate_mappings_foreign_capability_is_404(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    _assert_hidden(client.get(f"/capability-map/api/capabilities/{world['b_id']}/archimate-mappings"), world)


def test_create_mapping_foreign_capability_is_404(db_session, client, login_as, world):
    from app.models.business_capabilities import ApplicationCapabilityCoverage

    login_as(client, world["admin_a"])
    resp = client.post("/capability-map/api/mappings", json={
        "capability_id": world["b_id"], "applications": [{"app_id": 1, "mapping_data": {}}]})
    _assert_hidden(resp, world)
    assert ApplicationCapabilityCoverage.query.filter_by(capability_id=world["b_id"]).count() == 0


def test_capability_processes_foreign_capability_is_404(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    _assert_hidden(client.get(f"/capability-map/api/capabilities/{world['b_id']}/processes"), world)


def test_full_traceability_foreign_capability_is_404(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    _assert_hidden(client.get(f"/capability-map/api/capabilities/{world['b_id']}/full-traceability"), world)


def test_process_capabilities_names_no_foreign_capability(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    resp = client.get(f"/capability-map/api/processes/{world['process'].id}/capabilities")
    body = resp.get_data(as_text=True)
    assert world["b_name"] not in body
    assert world["b_code"] not in body


def test_vendor_suggestions_foreign_capability_is_404(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    _assert_hidden(client.get(f"/capability-map/api/vendors/capability-suggestions/{world['b_id']}"), world)


def test_abacus_compare_foreign_capability_is_404(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    _assert_hidden(client.get(
        f"/admin/abacus/consolidation/compare/{world['cap_a'].id}/{world['b_id']}"), world)
    _assert_hidden(client.get(
        f"/admin/abacus/consolidation/compare/{world['b_id']}/{world['cap_a'].id}"), world)


def test_abacus_merge_foreign_capability_is_404(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    _assert_hidden(client.post("/admin/abacus/consolidation/merge", json={
        "source_id": world["cap_a"].id, "target_id": world["b_id"]}), world)
    _assert_hidden(client.post("/admin/abacus/consolidation/merge", json={
        "source_id": world["b_id"], "target_id": world["cap_a"].id}), world)
    _b_unchanged(db_session, world)


def test_abacus_bulk_merge_leaves_foreign_capability_alone(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    resp = client.post("/admin/abacus/consolidation/bulk-merge", json={"pairs": [
        {"source_id": world["b_id"], "target_id": world["cap_a"].id},
        {"source_id": world["cap_a"].id, "target_id": world["b_id"]}]})
    assert world["b_name"] not in resp.get_data(as_text=True)
    _b_unchanged(db_session, world)


def test_own_capability_still_answers(db_session, client, login_as, world):
    login_as(client, world["admin_a"])
    assert client.get(
        f"/capability-map/api/capabilities/{world['cap_a'].id}/processes").status_code == 200
    assert client.get(
        f"/capability-map/api/capabilities/{world['cap_a'].id}/archimate-mappings").status_code == 200


def test_own_capability_refused_when_no_active_organisation(db_session, client, login_as, world, monkeypatch):
    """With no active organisation known, a capability route answers 404 instead of every organisation's row."""
    cap = world["cap_a"]
    login_as(client, world["admin_a"])
    monkeypatch.setattr("app.middleware.tenant_context.current_org_id", lambda: None)
    for url in (f"/enterprise/capabilities/{cap.id}", f"/enterprise/capabilities/{cap.id}/fact-sheet"):
        assert client.get(url).status_code == 404, url
    resp = client.put(f"/enterprise/capabilities/{cap.id}", json={"name": "Renamed with no organisation"})
    assert resp.status_code == 404
    resp = client.post("/capability-map/api/roadmap/gaps/add-from-capability", json={
        "capability_id": cap.id, "capability_type": "business", "capability_name": "No org"})
    assert resp.status_code == 404
    db_session.expire_all()
    assert cap.name != "Renamed with no organisation"
