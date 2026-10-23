"""purge-demo-test-records removes the named test records from a demo
organisation and nothing from an ordinary one."""

from __future__ import annotations

import os

from click.testing import CliRunner

from app.commands.purge_demo_test_records import purge_demo_test_records_command
from app.commands.seed_demo_company import seed_demo_company

TEST_APP = 'ZZ-AUDIT alert(1) & "quotes" éè 日本語'


def _add(org_id, name, vendor_id=None):
    import datetime

    from app import db
    from app.models.application_portfolio import ApplicationComponent, VendorContract

    row = ApplicationComponent(name=name, organization_id=org_id)
    db.session.add(row)
    db.session.commit()
    if vendor_id:
        db.session.add(VendorContract(
            contract_name="Audit contract", organization_id=org_id, application_id=row.id,
            vendor_id=vendor_id, start_date=datetime.date(2026, 1, 1)))
        db.session.commit()
    return row.id


def _names(org_id):
    from app.models.application_portfolio import ApplicationComponent

    return {a.name for a in ApplicationComponent.query.filter_by(organization_id=org_id).all()}


def test_only_the_demo_organisation_loses_its_test_records(app, db_session, make_org):
    from app import db
    from app.jobs.tenant_safe_job import platform_scope
    from app.models.organization import Organization
    from app.models.vendor.vendor_organization import VendorOrganization

    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    with platform_scope("test"):
        demo_id = db.session.query(Organization.id).filter_by(slug="lantern-quay").scalar()
        ordinary = make_org()
        ordinary_id = ordinary.id if hasattr(ordinary, "id") else ordinary
        vendor = VendorOrganization(name="ZZ-AUDIT Vendor Ltd")
        db.session.add(vendor)
        db.session.commit()
        vendor_id = vendor.id
        demo_apps = [_add(demo_id, TEST_APP, vendor_id), _add(demo_id, "Audit Test App"),
                     _add(demo_id, "ZZ-CSRF-PROBE")]
        ordinary_app = _add(ordinary_id, TEST_APP)
        demo_before = _names(demo_id)

        runner = CliRunner()
        dry = runner.invoke(purge_demo_test_records_command, [])
        print(dry.output)
        assert dry.exit_code == 0, dry.output
        assert "would remove application %d" % demo_apps[0] in dry.output
        assert "would remove application %d" % demo_apps[1] in dry.output
        assert "would remove vendor %d" % vendor_id in dry.output
        assert "ZZ-CSRF-PROBE" not in dry.output
        assert "dry run: nothing removed" in dry.output
        assert _names(demo_id) == demo_before  # a dry run removes nothing

        applied = runner.invoke(purge_demo_test_records_command, ["--apply"])
        assert applied.exit_code == 0, applied.output
        assert "removed application %d" % demo_apps[0] in applied.output
        demo_after = _names(demo_id)
        assert TEST_APP not in demo_after and "Audit Test App" not in demo_after
        assert "ZZ-CSRF-PROBE" in demo_after  # not on the list: kept
        assert demo_after == demo_before - {TEST_APP, "Audit Test App"}
        assert TEST_APP in _names(ordinary_id)  # the ordinary organisation is untouched
        assert VendorOrganization.query.filter_by(id=vendor_id).first() is None

        again = runner.invoke(purge_demo_test_records_command, ["--apply"])
        assert again.exit_code == 0
        assert "nothing to remove" in again.output
        assert TEST_APP in _names(ordinary_id)
        assert ordinary_app
