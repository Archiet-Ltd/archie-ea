"""Tests for the entity matching accept route.

Covers:
- (a) a new entity is saved once in organisation A
- (b) the same name again gives 409 naming the existing element
- (c) organisation B cannot see or reuse A's element, and B's 409 never names A's element
- (d) a read-only member gets 403 and nothing is saved
- (e) an unknown type is refused
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _login(client, user_id):
    from tests._session_test_helpers import mint_test_sid

    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid

    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


def _make_user(db_session, make_org, org_role, label):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    org = make_org(f"entmatch-{label}")
    user = User(
        email=f"entmatch-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name=label.capitalize(),
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role="architect",
        role=Role.query.filter_by(name="User").first(),
    )
    user.password = "Test!12345"
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, org_role)
    db_session.flush()
    return user, org


def _element_count_raw(db_session):
    from app import db

    return db.session.execute(db.text("SELECT count(*) FROM archimate_elements")).scalar()


def test_accept_new_entity_saves_once_in_org_a(app, db_session, make_org):
    """(a) A new entity is saved once in organisation A."""
    user_a, org_a = _make_user(db_session, make_org, "org_admin", "owner-a")
    client = app.test_client()
    _login(client, user_a.id)

    # Payload for a new entity (not matched)
    payload = {
        "entity": {
            "name": "Payment Gateway Service",
            "type": "ApplicationComponent",
            "recommendation": "New application component detected",
        },
        "action": "accept",
    }

    before = _element_count_raw(db_session)
    resp = client.post("/ai-chat/entity-matching/accept", json=payload)
    after = _element_count_raw(db_session)

    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    body = resp.get_json()
    assert body["success"] is True
    assert "element_id" in body
    assert after == before + 1

    # Verify the element exists and has correct properties
    from app.models.archimate_core import ArchiMateElement

    element = db_session.get(ArchiMateElement, body["element_id"])
    assert element is not None
    assert element.name == "Payment Gateway Service"
    assert element.type == "ApplicationComponent"
    assert element.layer == "Application"
    assert element.organization_id == org_a.id
    # Provenance should record entity matching origin
    props = element.custom_properties or {}
    assert props.get("source_model") == "entity_matching"
    assert props.get("origin") == "entity_matching"


def test_accept_same_name_again_gives_409_naming_existing(app, db_session, make_org):
    """(b) The same name again gives 409 naming the existing element."""
    user_a, org_a = _make_user(db_session, make_org, "org_admin", "owner-b")
    client = app.test_client()
    _login(client, user_a.id)

    payload = {
        "entity": {
            "name": "Analytics Engine",
            "type": "ApplicationComponent",
            "recommendation": "New application component detected",
        },
        "action": "accept",
    }

    # First accept
    resp1 = client.post("/ai-chat/entity-matching/accept", json=payload)
    assert resp1.status_code == 200
    body1 = resp1.get_json()
    first_id = body1["element_id"]

    # Second accept with same name
    resp2 = client.post("/ai-chat/entity-matching/accept", json=payload)
    assert resp2.status_code == 409, resp2.get_data(as_text=True)[:500]
    body2 = resp2.get_json()
    assert body2["success"] is False
    assert body2["code"] == "DUPLICATE_NAME"
    assert "duplicate_of" in body2
    assert body2["duplicate_of"]["id"] == first_id
    assert body2["duplicate_of"]["name"] == "Analytics Engine"

    # Verify only one element was created
    from app.models.archimate_core import ArchiMateElement

    count = db_session.query(ArchiMateElement).filter_by(name="Analytics Engine", organization_id=org_a.id).count()
    assert count == 1


def test_org_b_cannot_see_or_reuse_org_a_element(app, db_session, make_org, tenant_ctx):
    """(c) Organisation B cannot see or reuse A's element - B creates its own element with same name."""
    user_a, org_a = _make_user(db_session, make_org, "org_admin", "owner-c-a")
    user_b, org_b = _make_user(db_session, make_org, "org_admin", "owner-c-b")

    client_a = app.test_client()
    _login(client_a, user_a.id)

    # Org A creates an element
    payload = {
        "entity": {
            "name": "Shared Name Component",
            "type": "ApplicationComponent",
            "recommendation": "New application component detected",
        },
        "action": "accept",
    }
    resp_a = client_a.post("/ai-chat/entity-matching/accept", json=payload)
    assert resp_a.status_code == 200
    body_a = resp_a.get_json()
    a_element_id = body_a["element_id"]

    # Org B tries to accept same name - should succeed (tenant-scoped duplicate check)
    client_b = app.test_client()
    _login(client_b, user_b.id)

    resp_b = client_b.post("/ai-chat/entity-matching/accept", json=payload)
    assert resp_b.status_code == 200, resp_b.get_data(as_text=True)[:500]
    body_b = resp_b.get_json()
    assert body_b["success"] is True
    b_element_id = body_b["element_id"]
    # B's element must be different from A's
    assert b_element_id != a_element_id

    # Verify org A still has its element, org B has its own
    # Use tenant_ctx to avoid cross-contamination from g.current_org_id set by test client requests
    from app.models.archimate_core import ArchiMateElement

    with tenant_ctx(org_a.id):
        a_count = db_session.query(ArchiMateElement).filter_by(name="Shared Name Component", organization_id=org_a.id).count()
        assert a_count == 1
        a_elem = db_session.query(ArchiMateElement).filter_by(name="Shared Name Component", organization_id=org_a.id).first()

    with tenant_ctx(org_b.id):
        b_count = db_session.query(ArchiMateElement).filter_by(name="Shared Name Component", organization_id=org_b.id).count()
        assert b_count == 1
        b_elem = db_session.query(ArchiMateElement).filter_by(name="Shared Name Component", organization_id=org_b.id).first()

    # B's own repeat is refused naming B's element, never A's
    again = client_b.post("/ai-chat/entity-matching/accept", json=payload)
    assert again.status_code == 409
    assert again.get_json()["duplicate_of"]["id"] == b_element_id
    assert again.get_json()["duplicate_of"]["id"] != a_element_id

    # B cannot open A's element through the matched path
    other = client_b.post("/ai-chat/entity-matching/accept", json={"entity": {"entity_id": a_element_id}})
    assert other.status_code == 404
    assert "Shared Name Component" not in other.get_data(as_text=True)

    # Verify they are different elements
    assert a_elem.id != b_elem.id
    assert a_elem.organization_id == org_a.id
    assert b_elem.organization_id == org_b.id


