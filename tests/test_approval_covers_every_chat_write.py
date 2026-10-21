"""With approval required, every chat write waits for an approver.

REQUIRE_AI_APPROVAL on, two organisations. update-vendor, update-capability-mapping,
bulk-update-applications and bulk-update-capabilities answer 202, queue (one
approval per item for the bulk routes) and change nothing until an approver in
the same organisation accepts. With approval off they write as before. Exactly
one handler owns PUT /ai-chat/data/update-application/<id>.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from tests.test_chat_matrix_write_gate import _user
from tests.test_platform_admin_route_gates import _user as _admin_user
from tests.test_chat_update_editable_fields import (  # noqa: F401  (fixtures)
    D, _bulk, _csrf_off, _row, _send, world,
)

pytestmark = pytest.mark.usefixtures("db_session")

CHANGE = {
    "vendor": {"description": "queued change"},
    "mapping": {"coverage_percentage": 61, "notes": "queued change"},
}


def _approvals(db_session):
    db_session.expire_all()
    return db_session.execute(text("select count(*) from ai_chat_crud_approvals")).scalar()


@pytest.fixture
def gate_on(monkeypatch, app):
    monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", True)


@pytest.fixture
def approvers(db_session, world):
    # A vendor row is shared, so a vendor change is applied only by a platform admin.
    a = _admin_user(db_session, world["a"], platform=True)
    b = _user(db_session, world["b"], "architect", "approver-b")
    db_session.commit()
    db_session.commit()
    return {"a": a, "b": b, "a_platform": a}


def _approve(client, login_as, user, approval_id):
    login_as(client, user)
    return client.post(f"/ai-chat/approvals/{approval_id}/approve", headers={"X-CSRFToken": "test"})


@pytest.mark.parametrize("kind", ["vendor", "mapping"])
def test_single_route_queues_then_applies_after_approval(
        client, login_as, db_session, world, approvers, gate_on, kind):
    rid = world["recs_a"][kind]
    before, other = _row(db_session, kind, rid), _row(db_session, kind, world["recs_b"][kind])
    n = _approvals(db_session)
    resp = _send(client, login_as, world["architect"], kind, rid, CHANGE[kind])
    assert resp.status_code == 202, resp.get_data(as_text=True)
    assert _approvals(db_session) == n + 1
    assert _row(db_session, kind, rid) == before
    approval_id = resp.get_json()["approval_id"]

    # An approver in the other organisation cannot see or apply it.
    denied = _approve(client, login_as, approvers["b"], approval_id)
    assert denied.status_code in (403, 404), denied.get_data(as_text=True)
    assert _row(db_session, kind, rid) == before
    assert _row(db_session, kind, world["recs_b"][kind]) == other

    ok = _approve(client, login_as, approvers["a_platform" if kind == "vendor" else "a"], approval_id)
    assert ok.status_code == 200, ok.get_data(as_text=True)
    after = json.loads(_row(db_session, kind, rid))
    for field, value in CHANGE[kind].items():
        assert after[field] == value
    assert _row(db_session, kind, world["recs_b"][kind]) == other


@pytest.mark.parametrize("kind", ["vendor", "mapping"])
def test_single_route_with_approval_off_writes_directly(
        client, login_as, db_session, world, kind):
    rid = world["recs_a"][kind]
    n = _approvals(db_session)
    resp = _send(client, login_as, world["architect"], kind, rid, CHANGE[kind])
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _approvals(db_session) == n
    assert json.loads(_row(db_session, kind, rid))["description" if kind == "vendor" else "notes"] == "queued change"


@pytest.mark.parametrize("kind", ["vendor", "mapping"])
def test_single_route_refuses_forbidden_field_before_queueing(
        client, login_as, db_session, world, gate_on, kind):
    n = _approvals(db_session)
    resp = _send(client, login_as, world["architect"], kind, world["recs_a"][kind], {"id": 987654})
    assert resp.status_code == 400
    assert "id" in resp.get_data(as_text=True)
    assert _approvals(db_session) == n


BULK = [
    ("bulk-update-applications", "application_id", "app", "description"),
    ("bulk-update-capabilities", "capability_id", "bcap", "description"),
]


@pytest.mark.parametrize("path,id_key,kind,field", BULK)
def test_bulk_route_queues_one_approval_per_item_then_applies(
        client, login_as, db_session, world, approvers, gate_on, path, id_key, kind, field):
    rid = world["recs_a"][kind]
    before = _row(db_session, kind, rid)
    n = _approvals(db_session)
    resp = _bulk(client, login_as, world, path, id_key, rid, {field: "bulk queued"})
    assert resp.status_code == 202, resp.get_data(as_text=True)
    body = resp.get_json()
    assert len(body["queued"]) == 1
    assert _approvals(db_session) == n + 1
    assert _row(db_session, kind, rid) == before
    approval_id = body["queued"][0]["approval_id"]

    denied = _approve(client, login_as, approvers["b"], approval_id)
    assert denied.status_code in (403, 404)
    assert _row(db_session, kind, rid) == before

    ok = _approve(client, login_as, approvers["a_platform" if kind == "vendor" else "a"], approval_id)
    assert ok.status_code == 200, ok.get_data(as_text=True)
    assert json.loads(_row(db_session, kind, rid))[field] == "bulk queued"


@pytest.mark.parametrize("path,id_key,kind,field", BULK)
def test_bulk_route_with_two_items_queues_two(
        client, login_as, db_session, world, gate_on, path, id_key, kind, field):
    rid = world["recs_a"][kind]
    n = _approvals(db_session)
    login_as(client, world["architect"])
    resp = client.post(f"{D}/{path}", headers={"X-CSRFToken": "test"}, json={"updates": [
        {id_key: rid, "update_data": {"name": "first name"}},
        {id_key: rid, "update_data": {"name": "second name"}},
    ]})
    assert resp.status_code == 202
    assert len(resp.get_json()["queued"]) == 2
    assert _approvals(db_session) == n + 2


@pytest.mark.parametrize("path,id_key,kind,field", BULK)
def test_bulk_route_with_approval_off_writes_directly(
        client, login_as, db_session, world, path, id_key, kind, field):
    rid = world["recs_a"][kind]
    n = _approvals(db_session)
    resp = _bulk(client, login_as, world, path, id_key, rid, {field: "direct"})
    assert resp.status_code == 200
    assert resp.get_json()["summary"]["success"] == 1
    assert _approvals(db_session) == n
    assert json.loads(_row(db_session, kind, rid))[field] == "direct"


def test_one_handler_owns_update_application(app):
    rules = [r for r in app.url_map.iter_rules()
             if r.rule.startswith("/ai-chat/data/update-application/") and "PUT" in r.methods]
    assert len(rules) == 1, [(r.rule, r.endpoint) for r in rules]
    assert rules[0].endpoint == "unified_ai_chat.update_application_metadata"
    from app.modules.ai_chat.routes import data_interaction_routes
    assert not hasattr(data_interaction_routes, "update_application")


def test_update_application_is_answered_by_the_remaining_handler(
        client, login_as, db_session, world, gate_on):
    n = _approvals(db_session)
    resp = _send(client, login_as, world["architect"], "app", world["recs_a"]["app"],
                 {"description": "x"})
    assert resp.status_code == 202
    assert _approvals(db_session) == n + 1


@pytest.mark.parametrize("path,id_key,kind,field", BULK)
def test_same_change_to_two_records_is_two_approvals(
        client, login_as, db_session, world, approvers, gate_on, path, id_key, kind, field):
    from tests.test_chat_update_editable_fields import _records

    second = _records(db_session, world["a"])[kind]
    db_session.commit()
    first = world["recs_a"][kind]
    n = _approvals(db_session)
    login_as(client, world["architect"])
    resp = client.post(f"{D}/{path}", headers={"X-CSRFToken": "test"}, json={"updates": [
        {id_key: first, "update_data": {"lifecycle_status" if kind == "app" else "level": "x1"}},
        {id_key: second, "update_data": {"lifecycle_status" if kind == "app" else "level": "x1"}},
    ]})
    assert resp.status_code == 202, resp.get_data(as_text=True)
    queued = resp.get_json()["queued"]
    assert len(queued) == 2 and queued[0]["approval_id"] != queued[1]["approval_id"]
    assert _approvals(db_session) == n + 2
