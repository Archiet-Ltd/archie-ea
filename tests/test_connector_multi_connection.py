"""One connector type can hold several connections in one organisation, each with its own access.

A made-up type stands in for the real one, added to the framework's constants and to the permitted
set with monkeypatch, so these tests do not need any real multi-connection connector to exist.
Values are made up; the tests assert on presence and slot names only.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

KIND = "demo_multi"


@pytest.fixture(autouse=True)
def _demo_kind(monkeypatch):
    from app.modules.intelligence.services import connector_allowlist
    from app.services import connector_framework as cf

    monkeypatch.setattr(cf, "MULTI_CONNECTION_CREDENTIAL_TYPES", frozenset({KIND}), raising=False)
    monkeypatch.setattr(cf, "OWN_PAGE_CONNECTOR_TYPES", frozenset({KIND}), raising=False)
    monkeypatch.setattr(
        connector_allowlist,
        "PERMITTED_CONNECTOR_TYPES",
        connector_allowlist.PERMITTED_CONNECTOR_TYPES | {KIND},
    )


def _connection(org, slot, name):
    from app.models.connector_config import ConnectorConfig
    from app import db

    cfg = ConnectorConfig(
        organization_id=org.id, connector_type=KIND, name=name, config={"vault_slot": slot}
    )
    db.session.add(cfg)
    db.session.flush()
    return cfg


def _store(org, slot, marker, staged_for=None):
    from app.modules.codegen.services.credential_vault import OrgCredentialVault

    vault = OrgCredentialVault()
    value = json.dumps({"access_token": marker})
    if staged_for is None:
        vault.store(org.id, KIND, f"{KIND}:{slot}", value)
    else:
        vault.store_staged(org.id, KIND, f"{KIND}:{slot}", value, staged_for)


def _credential_types(org):
    from app.models.connector_config import OrgConnectorCredential

    rows = OrgConnectorCredential.query.filter_by(organization_id=org.id, connector_type=KIND).all()
    return sorted(r.credential_type for r in rows)


def _delete(org, cfg):
    from app.services.connector_framework import CONNECTOR_ACTION_DELETE, apply_connector_change

    apply_connector_change(
        org_id=org.id,
        action=CONNECTOR_ACTION_DELETE,
        payload={"connector_type": KIND, "connector_id": cfg.id},
        delegated=True,
    )


def test_deleting_one_connection_leaves_the_other_connections_access(db_session, make_org):
    org = make_org("multi")
    first = _connection(org, "aaaa1111aaaa1111", "First server")
    second = _connection(org, "bbbb2222bbbb2222", "Second server")
    _store(org, "aaaa1111aaaa1111", "made-up-one")
    _store(org, "bbbb2222bbbb2222", "made-up-two")
    _store(org, "aaaa1111aaaa1111", "made-up-staged-one", staged_for=7)
    _store(org, "bbbb2222bbbb2222", "made-up-staged-two", staged_for=8)

    _delete(org, first)

    assert _credential_types(org) == [
        f"{KIND}:bbbb2222bbbb2222",
        f"pending:8:{KIND}:bbbb2222bbbb2222",
    ]
    assert second.id is not None


def test_two_organisations_never_remove_each_others_access(db_session, make_org):
    org_a = make_org("multi-a")
    org_b = make_org("multi-b")
    slot = "cccc3333cccc3333"  # the same slot name in both organisations
    cfg_a = _connection(org_a, slot, "A server")
    _connection(org_b, slot, "B server")
    _store(org_a, slot, "made-up-a")
    _store(org_b, slot, "made-up-b")

    _delete(org_a, cfg_a)

    assert _credential_types(org_a) == []
    assert _credential_types(org_b) == [f"{KIND}:{slot}"]


def test_a_connection_shows_only_its_own_access(db_session, make_org):
    from app.services.connector_framework import _connector_view

    org = make_org("multi-view")
    with_access = _connection(org, "dddd4444dddd4444", "Has access")
    without_access = _connection(org, "eeee5555eeee5555", "No access")
    _store(org, "dddd4444dddd4444", "made-up-view")

    assert _connector_view(with_access, org.id)["credential_stored"] is True
    assert _connector_view(with_access, org.id)["credential_masked"] == "******"
    assert _connector_view(without_access, org.id)["credential_stored"] is False
    assert _connector_view(without_access, org.id)["credential_masked"] is None


def test_the_field_map_reads_the_connections_own_access(db_session, make_org):
    from app.services.connector_framework import _credential_field_map

    org = make_org("multi-map")
    _connection(org, "ffff6666ffff6666", "One")
    _connection(org, "1111aaaa1111aaaa", "Two")
    _store(org, "ffff6666ffff6666", "made-up-map-one")
    _store(org, "1111aaaa1111aaaa", "made-up-map-two")

    one = _credential_field_map(KIND, org.id, {"vault_slot": "ffff6666ffff6666"})
    two = _credential_field_map(KIND, org.id, {"vault_slot": "1111aaaa1111aaaa"})
    none = _credential_field_map(KIND, org.id, {"vault_slot": "9999999999999999"})
    no_slot = _credential_field_map(KIND, org.id, {})

    assert one == {"access_token": "made-up-map-one"}
    assert two == {"access_token": "made-up-map-two"}
    assert none == {}
    assert no_slot == {}


@pytest.mark.parametrize("action", ["update", "delete"])
def test_update_and_delete_need_the_connection_id(db_session, make_org, action):
    from app.services.connector_framework import ConnectorWriteRefused, apply_connector_change

    org = make_org("multi-id")
    _connection(org, "2222bbbb2222bbbb", "Only one")
    _store(org, "2222bbbb2222bbbb", "made-up-id")

    with pytest.raises(ConnectorWriteRefused):
        apply_connector_change(
            org_id=org.id,
            action=action,
            payload={"connector_type": KIND, "name": "New name"},
            delegated=True,
        )

    assert _credential_types(org) == [f"{KIND}:2222bbbb2222bbbb"]


def test_a_single_connection_type_still_loses_all_its_access_on_delete(db_session, make_org):
    from app.models.connector_config import ConnectorConfig
    from app.modules.codegen.services.credential_vault import OrgCredentialVault
    from app.services.connector_framework import CONNECTOR_ACTION_DELETE, apply_connector_change
    from app.models.connector_config import OrgConnectorCredential
    from app import db

    org = make_org("single")
    cfg = ConnectorConfig(
        organization_id=org.id, connector_type="jira", name="Jira", config={"instance_url": "https://x.invalid"}
    )
    db.session.add(cfg)
    db.session.flush()
    vault = OrgCredentialVault()
    vault.store(org.id, "jira", "api_token", "made-up-token")
    vault.store(org.id, "jira", "credentials", json.dumps({"credential": "made-up"}))

    apply_connector_change(
        org_id=org.id,
        action=CONNECTOR_ACTION_DELETE,
        payload={"connector_type": "jira", "connector_id": cfg.id},
        delegated=True,
    )

    assert OrgConnectorCredential.query.filter_by(organization_id=org.id, connector_type="jira").count() == 0
