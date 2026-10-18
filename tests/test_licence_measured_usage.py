"""Measured licence seats from Microsoft 365, kept apart from typed usage.

Fetched through the connector framework's sync path, recorded per organisation,
preferred over the typed figure wherever usage is read for a decision.
"""

from datetime import date, datetime

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

SKU = "ENTERPRISEPACK"


def _licence(db_session, org_id, *, product=SKU, entitled=100, typed=80):
    from app.models.application_portfolio import VendorContract
    from app.models.license_entitlement import LicenseEntitlement

    contract = VendorContract(organization_id=org_id, contract_name="M365 agreement",
                              start_date=date(2026, 1, 1))
    db_session.add(contract)
    db_session.flush()
    ent = LicenseEntitlement(organization_id=org_id, contract_id=contract.id, license_type="named_user",
                             product_name=product, quantity_entitled=entitled, quantity_used=typed,
                             unit_cost=10)
    db_session.add(ent)
    db_session.flush()
    return ent


def _m365_connector(db_session, org_id, monkeypatch, consumed, tenant="tenant-a"):
    from app.connectors import m365
    from app.models.connector_config import ConnectorConfig
    from app.services import connector_framework as cf

    cfg = ConnectorConfig(connector_type="m365", name=f"M365 {tenant}", organization_id=org_id,
                          config={"tenant_id": tenant, "client_id": "cid"})
    db_session.add(cfg)
    db_session.flush()

    async def fake_fetch(self):
        assert self.config.config["tenant_id"] == tenant
        return [{"skuPartNumber": SKU, "consumedUnits": consumed},
                {"skuPartNumber": "UNMAPPED_SKU", "consumedUnits": 3}]

    monkeypatch.setattr(m365.M365LicenceUsageConnector, "_fetch_skus", fake_fetch)
    monkeypatch.setattr(cf, "_credential_field_map",
                        lambda t, o, c: {"base_url": "https://graph.microsoft.com/v1.0",
                                         "tenant_id": c.get("tenant_id"), "client_id": "cid",
                                         "client_secret": "s"})
    return cfg


def test_sync_records_measurement_beside_typed_figure(db_session, make_org, tenant_ctx, monkeypatch):
    from app.services.connector_framework import run_connector_sync

    org = make_org("m365")
    with tenant_ctx(org.id):
        ent = _licence(db_session, org.id, typed=80)
        cfg = _m365_connector(db_session, org.id, monkeypatch, consumed=42)
        result = run_connector_sync(org_id=org.id, connector_id=cfg.id, delegated=True)
        assert result["success"] and result["measurements_recorded"] == 1
        assert result["unmatched"] == ["UNMAPPED_SKU"]

        m = ent.latest_measurement
        assert (m.seats_used, m.source, m.organization_id) == (42, "m365", org.id)
        assert m.fetched_at is not None and ent.last_usage_sync == m.fetched_at
        # typed figure untouched and still reported as typed
        assert ent.quantity_used == 80
        assert ent.effective_usage == (42, "measured")


def test_typed_is_fallback_when_nothing_measured(db_session, make_org, tenant_ctx):
    org = make_org("typed")
    with tenant_ctx(org.id):
        ent = _licence(db_session, org.id, typed=80)
        assert ent.effective_usage == (80, "typed")
        blank = _licence(db_session, org.id, typed=None)
        assert blank.effective_usage == (None, None)


def test_readers_prefer_measured(db_session, make_org, tenant_ctx):
    from app.models.license_usage_measurement import LicenseUsageMeasurement
    from app.modules.procurement import procurement_ai_service as svc

    org = make_org("readers")
    with tenant_ctx(org.id):
        ent = _licence(db_session, org.id, entitled=100, typed=100)  # typed: fully used
        assert svc._build_licenses_position_context(org.id)["unused_entitlement_count"] == 0
        db_session.add(LicenseUsageMeasurement(organization_id=org.id, entitlement_id=ent.id,
                                               seats_used=30, source="m365", fetched_at=datetime.utcnow()))
        db_session.flush()
        ctx = svc._build_licenses_position_context(org.id)
        assert ctx["unused_entitlement_count"] == 1
        assert ctx["unused_entitlements"][0]["quantity_used"] == 30
        assert ctx["unused_entitlements"][0]["quantity_used_basis"] == "measured"
        renewal = svc._build_renewal_brief_context(ent.contract)
        assert renewal["licences"][0]["seats_used"] == 30
        assert renewal["licences"][0]["seats_used_basis"] == "measured"
        assert float(ent.unused_value) == 700.0  # 70 unused seats x 10


