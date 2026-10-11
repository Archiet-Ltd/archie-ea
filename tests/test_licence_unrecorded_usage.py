"""A licence whose deployment or usage is not known stays "not recorded".

A blank deployed/used figure is stored as NULL (rendered "—"), never as a 0
that reads as a measured "nobody uses this", and no compliance verdict is
reached until deployment is recorded. ``flask reconcile-schema`` relaxes the
old NOT NULL columns on an existing database.
"""

from datetime import date

import pytest
from sqlalchemy import inspect, text

pytestmark = pytest.mark.usefixtures("db_session")


def _licence(db_session, org_id, form):
    from app.models.application_portfolio import VendorContract
    from app.models.license_entitlement import LicenseEntitlement
    from app.modules.procurement.crud_routes import _apply_license_form

    contract = VendorContract(organization_id=org_id, contract_name="Licence host",
                              start_date=date(2026, 1, 1))
    db_session.add(contract)
    db_session.flush()
    entitlement = LicenseEntitlement(organization_id=org_id, contract_id=contract.id)
    _apply_license_form(entitlement, form)
    db_session.add(entitlement)
    db_session.flush()
    db_session.expire(entitlement)
    return entitlement


def test_blank_usage_is_stored_as_not_recorded(db_session, make_org, tenant_ctx):
    org = make_org("licence-blank")
    with tenant_ctx(org.id):
        lic = _licence(db_session, org.id, {"product_name": "Unmeasured", "quantity_entitled": "50",
                                            "quantity_deployed": "", "quantity_used": ""})
        assert (lic.quantity_entitled, lic.quantity_deployed, lic.quantity_used) == (50, None, None)
        assert lic.compliance_status is None
        assert lic.utilization_percent is None and lic.deployment_percent is None
        assert lic.available_quantity is None


def test_recorded_usage_is_kept_and_judged(db_session, make_org, tenant_ctx):
    org = make_org("licence-measured")
    with tenant_ctx(org.id):
        lic = _licence(db_session, org.id, {"product_name": "Measured", "quantity_entitled": "100",
                                            "quantity_deployed": "120", "quantity_used": "0"})
        assert (lic.quantity_deployed, lic.quantity_used) == (120, 0)
        assert lic.compliance_status == "over_deployed"
        assert lic.utilization_percent == 0 and lic.available_quantity == -20


def test_reconcile_schema_allows_unrecorded_usage(db_session):
    from app.commands.reconcile_schema import _relax_not_null_for_unrecorded_values

    conn = db_session.connection()
    # Put the columns back the way older databases have them (rolled back at teardown).
    for column in ("quantity_deployed", "quantity_used"):
        conn.execute(text(f"UPDATE license_entitlements SET {column} = 0 WHERE {column} IS NULL"))
        conn.execute(text(f"ALTER TABLE license_entitlements ALTER COLUMN {column} SET NOT NULL"))

    added, failed = [], []
    _relax_not_null_for_unrecorded_values(dry_run=False, existing_tables={"license_entitlements"},
                                          added=added, failed=failed)
    assert failed == [] and len(added) == 2
    columns = {c["name"]: c for c in inspect(conn).get_columns("license_entitlements")}
    assert columns["quantity_deployed"]["nullable"] and columns["quantity_used"]["nullable"]

    added = []
    _relax_not_null_for_unrecorded_values(dry_run=False, existing_tables={"license_entitlements"},
                                          added=added, failed=failed)
    assert added == []
