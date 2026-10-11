"""R1-B85 (PB-0216): the CTO scorecard.

Supported-version share and open exceptions each read an existing canonical
store (no second figure invented), the oldest two exceptions sort first for
escalation, and escalating never crosses organisations.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from app.modules.architecture.services.cto_scorecard_service import (
    escalate,
    open_exceptions,
    supported_version_share,
)


def _vendor_product(db_session, *, end_of_life_date):
    from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct

    vendor = VendorOrganization(name=f"Vendor {uuid.uuid4().hex[:6]}")
    db_session.add(vendor)
    db_session.flush()
    product = VendorProduct(
        vendor_organization_id=vendor.id,
        name=f"Product {uuid.uuid4().hex[:6]}",
        end_of_life_date=end_of_life_date,
    )
    db_session.add(product)
    db_session.flush()
    return product


def _app(db_session, org, vendor_product_id):
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(
        name=f"App {uuid.uuid4().hex[:6]}", organization_id=org.id, vendor_product_id=vendor_product_id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _exception(db_session, org, *, raised_at, title="Exception"):
    from app.models.architecture_decision import ArchitectureChangeRequest

    row = ArchitectureChangeRequest(
        acr_reference=f"ACR-TEST-{uuid.uuid4().hex[:8]}",
        title=title,
        organization_id=org.id,
        disposition="exception",
        raised_at=raised_at,
    )
    db_session.add(row)
    db_session.flush()
    return row


class TestSupportedVersionShare:
    def test_no_vendor_product_recorded_reads_as_no_data_not_zero_percent(self, db_session, make_org):
        org = make_org("scorecard-no-data")
        result = supported_version_share(org.id)
        assert result == {"total": 0, "supported": 0, "share_pct": None}

    def test_mix_of_supported_and_unsupported_computes_real_share(self, db_session, make_org):
        org = make_org("scorecard-mix")
        supported = _vendor_product(db_session, end_of_life_date=datetime.utcnow() + timedelta(days=3650))
        unsupported = _vendor_product(db_session, end_of_life_date=datetime.utcnow() - timedelta(days=1))
        _app(db_session, org, supported.id)
        _app(db_session, org, unsupported.id)
        db_session.commit()

        result = supported_version_share(org.id)
        assert result["total"] == 2
        assert result["supported"] == 1
        assert result["share_pct"] == 50.0

    def test_two_organisations_shares_never_cross(self, db_session, make_org):
        org_a = make_org("scorecard-fence-a")
        org_b = make_org("scorecard-fence-b")
        unsupported = _vendor_product(db_session, end_of_life_date=datetime.utcnow() - timedelta(days=1))
        _app(db_session, org_a, unsupported.id)
        db_session.commit()

        assert supported_version_share(org_a.id)["total"] == 1
        assert supported_version_share(org_b.id)["total"] == 0


class TestOpenExceptions:
    def test_oldest_exception_sorts_first(self, db_session, make_org):
        org = make_org("scorecard-exceptions")
        older = _exception(db_session, org, raised_at=datetime.utcnow() - timedelta(days=90), title="Older")
        newer = _exception(db_session, org, raised_at=datetime.utcnow() - timedelta(days=5), title="Newer")
        db_session.commit()

        rows = open_exceptions(org.id)
        assert [r["id"] for r in rows] == [older.id, newer.id]

    def test_a_closed_exception_is_not_open(self, db_session, make_org):
        org = make_org("scorecard-closed")
        row = _exception(db_session, org, raised_at=datetime.utcnow() - timedelta(days=10))
        row.closed_at = datetime.utcnow()
        db_session.commit()

        assert open_exceptions(org.id) == []

    def test_escalate_marks_the_row_and_reload_reflects_it(self, db_session, make_org):
        org = make_org("scorecard-escalate")
        row = _exception(db_session, org, raised_at=datetime.utcnow() - timedelta(days=20))
        db_session.commit()

        assert escalate(org.id, row.id) is True

        db_session.expire_all()
        reloaded = open_exceptions(org.id)
        assert reloaded[0]["escalated_at"] is not None

    def test_escalate_refuses_another_organisations_exception(self, db_session, make_org):
        org_a = make_org("scorecard-escalate-fence-a")
        org_b = make_org("scorecard-escalate-fence-b")
        row = _exception(db_session, org_a, raised_at=datetime.utcnow() - timedelta(days=20))
        db_session.commit()

        assert escalate(org_b.id, row.id) is False
        db_session.expire_all()
        assert open_exceptions(org_a.id)[0]["escalated_at"] is None

    def test_escalate_a_nonexistent_exception_returns_false_not_an_error(self, db_session, make_org):
        org = make_org("scorecard-escalate-missing")
        assert escalate(org.id, 999999) is False
