"""Datadog as the second operational source adapter: real adapter and connector, HTTP faked."""

from __future__ import annotations

from datetime import datetime

import aiohttp
import pytest


def _element(db_session, org_id, name):
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(
        name=name, type="ApplicationComponent", layer="Application", organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def _monitor(mid, service="orders", state="Alert", **extra):
    return {
        "id": mid,
        "name": f"monitor {mid}",
        "overall_state": state,
        "priority": 2,
        "tags": [f"service:{service}", "env:prod"] if service else ["env:prod"],
        "created": "2026-09-01T08:00:00+00:00",
        "modified": "2026-10-01T08:00:00+00:00",
        "overall_state_modified": "2026-10-01T08:30:00+00:00",
        **extra,
    }


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    async def json(self):
        return self._body

    def release(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def world(db_session, make_org, monkeypatch, app):
    from types import SimpleNamespace

    import app.utils.ssrf_guard as guard

    from app.models.connector_config import ConnectorConfig
    from app.modules.codegen.services import credential_vault
    from app.modules.intelligence.services.crosswalk_service import CrosswalkService

    monkeypatch.setitem(app.config, "ALLOW_INTERNAL_WEBHOOK_TARGETS", False)
    # Datadog is deliberately not on the built-in crosswalk allowlist; approve it as configuration.
    monkeypatch.setitem(app.config, "COMPLIANCE_APPROVED_CONNECTOR_TYPES", ("datadog",))
    monkeypatch.setattr(
        guard.socket, "getaddrinfo", lambda host, port, *a, **k: [(2, 1, 6, "", ("93.184.216.34", port))]
    )
    monkeypatch.setattr(guard, "_local_interface_addresses", lambda: set())

    org_a, org_b = make_org("dd-a"), make_org("dd-b")
    # Different sites per organisation, so a request's host says whose it was.
    sites = {org_a.id: "datadoghq.com", org_b.id: "datadoghq.eu"}
    for org in (org_a, org_b):
        db_session.add(
            ConnectorConfig(
                organization_id=org.id,
                connector_type="datadog",
                name="Datadog",
                config={"site": sites[org.id], "enabled": True},
            )
        )
    db_session.flush()
    el_a = _element(db_session, org_a.id, "Order API")
    el_b = _element(db_session, org_b.id, "Ledger")
    # The same service tag exists in both tenants and means a different element in each.
    CrosswalkService.write_link("datadog", "orders", el_a.id, org_id=org_a.id)
    CrosswalkService.write_link("datadog", "orders", el_b.id, org_id=org_b.id)

    data = {"datadoghq.com": [], "datadoghq.eu": []}
    calls = []

    async def fake_request(self, method, str_or_url, **kw):
        url = str(str_or_url)
        site = url.split("//")[1].split("/")[0].removeprefix("api.")
        calls.append({"site": site, "headers": kw.get("headers", {}), "params": kw.get("params", {})})
        page = kw["params"]["page"]
        size = kw["params"]["page_size"]
        return _Resp(200, data[site][page * size : (page + 1) * size])

    monkeypatch.setattr(aiohttp.ClientSession, "_request", fake_request)
    # The vault: per-organisation keys, so the header says whose call it was.
    monkeypatch.setattr(
        credential_vault.OrgCredentialVault,
        "retrieve",
        lambda self, org_id=None, connector_type="", credential_type="credentials": f"{credential_type}-{org_id}",
    )
    return SimpleNamespace(
        a=org_a, b=org_b, el_a=el_a, el_b=el_b, sites=sites, data=data, calls=calls
    )


def _pull(org_id, since=None):
    from app.connectors.datadog import pull_operational_records

    return pull_operational_records(org_id, since)


def _rows(org_id):
    from app.models.operational_record import OperationalRecord

    return OperationalRecord.query.filter_by(organization_id=org_id).all()


def test_alerting_and_resolved_monitors_link_to_the_crosswalk_element(db_session, world):
    world.data["datadoghq.com"] = [
        _monitor(101),
        _monitor(102, state="OK", overall_state_modified="2026-10-02T10:00:00Z"),
    ]
    report = _pull(world.a.id)
    assert (report["fetched"], report["created"], report["unmatched_count"]) == (2, 2, 0)
    rows = {r.external_id: r for r in _rows(world.a.id)}
    alert, ok = rows["101"], rows["102"]
    assert alert.kind == "incident" and alert.source_system == "datadog"
    assert alert.element_id == world.el_a.id and alert.severity == "2" and alert.state == "Alert"
    assert alert.opened_at == datetime(2026, 10, 1, 8, 30) and alert.resolved_at is None
    assert ok.resolved_at == datetime(2026, 10, 2, 10, 0) and ok.opened_at is None


def test_two_organisations_never_see_each_others_alerts(db_session, world):
    world.data["datadoghq.com"] = [_monitor(7, name="A only")]
    world.data["datadoghq.eu"] = [_monitor(7, name="B only")]
    _pull(world.a.id)
    _pull(world.b.id)
    (row_a,), (row_b,) = _rows(world.a.id), _rows(world.b.id)
    assert row_a.element_id == world.el_a.id and row_a.title == "A only"
    assert row_b.element_id == world.el_b.id and row_b.title == "B only"
    # Each organisation's pull went to its own site, with its own keys, and only its own.
    assert [c["site"] for c in world.calls] == ["datadoghq.com", "datadoghq.eu"]
    assert world.calls[0]["headers"]["DD-API-KEY"] == f"api_key-{world.a.id}"
    assert world.calls[1]["headers"]["DD-API-KEY"] == f"api_key-{world.b.id}"


def test_unmatched_alerts_are_reported_not_stored_or_guessed(db_session, world):
    world.data["datadoghq.com"] = [
        _monitor(1),
        _monitor(2, service="ghost"),
        _monitor(3, service=None),
        _monitor(4, tags=["service:orders", "service:billing"]),
        _monitor(5, overall_state_modified="not a date"),
    ]
    report = _pull(world.a.id)
    assert report["fetched"] == 5 and report["created"] == 1
    assert report["unmatched_count"] == 4
    reasons = {u["external_id"]: u["reason"] for u in report["unmatched"]}
    assert reasons["2"] == "configuration item has no crosswalk link to a twin element"
    assert reasons["3"] == "no configuration item on the record"
    assert reasons[None] == "record could not be mapped"
    assert [u for u in report["unmatched"] if u["reason"] == "record could not be mapped"].__len__() == 2
    assert [r.external_id for r in _rows(world.a.id)] == ["1"]


def test_unmatched_service_is_not_attributed_to_another_organisations_element(db_session, world):
    from app.modules.intelligence.services.crosswalk_service import CrosswalkService

    CrosswalkService.write_link("datadog", "only-b", world.el_b.id, org_id=world.b.id)
    world.data["datadoghq.com"] = [_monitor(9, service="only-b")]
    report = _pull(world.a.id)
    assert report["unmatched_count"] == 1 and _rows(world.a.id) == []


def test_monitors_that_never_changed_state_are_not_alerts(db_session, world):
    world.data["datadoghq.com"] = [
        _monitor(1, state="OK", overall_state_modified="2026-09-01T08:00:00+00:00"),
    ]
    assert _pull(world.a.id)["fetched"] == 0


def test_repull_updates_in_place_and_since_filters(db_session, world):
    world.data["datadoghq.com"] = [_monitor(1), _monitor(2)]
    _pull(world.a.id)
    world.data["datadoghq.com"] = [
        _monitor(1, state="OK", overall_state_modified="2026-10-03T10:00:00+00:00"),
        _monitor(2),
    ]
    report = _pull(world.a.id, since=datetime(2026, 10, 2, 0, 0, 0))
    assert (report["fetched"], report["created"], report["updated"]) == (1, 0, 1)
    rows = {r.external_id: r for r in _rows(world.a.id)}
    assert rows["1"].state == "OK" and rows["1"].resolved_at == datetime(2026, 10, 3, 10, 0)
    assert rows["2"].state == "Alert"


def test_listing_is_paged(db_session, world):
    world.data["datadoghq.com"] = [_monitor(i) for i in range(1, 1202)]
    report = _pull(world.a.id)
    assert report["fetched"] == 1201 and len(world.calls) == 2


def test_failed_listing_raises_rather_than_returning_a_short_list(db_session, world, monkeypatch):
    async def forbidden(self, method, url, **kw):
        return _Resp(403, {})

    monkeypatch.setattr(aiohttp.ClientSession, "_request", forbidden)
    with pytest.raises(RuntimeError, match="403"):
        _pull(world.a.id)
    assert _rows(world.a.id) == []


def test_disabled_or_missing_connector_returns_nothing(db_session, world):
    from app.models.connector_config import ConnectorConfig

    cfg = ConnectorConfig.query.filter_by(organization_id=world.a.id).one()
    cfg.config = {**cfg.config, "enabled": False}
    db_session.flush()
    assert _pull(world.a.id) == {}
    assert _pull(987654) == {}
