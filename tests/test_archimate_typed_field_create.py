"""The generic ArchiMate element create modal previously posted only
element_type/name/description, even though ``services/field_configs.py``
defines typed fields (e.g. BusinessActor.actor_type) for many element types.
Those configs were passed into the dashboard template context but never
referenced by it (dead data), and the create POST handler never received the
typed values in the first place.

This closes the gap end to end:
  - dashboard.html renders typed fields for the selected type from
    ``window.__APP_CONFIG__.fieldConfigs`` (services/field_configs.py,
    exposed by the ``dashboard``/``list_elements``/``create_element`` routes).
  - dashboard.js includes those typed values in the create/update POST body.
  - ``create_element`` (routes.py) already called ``_set_model_fields`` for
    typed fields on create — the same helper PATCH-style updates use — it
    was just never handed anything to apply. ``_set_model_fields`` now also
    draws its allowed-field list from ``get_element_field_names(element_type)``
    (field_configs.py) in addition to its legacy hard-coded table, so any
    field the create modal can render is also a field the server will persist.

Covers:
  1. Creating a BusinessActor with a typed field (``actor_type``) persists it.
  2. Creating a type with no field_configs.py entry (Contract) still works
     with just name + description — unchanged behaviour.
  3. A bogus/unknown field name in the create payload is ignored, not a 500 —
     ``_set_model_fields``'s ``hasattr(element, field)`` guard already
     enforces this; this pins that create's payload doesn't bypass it.

Tenant note: BusinessActor and Contract both inherit ``TenantMixin``, whose
``organization_id`` column is NOT NULL. It is auto-set only when
``g.current_org_id`` is populated, which the ``tenant_context`` middleware's
``before_request`` hook does *unconditionally* from ``current_user`` on every
request (setting it back to None for an unauthenticated caller) — so merely
flipping ``LOGIN_DISABLED`` (which skips the ``@login_required`` check but
does not log anyone in) is not enough here: the request would still 500 on
the NOT NULL constraint. These tests instead fake a real Flask-Login session
for a real User row pinned to a real Organization, exactly the pattern
``tests/test_ba_tenant_and_authz.py::_login`` uses for the same reason.
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

    Setting the session cookie alone is the standard Flask-Login test
    pattern but is not sufficient when the app/request context is reused
    across calls in the same test (as it can be here, since ``db_session``
    holds an app context open for the whole test) — Flask-Login and the
    tenant middleware both cache resolved state on ``g``. See the detailed
    explanation in ``tests/test_ba_tenant_and_authz.py::_login``, which this
    mirrors.
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


@pytest.fixture
def client(app):
    return app.test_client()


def test_create_business_process_persists_config_only_field(
    app, db_session, client, make_org
):
    """The discriminating case: a field that exists ONLY in field_configs.py.

    BusinessProcess has a config (process_type, apqc_code) but was never in
    _set_model_fields' legacy hard-coded field_mappings table — so this fails
    on the pre-change tree and passes only once the allowed-field set is the
    union of the legacy table and the type's config. BusinessActor cannot
    prove that: its actor_type was already in the legacy table, so a
    BusinessActor-based test passes on both sides of the change (verified —
    the first version of this file did exactly that and went RED nowhere).
    """
    from app.models.process_data import BusinessProcess

    org = make_org("typed-create-bp")
    user = _make_user(db_session, org.id, "BpCreator")
    _login(client, user.id)

    resp = client.post(
        "/architecture/business/BusinessProcess/new",
        json={
            "name": "Handle Claim",
            "description": "End-to-end claim handling",
            "process_type": "core",
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["success"] is True

    created = db_session.get(BusinessProcess, payload["id"])
    assert created is not None
    assert created.process_type == "core", (
        "process_type has a field_configs.py config and a matching model "
        "column but was not persisted — the create path is not consulting "
        "the typed field configs"
    )


def test_create_business_actor_persists_typed_field(app, db_session, client, make_org):
    """Regression pin for the LEGACY path: actor_type was already in
    _set_model_fields' hard-coded field_mappings, so this passes before and
    after the change — it exists to ensure the union logic did not break the
    fields the old table served."""
    from app.models.business_layer import BusinessActor

    org = make_org("typed-create")
    user = _make_user(db_session, org.id, "BaCreator")
    _login(client, user.id)

    resp = client.post(
        "/architecture/business/BusinessActor/new",
        json={
            "name": "Claims Processing Team",
            "description": "Handles inbound claims",
            "actor_type": "Team",
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["success"] is True

    created = db_session.get(BusinessActor, payload["id"])
    assert created is not None
    assert created.name == "Claims Processing Team"
    assert created.actor_type == "Team", (
        "actor_type was posted with a real field_configs.py config for "
        "BusinessActor and a matching column on the model; it must be "
        "persisted by _set_model_fields, not silently dropped"
    )


def test_create_element_type_with_no_config_still_works(app, db_session, client, make_org):
    """Contract has no field_configs.py entry — the create path must keep
    behaving exactly as it did before typed fields existed: name +
    description only, no typed fields expected or required."""
    from app.models.archimate_business import Contract
    from app.modules.architecture.routes.archimate_crud.services.field_configs import (
        get_element_config,
    )

    assert get_element_config("Contract") is None, (
        "this test assumes Contract has no typed field config; if one was "
        "added, pick a different unconfigured type here"
    )

    org = make_org("typed-create-nocfg")
    user = _make_user(db_session, org.id, "ContractCreator")
    _login(client, user.id)

    resp = client.post(
        "/architecture/business/Contract/new",
        json={"name": "Vendor Master Agreement", "description": "Supplier SLA"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert payload["success"] is True

    created = db_session.get(Contract, payload["id"])
    assert created is not None
    assert created.name == "Vendor Master Agreement"
    assert created.description == "Supplier SLA"


def test_create_ignores_a_bogus_field_name_instead_of_500ing(app, db_session, client, make_org):
    """Asserts a refusal: an unknown field answers 400 naming it, and no record is created."""
    from app.models.business_layer import BusinessActor

    org = make_org("typed-create-bogus")
    user = _make_user(db_session, org.id, "BogusCreator")
    _login(client, user.id)

    resp = client.post(
        "/architecture/business/BusinessActor/new",
        json={
            "name": "Ops Team Bogus",
            "description": "",
            "actor_type": "Team",
            "this_field_does_not_exist_on_any_model": "haha",
        },
    )
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert "this_field_does_not_exist_on_any_model" in resp.get_json()["error"]
    assert BusinessActor.query.filter_by(name="Ops Team Bogus").count() == 0

