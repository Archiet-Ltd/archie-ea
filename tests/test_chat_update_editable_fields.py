"""The chat's update functions change only the fields a person may edit.

For an application, a capability, a vendor and a capability mapping (and the
two bulk routes that call them) a request that names any attribute outside the
edit list is refused with 400 naming the field(s), and nothing is written.
Allowed fields still update. Two organisations in every test.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from tests.test_chat_matrix_write_gate import _user

pytestmark = pytest.mark.usefixtures("db_session")

D = "/ai-chat/data"


@pytest.fixture(autouse=True)
def _csrf_off(monkeypatch, app):
    from app.utils import csrf_helper

    monkeypatch.setattr(csrf_helper, "validate_csrf", lambda *_a, **_k: None)
    # Exercise the direct write path; the approval queue replays the same
    # service functions when an approver accepts.
    monkeypatch.setitem(app.config, "REQUIRE_AI_APPROVAL", False)


def _records(db_session, org):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_application_capability_mapping import (
        UnifiedApplicationCapabilityMapping,
    )
    from app.models.unified_capability import UnifiedCapability
    from app.models.vendor.vendor_organization import VendorOrganization

    tag = uuid.uuid4().hex[:6]
    app_row = ApplicationComponent(
        name=f"MA app {tag}", description="original", business_domain="IT",
        organization_id=org.id,
    )
    bcap = BusinessCapability(name=f"MA bcap {tag}", description="original",
                              organization_id=org.id)
    ucap = UnifiedCapability(name=f"MA ucap {tag}", organization_id=org.id)
    vendor = VendorOrganization(name=f"MA vendor {tag}", description="original")
    db_session.add_all([app_row, bcap, ucap, vendor])
    db_session.flush()
    mapping = UnifiedApplicationCapabilityMapping(
        unified_capability_id=ucap.id, application_component_id=app_row.id,
        support_level="partial", coverage_percentage=10,
    )
    db_session.add(mapping)
    db_session.flush()
    return {"app": app_row.id, "bcap": bcap.id, "ucap": ucap.id,
            "vendor": vendor.id, "mapping": mapping.id, "org": org.id}


@pytest.fixture
def world(db_session, make_org):
    a, b = make_org("ma-a"), make_org("ma-b")
    w = {
        "a": a, "b": b,
        "architect": _user(db_session, a, "architect", "architect"),
        "recs_a": _records(db_session, a),
        "recs_b": _records(db_session, b),
    }
    db_session.commit()
    return w


TABLES = {
    "app": "application_components",
    "bcap": "business_capability",
    "vendor": "vendor_organizations",
    "mapping": "unified_application_capability_mapping",
}


def _row(db_session, kind, rid):
    # Raw SQL so the reading user's organisation cannot hide a row.
    db_session.expire_all()
    return db_session.execute(
        text(f"select to_json(t)::text from {TABLES[kind]} t where id = :i"), {"i": rid}
    ).scalar()


def _send(client, login_as, user, kind, rid, body):
    login_as(client, user)
    urls = {
        "app": f"{D}/update-application/{rid}",
        "bcap": f"{D}/update-capability/{rid}",
        "vendor": f"{D}/update-vendor/{rid}",
        "mapping": f"{D}/update-capability-mapping/{rid}",
    }
    return client.put(urls[kind], json=body, headers={"X-CSRFToken": "test"})


def _forbidden(kind, w):
    """(field name, value) pairs that no edit form allows."""
    b = w["recs_b"]
    if kind == "app":
        return [("organization_id", b["org"]), ("id", 987654),
                ("created_at", "2001-01-01T00:00:00"),
                ("archimate_element_id", 424242), ("deleted_by", 1), ("lock_version", 99)]
    if kind == "bcap":
        return [("organization_id", b["org"]), ("id", 987654),
                ("parent_capability_id", b["bcap"]),
                ("canonical_capability_id", b["bcap"]), ("archimate_element_id", 424242),
                ("created_at", "2001-01-01T00:00:00")]
    if kind == "vendor":
        return [("id", 987654), ("created_by_id", 1), ("parent_vendor_id", b["vendor"]),
                ("is_seed_data", True), ("created_at", "2001-01-01T00:00:00")]
    return [("id", 987654), ("application_component_id", b["app"]),
            ("unified_capability_id", b["ucap"]), ("created_at", "2001-01-01T00:00:00")]


ALLOWED = {
    "app": {"description": "changed", "business_domain": "Finance"},
    "bcap": {"description": "changed"},
    "vendor": {"description": "changed"},
    "mapping": {"coverage_percentage": 77, "notes": "changed"},
}
KINDS = list(ALLOWED)


@pytest.mark.parametrize("kind", KINDS)
def test_forbidden_fields_are_refused_and_nothing_is_written(
        client, login_as, db_session, world, kind):
    rid = world["recs_a"][kind]
    before = _row(db_session, kind, rid)
    other_before = _row(db_session, kind, world["recs_b"][kind])
    for field, value in _forbidden(kind, world):
        resp = _send(client, login_as, world["architect"], kind, rid, {field: value})
        assert resp.status_code == 400, (field, resp.status_code, resp.get_data(as_text=True)[:200])
        assert field in resp.get_data(as_text=True), field
        assert _row(db_session, kind, rid) == before, field
        assert _row(db_session, kind, world["recs_b"][kind]) == other_before, field


@pytest.mark.parametrize("kind", KINDS)
def test_forbidden_field_beside_an_allowed_one_writes_nothing(
        client, login_as, db_session, world, kind):
    rid = world["recs_a"][kind]
    before = _row(db_session, kind, rid)
    field, value = _forbidden(kind, world)[0]
    body = dict(ALLOWED[kind])
    body[field] = value
    resp = _send(client, login_as, world["architect"], kind, rid, body)
    assert resp.status_code == 400
    assert field in resp.get_data(as_text=True)
    assert _row(db_session, kind, rid) == before


@pytest.mark.parametrize("kind", KINDS)
def test_unknown_field_is_refused_with_its_name(client, login_as, db_session, world, kind):
    rid = world["recs_a"][kind]
    before = _row(db_session, kind, rid)
    resp = _send(client, login_as, world["architect"], kind, rid, {"no_such_attribute_zz": 1})
    assert resp.status_code == 400
    assert "no_such_attribute_zz" in resp.get_data(as_text=True)
    assert _row(db_session, kind, rid) == before


@pytest.mark.parametrize("kind", KINDS)
def test_allowed_fields_still_update(client, login_as, db_session, world, kind):
    rid = world["recs_a"][kind]
    resp = _send(client, login_as, world["architect"], kind, rid, ALLOWED[kind])
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    after = json.loads(_row(db_session, kind, rid))
    for field, value in ALLOWED[kind].items():
        assert after[field] == value, field


def _bulk(client, login_as, world, path, id_key, rid, data):
    login_as(client, world["architect"])
    return client.post(
        f"{D}/{path}",
        json={"updates": [{id_key: rid, "update_data": data}]},
        headers={"X-CSRFToken": "test"})


def test_bulk_application_update_refuses_a_forbidden_field(client, login_as, db_session, world):
    rid = world["recs_a"]["app"]
    before = _row(db_session, "app", rid)
    resp = _bulk(client, login_as, world, "bulk-update-applications", "application_id", rid,
                 {"organization_id": world["recs_b"]["org"]})
    assert "organization_id" in resp.get_data(as_text=True)
    assert resp.get_json()["summary"]["success"] == 0
    assert _row(db_session, "app", rid) == before


def test_bulk_application_update_allowed_field_still_updates(client, login_as, db_session, world):
    rid = world["recs_a"]["app"]
    resp = _bulk(client, login_as, world, "bulk-update-applications", "application_id", rid,
                 {"description": "bulk changed"})
    assert resp.get_json()["summary"]["success"] == 1
    assert json.loads(_row(db_session, "app", rid))["description"] == "bulk changed"


def test_bulk_capability_update_refuses_a_forbidden_field(client, login_as, db_session, world):
    rid = world["recs_a"]["bcap"]
    before = _row(db_session, "bcap", rid)
    resp = _bulk(client, login_as, world, "bulk-update-capabilities", "capability_id", rid,
                 {"organization_id": world["recs_b"]["org"]})
    assert "organization_id" in resp.get_data(as_text=True)
    assert resp.get_json()["summary"]["success"] == 0
    assert _row(db_session, "bcap", rid) == before


def test_bulk_capability_update_allowed_field_still_updates(client, login_as, db_session, world):
    rid = world["recs_a"]["bcap"]
    resp = _bulk(client, login_as, world, "bulk-update-capabilities", "capability_id", rid,
                 {"description": "bulk changed"})
    assert resp.get_json()["summary"]["success"] == 1
    assert json.loads(_row(db_session, "bcap", rid))["description"] == "bulk changed"


def test_application_edit_list_has_no_key_or_ownership_column():
    from sqlalchemy.inspection import inspect as sa_inspect

    from app.models.application_portfolio import ApplicationComponent
    from app.modules.ai_chat.services.ai_data_interaction_service import (
        application_editable_fields,
    )

    allowed = application_editable_fields()
    assert {"name", "description", "business_domain", "lifecycle_status"} <= allowed
    cols = {c.key: c for c in sa_inspect(ApplicationComponent).columns}
    for name in allowed:
        assert name in cols
        assert not cols[name].foreign_keys and not cols[name].primary_key, name
    for name in ("organization_id", "id", "created_at", "archimate_element_id",
                 "vendor_product_id", "lock_version", "deleted_by"):
        assert name not in allowed, name