def test_latest_measurement_wins(db_session, make_org, tenant_ctx):
    from datetime import timedelta

    from app.models.license_usage_measurement import LicenseUsageMeasurement

    org = make_org("latest")
    with tenant_ctx(org.id):
        ent = _licence(db_session, org.id)
        now = datetime.utcnow()
        for seats, age in ((50, 2), (60, 0), (55, 1)):
            db_session.add(LicenseUsageMeasurement(organization_id=org.id, entitlement_id=ent.id,
                                                   seats_used=seats, source="m365",
                                                   fetched_at=now - timedelta(days=age)))
        db_session.flush()
        assert ent.latest_measurement.seats_used == 60


def test_two_organisations_never_share_measurements(db_session, make_org, tenant_ctx, monkeypatch):
    from app.models.license_entitlement import LicenseEntitlement
    from app.models.license_usage_measurement import LicenseUsageMeasurement
    from app.services.connector_framework import run_connector_sync

    org_a, org_b = make_org("iso-a"), make_org("iso-b")
    with tenant_ctx(org_a.id):
        ent_a = _licence(db_session, org_a.id, typed=10)
        ent_a_id = ent_a.id
    with tenant_ctx(org_b.id):
        ent_b = _licence(db_session, org_b.id, typed=20)
        ent_b_id = ent_b.id
    with tenant_ctx(org_a.id):
        cfg = _m365_connector(db_session, org_a.id, monkeypatch, consumed=77, tenant="tenant-a")
        run_connector_sync(org_id=org_a.id, connector_id=cfg.id, delegated=True)
        assert db_session.get(LicenseEntitlement, ent_a_id).latest_measurement.seats_used == 77

    db_session.expunge_all()
    with tenant_ctx(org_b.id):
        ent_b = db_session.get(LicenseEntitlement, ent_b_id)
        assert ent_b.latest_measurement is None
        assert ent_b.effective_usage == (20, "typed")
        assert ent_b.last_usage_sync is None
        assert db_session.query(LicenseUsageMeasurement).count() == 0
        # the same sync cannot be run as org B against org A's connector
        assert run_connector_sync(org_id=org_b.id, connector_id=cfg.id, delegated=True)["success"] is False

    db_session.expunge_all()
    with tenant_ctx(org_a.id):
        assert db_session.query(LicenseUsageMeasurement).count() == 1


def test_licence_screen_shows_typed_and_measured_apart(db_session, make_org, tenant_ctx, app):
    from flask import render_template

    from app.models.license_usage_measurement import LicenseUsageMeasurement

    org = make_org("screen")
    with tenant_ctx(org.id):
        ent = _licence(db_session, org.id, typed=80)
        db_session.add(LicenseUsageMeasurement(organization_id=org.id, entitlement_id=ent.id,
                                               seats_used=42, source="m365", fetched_at=datetime(2026, 10, 1, 9, 30)))
        db_session.flush()
        html = render_template("procurement/license_detail.html", license=ent)
        assert "Used (typed)" in html and "Measured" in html
        measured_block = html.split('data-testid="measured-usage"')[1].split("</div>\n                    </div>")[0]
        assert "42" in measured_block and "Microsoft 365" in measured_block and "80" not in measured_block
        blank = _licence(db_session, org.id, typed=80)
        html2 = render_template("procurement/license_detail.html", license=blank)
        assert "Microsoft 365" not in html2
