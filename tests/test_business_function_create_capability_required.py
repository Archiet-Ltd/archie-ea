"""BusinessFunction.capability_id is NOT NULL on the model
(app/models/business_capabilities.py), but the generic element-create route
(``POST /architecture/<layer>/<element_type>/new`` in
app/modules/architecture/routes/archimate_crud/routes.py) never collected it:
it is not one of the unconditional name/description fields, not in
``_set_model_fields``'s legacy field_mappings table, and not a configured
field in services/field_configs.py's ``BusinessFunction`` entry (``domain``
only). Every submission reached ``db.session.commit()`` and failed the
database's own NOT NULL constraint — logged as an ERROR with a full
traceback for an entirely predictable validation failure, instead of a clean
400.

This pins the fix: a request missing ``capability_id`` is rejected before
any insert is attempted, with a clear message; a request that supplies a
valid ``capability_id`` still succeeds and persists it.
"""

from __future__ import annotations

import uuid

import pytest


def _make_user(db_session, org_id, label="creator"):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


def _login(client, user_id):
    """Fake a Flask-Login session and clear any request-context caching.

    Mirrors tests/test_archimate_typed_field_create.py::_login (itself
    mirroring tests/test_ba_tenant_and_authz.py::_login) — necessary here
    because db_session holds an app context open for the whole test, so
    Flask-Login's and the tenant middleware's ``g``-cached state must be
    cleared by hand rather than relying on a fresh request context.
    """
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


def _unified_capability(db_session, org):
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(
        name=f"Capability {uuid.uuid4().hex[:8]}",
        code=f"CAP-{uuid.uuid4().hex[:8]}",
        level=1,
        scope="tenant",
        organization_id=org.id,
    )
    db_session.add(cap)
    db_session.flush()
    return cap


@pytest.fixture
def client(app):
    return app.test_client()


def test_create_business_function_without_capability_id_returns_400(
    app, db_session, client, make_org
):
    """The bug: submitting a BusinessFunction with no capability_id used to
    reach the database's NOT NULL constraint (an IntegrityError, caught
    generically and logged as an ERROR with a traceback) instead of a clean,
    validated 400 naming the missing field."""
    from app.models.business_capabilities import BusinessFunction

    org = make_org("bf-create-missing-cap")
    user = _make_user(db_session, org.id, "BfCreator")
    _login(client, user.id)

    before_count = BusinessFunction.query.filter_by(organization_id=org.id).count()

    resp = client.post(
        "/architecture/business/BusinessFunction/new",
        json={
            "name": "Process Customer Refunds",
            "description": "Handles refund requests end to end",
        },
    )

    assert resp.status_code == 400, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["success"] is False
    assert "capability" in payload["error"].lower(), (
        f"error message should clearly name the missing field, got: {payload['error']!r}"
    )

    after_count = BusinessFunction.query.filter_by(organization_id=org.id).count()
    assert after_count == before_count, (
        "a rejected request must not leave a partially-created row behind"
    )


def test_create_business_function_with_capability_id_succeeds(
    app, db_session, client, make_org
):
    """With a valid capability_id supplied, creation still succeeds and the
    value is actually persisted — the fix must not just reject everything."""
    from app.models.business_capabilities import BusinessFunction

    org = make_org("bf-create-with-cap")
    user = _make_user(db_session, org.id, "BfCreator2")
    _login(client, user.id)
    cap = _unified_capability(db_session, org)

    resp = client.post(
        "/architecture/business/BusinessFunction/new",
        json={
            "name": "Process Customer Refunds",
            "description": "Handles refund requests end to end",
            "capability_id": cap.id,
        },
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["success"] is True

    created = db_session.get(BusinessFunction, payload["id"])
    assert created is not None
    assert created.capability_id == cap.id


def test_create_business_function_with_non_numeric_capability_id_returns_400(
    app, db_session, client, make_org
):
    """A garbage (non-integer) capability_id must be treated the same as a
    missing one -- a clean 400, not an attempt to insert it and let the
    database reject the type."""
    org = make_org("bf-create-bad-cap")
    user = _make_user(db_session, org.id, "BfCreator3")
    _login(client, user.id)

    resp = client.post(
        "/architecture/business/BusinessFunction/new",
        json={
            "name": "Process Customer Refunds",
            "capability_id": "not-a-number",
        },
    )

    assert resp.status_code == 400, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["success"] is False
    assert "capability" in payload["error"].lower()
