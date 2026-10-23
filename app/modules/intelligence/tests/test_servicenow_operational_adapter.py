"""ServiceNow as the first operational source adapter: real adapter, HTTP faked."""

from __future__ import annotations

from datetime import datetime

import pytest
import requests


def _element(db_session, org_id, name):
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(
        name=name, type="ApplicationComponent", layer="Application", organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _incident(sys_id, ci, number="INC1", **extra):
    return {
        "sys_id": sys_id,
        "number": number,
        "short_description": f"issue {sys_id}",
        "state": "2",
        "severity": "1",
        "cmdb_ci": {"link": "https://x/api/now/table/cmdb_ci/" + ci, "value": ci} if ci else "",
        "opened_at": "2026-10-01 08:30:00",
        "resolved_at": "",
        "closed_at": "",
        "sys_updated_on": "2026-10-02 09:00:00",
        **extra,
    }


@pytest.fixture
def world(db_session, make_org, monkeypatch, app):
    from types import SimpleNamespace

    import app.utils.ssrf_guard as guard

    from app.models.connector_config import ConnectorConfig
    from app.modules.codegen.services import credential_vault
    from app.modules.intelligence.services.crosswalk_service import CrosswalkService

    # Resolve every name to a public address (the sandbox has no DNS); the guard itself still runs.
    monkeypatch.setitem(app.config, "ALLOW_INTERNAL_WEBHOOK_TARGETS", False)
    monkeypatch.setattr(
        guard.socket, "getaddrinfo", lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", port))]
    )
    monkeypatch.setattr(guard, "_local_interface_addresses", lambda: set())

    org_a, org_b = make_org("sn-a"), make_org("sn-b")
    for org, host in ((org_a, "a"), (org_b, "b")):
        db_session.add(
            ConnectorConfig(
                organization_id=org.id,
                connector_type="servicenow",
                name="ServiceNow",
                config={"instance_url": f"https://{host}.service-now.com", "client_id": "c", "enabled": True},
            )
        )
    db_session.flush()
    el_a = _element(db_session, org_a.id, "Order API")
    el_b = _element(db_session, org_b.id, "Ledger")
    # The same CI sys_id exists in both tenants and means a different element in each.
    CrosswalkService.write_link("servicenow", "ci-shared", el_a.id, org_id=org_a.id)
    CrosswalkService.write_link("servicenow", "ci-shared", el_b.id, org_id=org_b.id)

    data = {"a": {}, "b": {}}
    calls = []

    class Resp:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    def fake_post(url, **kw):
        return Resp({"access_token": "tok", "expires_in": 1800})

    def fake_get(url, **kw):
        host = url.split("//")[1].split(".")[0]
        table = url.rsplit("/", 1)[1]
        calls.append((host, table, kw["params"]))
        return Resp({"result": data[host].get(table, [])})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(
        credential_vault.OrgCredentialVault, "retrieve", lambda self, **kw: "secret"
    )
    return SimpleNamespace(a=org_a, b=org_b, el_a=el_a, el_b=el_b, data=data, calls=calls)


def _pull(org_id, since=None):
    from app.services.servicenow_connector_service import ServiceNowConnectorService

    return ServiceNowConnectorService().pull_operational_records(org_id, since)


def _rows(org_id):
    from app.models.operational_record import OperationalRecord

    return OperationalRecord.query.filter_by(organization_id=org_id).all()


def test_incidents_problems_and_changes_link_to_the_crosswalk_element(db_session, world):
    world.data["a"] = {
        "incident": [_incident("i1", "ci-shared")],
        "problem": [_incident("p1", "ci-shared", number="PRB1")],
        "change_request": [_incident("c1", "ci-shared", number="CHG1")],
    }
    report = _pull(world.a.id)
    assert (report["fetched"], report["created"], report["unmatched_count"]) == (3, 3, 0)
    rows = {r.kind: r for r in _rows(world.a.id)}
    assert set(rows) == {"incident", "problem", "change"}
    inc = rows["incident"]
    assert inc.element_id == world.el_a.id
    assert inc.source_system == "servicenow" and inc.external_id == "i1"
    assert inc.reference == "INC1" and inc.severity == "1"
    assert inc.opened_at == datetime(2026, 10, 1, 8, 30)
    assert inc.resolved_at is None and inc.closed_at is None


def test_two_organisations_never_see_each_others_records(db_session, world):
    world.data["a"] = {"incident": [_incident("shared-sys-id", "ci-shared", title="x")]}
    world.data["b"] = {"incident": [_incident("shared-sys-id", "ci-shared", number="INC-B")]}
    _pull(world.a.id)
    _pull(world.b.id)
    (row_a,), (row_b,) = _rows(world.a.id), _rows(world.b.id)
    assert row_a.element_id == world.el_a.id and row_a.reference == "INC1"
    assert row_b.element_id == world.el_b.id and row_b.reference == "INC-B"
    assert {h for h, _t, _p in world.calls} == {"a", "b"}


def test_unmatched_records_are_reported_not_stored_or_guessed(db_session, world):
    world.data["a"] = {
        "incident": [
            _incident("ok", "ci-shared"),
            _incident("ghost", "ci-unknown"),
            _incident("noci", ""),
            _incident("bad-date", "ci-shared", opened_at="not a date"),
        ]
    }
    report = _pull(world.a.id)
    assert report["fetched"] == 4 and report["created"] == 1
    assert report["unmatched_count"] == 3
    reasons = {u["external_id"]: u["reason"] for u in report["unmatched"]}
    assert reasons["ghost"] == "configuration item has no crosswalk link to a twin element"
    assert reasons["noci"] == "no configuration item on the record"
    assert None in reasons and reasons[None] == "record could not be mapped"
    assert [r.external_id for r in _rows(world.a.id)] == ["ok"]


def test_unmatched_ci_is_not_attributed_to_another_organisations_element(db_session, world):
    from app.modules.intelligence.services.crosswalk_service import CrosswalkService

    CrosswalkService.write_link("servicenow", "only-b", world.el_b.id, org_id=world.b.id)
    world.data["a"] = {"incident": [_incident("x", "only-b")]}
    report = _pull(world.a.id)
    assert report["unmatched_count"] == 1 and _rows(world.a.id) == []


def test_repull_updates_in_place_and_since_is_sent(db_session, world):
    world.data["a"] = {"incident": [_incident("i1", "ci-shared")]}
    _pull(world.a.id)
    world.data["a"] = {
        "incident": [_incident("i1", "ci-shared", state="6", resolved_at="2026-10-03 10:00:00")]
    }
    report = _pull(world.a.id, since=datetime(2026, 10, 2, 0, 0, 0))
    assert (report["created"], report["updated"]) == (0, 1)
    (row,) = _rows(world.a.id)
    assert row.state == "6" and row.resolved_at == datetime(2026, 10, 3, 10, 0)
    assert any("sys_updated_on>=2026-10-02 00:00:00" in p["sysparm_query"] for _h, _t, p in world.calls)


def test_disabled_connector_returns_nothing(db_session, world):
    from app.models.connector_config import ConnectorConfig

    cfg = ConnectorConfig.query.filter_by(organization_id=world.a.id).one()
    cfg.config = {**cfg.config, "enabled": False}
    db_session.flush()
    assert _pull(world.a.id) == {}