def test_read_only_member_gets_403_and_nothing_saved(app, db_session, make_org):
    """(d) A read-only member gets 403 and nothing is saved."""
    user_viewer, org = _make_user(db_session, make_org, "viewer", "viewer-d")
    client = app.test_client()
    _login(client, user_viewer.id)

    payload = {
        "entity": {
            "name": "Viewer Attempt Component",
            "type": "ApplicationComponent",
            "recommendation": "New application component detected",
        },
        "action": "accept",
    }

    before = _element_count_raw(db_session)
    resp = client.post("/ai-chat/entity-matching/accept", json=payload)
    after = _element_count_raw(db_session)

    assert resp.status_code == 403, resp.get_data(as_text=True)[:500]
    body = resp.get_json()
    assert body["success"] is False
    assert body["code"] == "PERMISSION_DENIED"
    assert after == before  # No element created


def test_unknown_type_is_refused(app, db_session, make_org):
    """(e) An unknown type is refused with a plain sentence."""
    user_a, org_a = _make_user(db_session, make_org, "org_admin", "owner-e")
    client = app.test_client()
    _login(client, user_a.id)

    payload = {
        "entity": {
            "name": "Unknown Type Component",
            "type": "NotARealArchiMateType",
            "recommendation": "New entity detected",
        },
        "action": "accept",
    }

    before = _element_count_raw(db_session)
    resp = client.post("/ai-chat/entity-matching/accept", json=payload)
    after = _element_count_raw(db_session)

    assert resp.status_code == 400, resp.get_data(as_text=True)[:500]
    body = resp.get_json()
    assert body["success"] is False
    assert "unknown" in body.get("error", "").lower() or "not a valid" in body.get("error", "").lower()
    assert after == before  # No element created


def test_accept_matched_entity_opens_existing_no_new_element(app, db_session, make_org):
    """Accept on a matched entity creates nothing and returns the existing element."""
    from app.models.archimate_core import ArchiMateElement

    user_a, org_a = _make_user(db_session, make_org, "org_admin", "owner-f")
    client = app.test_client()
    _login(client, user_a.id)

    # Pre-create an element in the org
    existing = ArchiMateElement(
        organization_id=org_a.id,
        name="Existing Payment Service",
        type="ApplicationComponent",
        layer="Application",
        description="Pre-existing element",
        scope="enterprise",
    )
    db_session.add(existing)
    db_session.flush()
    existing_id = existing.id

    # Payload for a matched entity (has entity_id)
    payload = {
        "entity": {
            "entity_id": existing_id,
            "entity_name": "Existing Payment Service",
            "entity_type": "ApplicationComponent",
        },
        "action": "accept",
    }

    before = _element_count_raw(db_session)
    resp = client.post("/ai-chat/entity-matching/accept", json=payload)
    after = _element_count_raw(db_session)

    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    body = resp.get_json()
    assert body["success"] is True
    assert body["element_id"] == existing_id
    assert body.get("matched") is True
    assert after == before  # No new element created