"""Only a Platform Admin edits, deletes or bulk-changes a shared vendor row.

VendorOrganization has no organisation column: one row per vendor is shared by
every organisation. An architect in organisation B who edits or deletes it,
through any page, API, chat route or approval replay, gets 403 with the plain
message and nothing changes. Creating a vendor stays open to an architect.
A successful gated edit records the acting user in last_manual_edit_by (the
approver on an approval replay). Two organisations throughout.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.test_chat_matrix_write_gate import _user as _org_user
from tests.test_platform_admin_route_gates import _login, _user as _admin_user

pytestmark = pytest.mark.usefixtures("db_session")

REFUSAL = (
    "Vendor records are shared by every organisation. "
    "Ask a platform administrator to change this vendor."
)
D = "/ai-chat/data"


@pytest.fixture(autouse=True)
def _csrf_off(monkeypatch, app):
    from app.utils import csrf_helper

    monkeypatch.setattr(csrf_helper, "validate_csrf", lambda *_a, **_k: None)
    monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", False)


@pytest.fixture
def world(app, db_session, make_org):
    from app.models.vendor.vendor_organization import VendorOrganization

    org_a, org_b = make_org("vea-a"), make_org("vea-b")
    architect_b = _org_user(db_session, org_b, "architect", "architect-b")
    platform = _admin_user(db_session, org_a, platform=True)
    platform2 = _admin_user(db_session, org_a, platform=True)
    tenant_admin_a = _admin_user(db_session, org_a, platform=False)
    tenant_admin_b = _admin_user(db_session, org_b, platform=False)
    vendor = VendorOrganization(name=f"Shared Vendor {uuid.uuid4().hex[:8]}",
                                description="original")
    db_session.add(vendor)
    db_session.commit()
    return {
        "a": org_a, "b": org_b,
        "architect_id": architect_b.id, "platform_id": platform.id,
        "platform2_id": platform2.id,
        "tenant_admin_a_id": tenant_admin_a.id, "tenant_admin_b_id": tenant_admin_b.id,
        "vendor_id": vendor.id, "vendor_name": vendor.name,
    }


def _vendor_row(db_session, vid):
    db_session.expire_all()
    return db_session.execute(
        text("select to_json(t)::text from vendor_organizations t where id = :i"), {"i": vid}
    ).scalar()


def _count(db_session):
    db_session.expire_all()
    return db_session.execute(text("select count(*) from vendor_organizations")).scalar()


def _writers(w):
    v = w["vendor_id"]
    form = {"name": "Renamed", "vendor_type": "software_vendor"}
    return [
        # (label, method, path, body, as_form)
        ("api v1 update", "put", f"/api/v1/vendors/{v}", {"name": "Renamed"}, False),
        ("api v1 delete", "delete", f"/api/v1/vendors/{v}", None, False),
        ("unified api update", "put", f"/api/vendors/{v}", {"name": "Renamed"}, False),
        ("unified api patch", "patch", f"/api/vendors/{v}", {"description": "x"}, False),
        ("unified api delete", "delete", f"/api/vendors/{v}", None, False),
        ("unified api bulk delete", "delete", "/api/vendors/bulk", {"ids": [v]}, False),
        ("management api update", "put", f"/vendor-management/api/vendors/{v}",
         {"name": "Renamed"}, False),
        ("management api delete", "delete", f"/vendor-management/api/vendors/{v}", None, False),
        ("management import", "post", "/vendor-management/import", {}, False),
        ("vendors page edit", "post", f"/vendors/{v}/edit", form, True),
        ("vendors page edit (put)", "put", f"/vendors/{v}/edit", {"name": "Renamed"}, False),
        ("vendors page delete", "post", f"/vendors/{v}/delete", {}, True),
        ("vendors page activate", "post", f"/vendors/{v}/activate",
         {"contract_value_annual": "1000"}, True),
        ("vendors page import", "post", "/vendors/import", {}, True),
        ("dashboard page edit", "post", f"/dashboard/applications/vendors/{v}/edit", form, True),
        ("dashboard page delete", "post", f"/dashboard/applications/vendors/{v}/delete", {}, True),
        ("dashboard page activate", "post", f"/dashboard/applications/vendors/{v}/activate",
         {"contract_value_annual": "1000"}, True),
        ("document analysis apply", "post", f"/dashboard/api/vendors/{v}/apply-analysis",
         {"analysis": {"vendor_data": {"description": "overwritten"}}}, False),
        ("chat update", "put", f"{D}/update-vendor/{v}", {"description": "overwritten"}, False),
    ]


def _send(client, method, path, body, as_form):
    fn = getattr(client, method)
    headers = {"X-CSRFToken": "test"}
    if body is None:
        return fn(path, headers=headers)
    if as_form:
        return fn(path, data=body, headers=headers)
    return fn(path, json=body, headers=headers)


def test_every_writer_path_is_in_the_url_map(app, world):
    import re

    pats = [re.compile("^" + re.sub(r"<[^>]+>", "[^/]+", r.rule) + "$")
            for r in app.url_map.iter_rules()]
    missing = [p for _l, _m, p, _b, _f in _writers(world) if not any(x.match(p) for x in pats)]
    assert missing == []


def test_architect_in_another_organisation_cannot_change_a_shared_vendor(
        app, db_session, client, login_as, world):
    for label, method, path, body, as_form in _writers(world):
        before, n = _vendor_row(db_session, world["vendor_id"]), _count(db_session)
        _login(db_session, client, login_as, world["architect_id"])
        resp = _send(client, method, path, body, as_form)
        assert resp.status_code == 403, (
            f"{label}: {method.upper()} {path} answered {resp.status_code}: "
            f"{resp.get_data(as_text=True)[:200]}"
        )
        # delete and import routes also carry an older role gate that answers
        # an architect first; both refusals are plain and change nothing.
        body_text = resp.get_data(as_text=True)
        assert REFUSAL in body_text or "role does not include" in body_text, label
        assert _vendor_row(db_session, world["vendor_id"]) == before, f"{label} changed the row"
        assert _count(db_session) == n, f"{label} changed the row count"


def test_organisation_administrator_gets_the_plain_message_on_every_writer(
        app, db_session, client, login_as, world):
    for label, method, path, body, as_form in _writers(world):
        before, n = _vendor_row(db_session, world["vendor_id"]), _count(db_session)
        _login(db_session, client, login_as, world["tenant_admin_b_id"])
        resp = _send(client, method, path, body, as_form)
        assert resp.status_code == 403, f"{label}: {resp.status_code}"
        assert REFUSAL in resp.get_data(as_text=True), label
        assert _vendor_row(db_session, world["vendor_id"]) == before, label
        assert _count(db_session) == n, label


def test_chat_update_with_approval_on_is_refused_at_queue_time(
        app, db_session, client, login_as, world, monkeypatch):
    monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", True)
    before = _vendor_row(db_session, world["vendor_id"])
    approvals = db_session.execute(text("select count(*) from ai_chat_crud_approvals")).scalar()
    _login(db_session, client, login_as, world["architect_id"])
    resp = client.put(f"{D}/update-vendor/{world['vendor_id']}",
                      json={"description": "queued"}, headers={"X-CSRFToken": "test"})
    assert resp.status_code == 403, resp.get_data(as_text=True)
    assert REFUSAL in resp.get_data(as_text=True)
    db_session.expire_all()
    assert db_session.execute(
        text("select count(*) from ai_chat_crud_approvals")).scalar() == approvals
    assert _vendor_row(db_session, world["vendor_id"]) == before


def test_replay_of_a_queued_vendor_update_is_refused_for_a_non_platform_approver(
        app, db_session, client, login_as, world, monkeypatch):
    """A platform admin queues; an approver who is not a platform admin cannot
    apply it, so the replay refuses again and the row is unchanged."""
    monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", True)
    _login(db_session, client, login_as, world["platform_id"])
    queued = client.put(f"{D}/update-vendor/{world['vendor_id']}",
                        json={"description": "queued"}, headers={"X-CSRFToken": "test"})
    assert queued.status_code == 202, queued.get_data(as_text=True)
    approval_id = queued.get_json()["approval_id"]
    before = _vendor_row(db_session, world["vendor_id"])

    _login(db_session, client, login_as, world["tenant_admin_a_id"])
    resp = client.post(f"/ai-chat/approvals/{approval_id}/approve",
                       headers={"X-CSRFToken": "test"})
    assert _vendor_row(db_session, world["vendor_id"]) == before, resp.get_data(as_text=True)
    assert "queued" not in _vendor_row(db_session, world["vendor_id"])


def test_replay_applied_by_a_platform_approver_records_the_approver(
        app, db_session, client, login_as, world, monkeypatch):
    monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", True)
    _login(db_session, client, login_as, world["platform_id"])
    queued = client.put(f"{D}/update-vendor/{world['vendor_id']}",
                        json={"description": "queued"}, headers={"X-CSRFToken": "test"})
    assert queued.status_code == 202, queued.get_data(as_text=True)
    approval_id = queued.get_json()["approval_id"]

    _login(db_session, client, login_as, world["platform2_id"])
    ok = client.post(f"/ai-chat/approvals/{approval_id}/approve",
                     headers={"X-CSRFToken": "test"})
    assert ok.status_code == 200, ok.get_data(as_text=True)
    db_session.expire_all()
    row = db_session.execute(
        text("select description, last_manual_edit_by from vendor_organizations where id=:i"),
        {"i": world["vendor_id"]}).one()
    assert row.description == "queued"
    assert row.last_manual_edit_by == str(world["platform2_id"])


def _stamp(db_session, vid):
    db_session.expire_all()
    return db_session.execute(
        text("select last_manual_edit_by from vendor_organizations where id=:i"),
        {"i": vid}).scalar()


@pytest.mark.parametrize("path,body,as_form", [
    ("/api/v1/vendors/{v}", {"name": "Renamed"}, False),
    ("/api/vendors/{v}", {"description": "changed"}, False),
    ("/vendor-management/api/vendors/{v}", {"description": "changed"}, False),
    (f"{D}/update-vendor/{{v}}", {"description": "changed"}, False),
])
def test_platform_admin_edit_succeeds_and_records_who(
        app, db_session, client, login_as, world, path, body, as_form):
    assert _stamp(db_session, world["vendor_id"]) is None
    _login(db_session, client, login_as, world["platform_id"])
    resp = _send(client, "put", path.format(v=world["vendor_id"]), body, as_form)
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)
    assert _stamp(db_session, world["vendor_id"]) == str(world["platform_id"])


@pytest.mark.parametrize("path", [
    "/vendors/{v}/activate", "/dashboard/applications/vendors/{v}/activate"])
def test_platform_admin_activation_records_who(
        app, db_session, client, login_as, world, path):
    _login(db_session, client, login_as, world["platform_id"])
    resp = _send(client, "post", path.format(v=world["vendor_id"]),
                 {"contract_value_annual": "1000"}, True)
    assert resp.status_code < 400, resp.get_data(as_text=True)
    assert _stamp(db_session, world["vendor_id"]) == str(world["platform_id"])


def test_platform_admin_can_still_delete(app, db_session, client, login_as, world):
    _login(db_session, client, login_as, world["platform_id"])
    resp = client.delete(f"/api/v1/vendors/{world['vendor_id']}",
                         headers={"X-CSRFToken": "test"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _vendor_row(db_session, world["vendor_id"]) is None


@pytest.mark.parametrize("approval_on", [False, True])
def test_architect_is_refused_chat_create_and_nothing_is_created_or_queued(
        app, db_session, client, login_as, world, monkeypatch, approval_on):
    monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", approval_on)
    name = f"Refused chat {uuid.uuid4().hex[:6]}"
    n = _count(db_session)
    approvals = db_session.execute(text("select count(*) from ai_chat_crud_approvals")).scalar()
    _login(db_session, client, login_as, world["architect_id"])
    resp = client.post(f"{D}/create-vendor", json={"name": name},
                       headers={"X-CSRFToken": "test"})
    assert resp.status_code == 403, resp.get_data(as_text=True)[:300]
    assert REFUSAL in resp.get_data(as_text=True)
    db_session.expire_all()
    assert _count(db_session) == n
    assert db_session.execute(
        text("select count(*) from ai_chat_crud_approvals")).scalar() == approvals


def test_architect_is_refused_the_create_vendor_tool(app, db_session, world):
    from app.modules.ai_chat.tools.executor import ToolExecutor

    name = f"Refused tool {uuid.uuid4().hex[:6]}"
    n = _count(db_session)
    result = ToolExecutor(user_id=world["architect_id"])._tool_create_vendor({"name": name})
    assert result["success"] is False
    assert REFUSAL in result["error"]
    assert _count(db_session) == n


def test_service_create_is_refused_for_an_organisation_user_whatever_the_caller(
        app, db_session, world):
    """The choke point: a replay or any other caller gets the same refusal."""
    from app.modules.ai_chat.services.ai_data_interaction_service import (
        AIDataInteractionService,
    )

    n = _count(db_session)
    result = AIDataInteractionService(user_id=world["architect_id"]).create_vendor(
        {"name": f"Refused svc {uuid.uuid4().hex[:6]}"})
    assert result["success"] is False and result.get("forbidden") is True
    assert _count(db_session) == n


def test_platform_admin_creates_through_the_chat_and_the_tool(
        app, db_session, client, login_as, world):
    from app.modules.ai_chat.tools.executor import ToolExecutor

    chat_name = f"Created chat {uuid.uuid4().hex[:6]}"
    tool_name = f"Created tool {uuid.uuid4().hex[:6]}"
    n = _count(db_session)
    _login(db_session, client, login_as, world["platform_id"])
    resp = client.post(f"{D}/create-vendor", json={"name": chat_name},
                       headers={"X-CSRFToken": "test"})
    assert resp.status_code < 400, resp.get_data(as_text=True)[:300]
    result = ToolExecutor(user_id=world["platform_id"])._tool_create_vendor({"name": tool_name})
    assert result["success"] is True, result
    db_session.expire_all()
    assert _count(db_session) == n + 2


def test_architect_create_is_refused_at_queue_time_and_replay(
        app, db_session, client, login_as, world, monkeypatch):
    """A queued create from a platform admin is refused again at replay when
    the approver is not a platform admin."""
    monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", True)
    from app.modules.ai_chat.services.ai_data_interaction_service import (
        AIDataInteractionService,
    )

    n = _count(db_session)
    forbidden = AIDataInteractionService(user_id=world["tenant_admin_a_id"]).create_vendor(
        {"name": "Replay Probe Vendor"})
    assert forbidden["success"] is False and forbidden.get("forbidden") is True
    assert _count(db_session) == n


@pytest.mark.parametrize("path,as_form", [
    ("/api/v1/vendors/", False),
    ("/api/vendors/", False),
    ("/vendor-management/api/vendors", False),
    ("/vendor-management/create", False),
    ("/applications/vendors/create", True),
    ("/dashboard/applications/vendors/create", True),
])
def test_page_and_api_creates_stay_platform_admin_only(
        app, db_session, client, login_as, world, path, as_form):
    """Pins today's policy: a vendor created here is visible to every
    organisation, so opening these is a separate decision."""
    n = _count(db_session)
    _login(db_session, client, login_as, world["architect_id"])
    resp = _send(client, "post", path, {"name": "Probe Vendor"}, as_form)
    assert resp.status_code == 403, (path, resp.status_code)
    assert _count(db_session) == n


# ---- auto-create-vendors: create-only import, open to organisation users ----

def _vendor_fields(db_session, vid):
    db_session.expire_all()
    return db_session.execute(
        text("select to_json(t)::text from vendor_organizations t where id = :i"), {"i": vid}
    ).scalar()


def test_import_naming_an_existing_vendor_changes_nothing_and_adds_no_row(
        app, db_session, client, login_as, world):
    n = _count(db_session)
    before = _vendor_fields(db_session, world["vendor_id"])
    messy = "  " + world["vendor_name"].upper().replace(" ", "   ") + "  "
    _login(db_session, client, login_as, world["architect_id"])
    resp = client.post("/dashboard/applications/auto-create-vendors",
                       json={"vendors": [messy]}, headers={"X-CSRFToken": "test"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["created"] == 0
    assert _count(db_session) == n
    assert _vendor_fields(db_session, world["vendor_id"]) == before


def test_import_creates_a_new_name_once_with_who_and_how(
        app, db_session, client, login_as, world):
    name = f"Import Vendor {uuid.uuid4().hex[:6]}"
    n = _count(db_session)
    _login(db_session, client, login_as, world["architect_id"])
    resp = client.post("/dashboard/applications/auto-create-vendors",
                       json={"vendors": [name, "  " + name.upper() + " "]},
                       headers={"X-CSRFToken": "test"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["created"] == 1
    db_session.expire_all()
    assert _count(db_session) == n + 1
    row = db_session.execute(
        text("select created_by_id, seeded_by from vendor_organizations where name=:n"),
        {"n": name}).one()
    assert row.created_by_id == world["architect_id"]
    assert row.seeded_by == "import"
