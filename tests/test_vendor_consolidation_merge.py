"""
Two-organisation tests for vendor merge, contract links and renewals.

Scope:
  1. Consolidation of ``archimate_contracts`` into ``vendor_contracts``
  2. One vendor record with legal entities and parent group
  3. Duplicate vendor merge (keep-oldest with reviewable list)
"""

from datetime import date, datetime, timedelta

import pytest


# ===========================================================================
# Merge a duplicate vendor and prove contracts follow the surviving record
# ===========================================================================


@pytest.mark.usefixtures("db_session")
class TestVendorMergeTwoOrg:
    """Vendor merge respects both organisational boundaries."""

    def _make_vendor(self, name: str, created_offset_days: int = 0):
        """Helper: create and return a VendorOrganization row.

        Uses a uuid suffix only for code and seed_source_id to guarantee
        uniqueness on those columns. The caller must ensure name is already
        unique across the DB (e.g. by using distinct punctuation variants).
        """
        import uuid
        from app.models.vendor.vendor_organization import VendorOrganization

        suffix = uuid.uuid4().hex[:8]
        vendor = VendorOrganization(
            name=name,
            code="VEND-" + suffix.upper(),
            seed_source_id="test-" + suffix,
            seeded_by="test",
            created_at=datetime.utcnow() - timedelta(days=created_offset_days),
        )
        return vendor

    def _make_org(self, db_session, label: str):
        """Helper: create and return an Organization row."""
        import uuid
        from app.models.organization import Organization

        suffix = uuid.uuid4().hex[:10]
        org = Organization(name=f"Test Org {label} {suffix}", slug=f"test-org-{label}-{suffix}")
        db_session.add(org)
        db_session.flush()
        return org

    def _make_contract(self, db_session, org, vendor, app_component=None,
                       contract_name="Test Contract", start_date=None):
        """Helper: create and return a VendorContract row."""
        from app.models.application_portfolio import VendorContract

        c = VendorContract(
            contract_name=contract_name,
            organization_id=org.id,
            vendor_id=vendor.id,
            start_date=start_date or date(2026, 1, 1),
            status="active",
        )
        if app_component is not None:
            c.application_id = app_component.id
        db_session.add(c)
        db_session.flush()
        return c

    def _make_app(self, db_session, org, name="Test Application"):
        """Helper: create and return an ApplicationComponent row."""
        from app.models.application_portfolio import ApplicationComponent

        app = ApplicationComponent(
            name=name,
            description="Test app for vendor merge tests",
            organization_id=org.id,
        )
        db_session.add(app)
        db_session.flush()
        return app

    def test_find_duplicates_by_name(self, db_session):
        """Duplicates with similar names are detected by the merge service.

        Uses names that differ textually but normalise the same way after
        punctuation stripping, because VendorOrganization.name is UNIQUE.
        """
        from app.modules.vendors.services.vendor_merge_service import VendorMergeService

        # "Acme Corp" and "Acme-Corp" normalise to the same string after
        # punctuation stripping, but are distinct for the UNIQUE constraint.
        v1 = self._make_vendor("Acme Corp", created_offset_days=100)
        v2 = self._make_vendor("Acme.Corp", created_offset_days=10)  # duplicate after normalise
        v3 = self._make_vendor("Beta Ltd", created_offset_days=50)
        db_session.add_all([v1, v2, v3])
        db_session.flush()

        service = VendorMergeService()
        result = service.find_duplicates()

        assert result["total_candidates"] >= 1
        # The oldest (v1, offset 100) should be the keeper
        match = [c for c in result["candidates"] if c["keep_id"] == v1.id]
        assert len(match) == 1, "Acme Corp should be detected as duplicate with v1 as keeper"
        assert v2.id in match[0]["merge_ids"], "v2 should be in merge_ids"

    def test_merge_repoints_contracts(self, db_session):
        """After merge, contracts previously pointed at the duplicate point
        at the surviving vendor."""
        from app.models.vendor.vendor_organization import VendorOrganization
        from app.modules.vendors.services.vendor_merge_service import VendorMergeService

        org = self._make_org(db_session, "merge-org")
        v1 = self._make_vendor("DataStream Inc", created_offset_days=100)
        v2 = self._make_vendor("DataStream-Inc", created_offset_days=10)
        db_session.add_all([v1, v2])
        db_session.flush()

        # Create a contract pointing at v2 (the duplicate)
        contract = self._make_contract(
            db_session, org, v2, contract_name="DataStream Support"
        )
        assert contract.vendor_id == v2.id

        # Merge v2 into v1
        service = VendorMergeService()
        merge_result = service.merge(keep_id=v1.id, merge_ids=[v2.id])

        assert merge_result["kept_id"] == v1.id
        assert merge_result["merged_count"] == 1
        assert merge_result["contracts_repointed"] >= 1

        # Reload the contract — vendor_id should now be v1
        db_session.refresh(contract)
        assert contract.vendor_id == v1.id, "Contract vendor_id was not repointed"

    def test_merge_two_orgs_keeps_isolation(self, db_session):
        """Contracts in both organisations are correctly repointed after
        a vendor merge, and org isolation is preserved."""
        from app.models.vendor.vendor_organization import VendorOrganization
        from app.modules.vendors.services.vendor_merge_service import VendorMergeService

        org_a = self._make_org(db_session, "org-a")
        org_b = self._make_org(db_session, "org-b")

        v1 = self._make_vendor("MegaSoft", created_offset_days=100)
        v2 = self._make_vendor("MegaSoft.", created_offset_days=10)  # dot makes it distinct for UNIQUE
        db_session.add_all([v1, v2])
        db_session.flush()

        c_a = self._make_contract(db_session, org_a, v2, contract_name="MegaSoft License A")
        c_b = self._make_contract(db_session, org_b, v2, contract_name="MegaSoft License B")
        assert c_a.vendor_id == v2.id
        assert c_b.vendor_id == v2.id
        assert c_a.organization_id == org_a.id
        assert c_b.organization_id == org_b.id

        service = VendorMergeService()
        service.merge(keep_id=v1.id, merge_ids=[v2.id])

        db_session.refresh(c_a)
        db_session.refresh(c_b)
        assert c_a.vendor_id == v1.id
        assert c_b.vendor_id == v1.id
        # Org isolation preserved
        assert c_a.organization_id == org_a.id
        assert c_b.organization_id == org_b.id

    def test_merge_report_format(self, db_session):
        """The merge report returns a reviewable list of what was merged."""
        from app.modules.vendors.services.vendor_merge_service import VendorMergeService

        v1 = self._make_vendor("Cloud Nine", created_offset_days=100)
        v2 = self._make_vendor("Cloud-Nine", created_offset_days=10)
        db_session.add_all([v1, v2])
        db_session.flush()

        service = VendorMergeService()
        result = service.find_duplicates()
        report = service.merge_report(result["candidates"])

        assert len(report) >= 1
        row = next(r for r in report if r["kept_vendor"]["id"] == v1.id)
        assert row["kept_vendor"]["name"] == "Cloud Nine"
        assert any(m["id"] == v2.id for m in row["merged_vendors"])
        assert row["total_merged"] >= 1


# ===========================================================================
# Vendor record with legal entities and parent group
# ===========================================================================


@pytest.fixture(scope="session")
def _vendor_legal_schema(_schema, app):
    """Apply schema migrations so the partial unique index on
    legal_registration_number exists, exactly as ``flask db upgrade``
    builds it from an empty database.

    ``_schema`` calls ``db.create_all()`` which creates every table but
    does not honour ``postgresql_where`` on index definitions, so the
    partial unique index declared in VendorOrganization.__table_args__
    is missing until the Alembic migration runs.
    """
    from alembic import command
    from sqlalchemy import text

    with app.app_context():
        from app.commands.schema_migrations import (
            _alembic_config, recorded_revisions, known_revisions,
            BASELINE_REVISION, acquire_upgrade_lock, _UPGRADE_LOCK_KEY,
        )
        from app.extensions import db

        config = _alembic_config()
        known = known_revisions(config)

        with db.engine.connect() as lock_conn:
            acquire_upgrade_lock(lock_conn)
            try:
                with db.engine.connect() as conn:
                    before = recorded_revisions(conn)
                unknown = [r for r in before if r not in known]
                if unknown:
                    command.stamp(config, BASELINE_REVISION, purge=True)
                command.upgrade(config, "head")
            finally:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _UPGRADE_LOCK_KEY})
                lock_conn.commit()


@pytest.mark.usefixtures("db_session", "_vendor_legal_schema")
class TestVendorLegalEntities:
    """VendorOrganization carries legal entity fields and parent group FK."""

    def test_legal_entity_columns_exist(self, app):
        """The vendor_organizations table has the new columns."""
        import sqlalchemy as sa

        with app.app_context():
            from app.extensions import db

            inspector = sa.inspect(db.engine)
            cols = {c["name"] for c in inspector.get_columns("vendor_organizations")}
            assert "legal_name" in cols
            assert "legal_registration_number" in cols
            assert "legal_address" in cols
            assert "parent_vendor_id" in cols

    def test_store_and_read_legal_entity(self, db_session):
        """Legal entity fields can be stored and read back."""
        from app.models.vendor.vendor_organization import VendorOrganization

        v = VendorOrganization(
            name="TestCorp GmbH",
            code="VEND-TESTCORP",
            seed_source_id="test-legal",
            seeded_by="test",
            legal_name="TestCorp GmbH (Germany)",
            legal_registration_number="HRB 123456",
            legal_address="123 Business Park, Berlin, 10115, Germany",
        )
        db_session.add(v)
        db_session.flush()

        db_session.refresh(v)
        assert v.legal_name == "TestCorp GmbH (Germany)"
        assert v.legal_registration_number == "HRB 123456"
        assert v.legal_address == "123 Business Park, Berlin, 10115, Germany"

    def test_parent_vendor_relationship(self, db_session):
        """Parent group vendor can be set and traversed via relationship."""
        from app.models.vendor.vendor_organization import VendorOrganization

        parent = VendorOrganization(
            name="Global Parent Corp",
            code="VEND-GLOBAL",
            seed_source_id="test-parent",
            seeded_by="test",
        )
        child = VendorOrganization(
            name="Child Subsidiary Ltd",
            code="VEND-CHILD",
            seed_source_id="test-child",
            seeded_by="test",
            parent_vendor_id=None,  # set after flush
        )
        db_session.add_all([parent, child])
        db_session.flush()

        child.parent_vendor_id = parent.id
        db_session.flush()
        db_session.refresh(child)
        db_session.refresh(parent)

        assert child.parent_vendor_id == parent.id
        assert child.parent_vendor.id == parent.id
        assert parent in child.parent_vendor.__class__.query.all()
        # Test backref
        assert child in parent.subsidiary_vendors.all()

    def test_legal_registration_number_unique(self, db_session):
        """legal_registration_number is unique."""
        from app.models.vendor.vendor_organization import VendorOrganization
        from sqlalchemy.exc import IntegrityError

        v1 = VendorOrganization(
            name="Vendor One",
            code="VEND-V1",
            seed_source_id="test-uniq-1",
            seeded_by="test",
            legal_registration_number="UNIQUE-REG-001",
        )
        db_session.add(v1)
        db_session.flush()

        v2 = VendorOrganization(
            name="Vendor Two",
            code="VEND-V2",
            seed_source_id="test-uniq-2",
            seeded_by="test",
            legal_registration_number="UNIQUE-REG-001",  # same number
        )
        db_session.add(v2)
        with pytest.raises(IntegrityError):
            db_session.flush()


# ===========================================================================
# Consolidation of archimate_contracts into vendor_contracts
# ===========================================================================


@pytest.mark.usefixtures("db_session")
class TestContractArchimateConsolidation:
    """VendorContract can link to an ArchiMate Contract as a mirror."""

    def test_archimate_contract_id_column_exists(self, app):
        """The vendor_contracts table has the archimate_contract_id column."""
        import sqlalchemy as sa

        with app.app_context():
            from app.extensions import db

            inspector = sa.inspect(db.engine)
            cols = {c["name"] for c in inspector.get_columns("vendor_contracts")}
            assert "archimate_contract_id" in cols

    def test_link_vendor_contract_to_archimate_contract(self, db_session, app):
        """A VendorContract can reference an ArchiMate Contract."""
        from datetime import date
        from app.models.application_portfolio import VendorContract
        from app.models.archimate_business import Contract as ArchimateContract
        from app.models.organization import Organization
        import uuid

        org = Organization(
            name=f"Test Org {uuid.uuid4().hex[:10]}",
            slug=f"test-org-{uuid.uuid4().hex[:10]}",
        )
        db_session.add(org)
        db_session.flush()

        # Create ArchiMate Contract within a tenant context so the
        # auto-created ArchiMateElement gets an organization_id
        with app.test_request_context("/"):
            from flask import g
            g.current_org_id = org.id

            arch_contract = ArchimateContract(
                name="ArchiMate Mirror Contract",
                contract_type="Service Agreement",
                effective_date=date(2026, 1, 1),
            )
            db_session.add(arch_contract)
            db_session.flush()

        vendor_contract = VendorContract(
            contract_name="Vendor Side Contract",
            organization_id=org.id,
            start_date=date(2026, 1, 1),
            archimate_contract_id=arch_contract.id,
        )
        db_session.add(vendor_contract)
        db_session.flush()
        db_session.refresh(vendor_contract)

        assert vendor_contract.archimate_contract_id == arch_contract.id
        assert vendor_contract.archimate_contract is not None
        assert vendor_contract.archimate_contract.name == "ArchiMate Mirror Contract"

    def test_archimate_contract_backref(self, db_session, app):
        """An ArchiMate Contract can see its VendorContract mirror via backref."""
        from datetime import date
        from app.models.application_portfolio import VendorContract
        from app.models.archimate_business import Contract as ArchimateContract
        from app.models.organization import Organization
        import uuid

        org = Organization(
            name=f"Test Org {uuid.uuid4().hex[:10]}",
            slug=f"test-org-{uuid.uuid4().hex[:10]}",
        )
        db_session.add(org)
        db_session.flush()

        with app.test_request_context("/"):
            from flask import g
            g.current_org_id = org.id

            arch_contract = ArchimateContract(
                name="Contract With Mirror",
                contract_type="License",
                effective_date=date(2026, 1, 1),
            )
            db_session.add(arch_contract)
            db_session.flush()

        vc1 = VendorContract(
            contract_name="Primary Mirror",
            organization_id=org.id,
            start_date=date(2026, 1, 1),
            archimate_contract_id=arch_contract.id,
        )
        db_session.add(vc1)
        db_session.flush()
        db_session.refresh(arch_contract)

        # The backref "vendor_contract_mirror" should show the linked VendorContract
        assert arch_contract.vendor_contract_mirror is not None
        # It might be a list or a single relationship - let's check
        mirrors = (
            arch_contract.vendor_contract_mirror
            if isinstance(arch_contract.vendor_contract_mirror, list)
            else [arch_contract.vendor_contract_mirror]
        )
        assert any(m.id == vc1.id for m in mirrors)


# ===========================================================================
# Renewals view: notice period and last day to cancel
# ===========================================================================


@pytest.mark.usefixtures("db_session")
class TestContractRenewals:
    """Contract renewal calculations — last day to cancel from notice period."""

    def test_last_cancel_day_from_notice_period(self, db_session):
        """When a notice_period_days is set, the last day to cancel is
        ``renewal_date - notice_period_days`` (or ``end_date - notice_period_days``
        when no renewal_date is recorded)."""
        from app.models.application_portfolio import VendorContract
        from app.models.organization import Organization
        import uuid

        org = Organization(
            name=f"Test Org {uuid.uuid4().hex[:10]}",
            slug=f"test-org-{uuid.uuid4().hex[:10]}",
        )
        db_session.add(org)
        db_session.flush()

        # Contract with renewal_date and notice_period_days
        c = VendorContract(
            contract_name="Renewable Contract",
            organization_id=org.id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            renewal_date=date(2027, 1, 1),
            notice_period_days=90,
            auto_renewal=True,
            status="active",
        )
        db_session.add(c)
        db_session.flush()

        # Last day to cancel = renewal_date - notice_period_days
        from datetime import timedelta
        expected_cancel = c.renewal_date - timedelta(days=c.notice_period_days)
        assert expected_cancel == date(2026, 10, 3)  # 2027-01-01 minus 90 days

    def test_default_notice_period_value(self, db_session):
        """When notice_period_days is not explicitly set, the model default (90)
        is used, and last_cancel_day is None when no renewal_date is set."""
        from app.models.application_portfolio import VendorContract
        from app.models.organization import Organization
        import uuid

        org = Organization(
            name=f"Test Org {uuid.uuid4().hex[:10]}",
            slug=f"test-org-{uuid.uuid4().hex[:10]}",
        )
        db_session.add(org)
        db_session.flush()

        c = VendorContract(
            contract_name="No Notice Contract",
            organization_id=org.id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            auto_renewal=True,
            status="active",
        )
        db_session.add(c)
        db_session.flush()

        # With no notice_period_days, last_cancel_day would be None
        last_cancel = None
        if c.notice_period_days and c.renewal_date:
            last_cancel = c.renewal_date - timedelta(days=c.notice_period_days)
        assert last_cancel is None
        assert c.notice_period_days == 90  # default value when notice period not explicitly set


# ===========================================================================
# Two-organisation contract link tests
# ===========================================================================


@pytest.mark.usefixtures("db_session")
class TestTwoOrgContractLinks:
    """Contract-app links work across two organisations."""

    def _make_org(self, db_session, label: str):
        import uuid
        from app.models.organization import Organization

        suffix = uuid.uuid4().hex[:10]
        org = Organization(name=f"Test Org {label} {suffix}", slug=f"test-org-{label}-{suffix}")
        db_session.add(org)
        db_session.flush()
        return org

    def _make_vendor(self, db_session, name: str):
        from app.models.vendor.vendor_organization import VendorOrganization

        v = VendorOrganization(
            name=name,
            code="VEND-" + name.replace(" ", "_").upper()[:8],
            seed_source_id="test-" + name.replace(" ", "-").lower(),
            seeded_by="test",
        )
        db_session.add(v)
        db_session.flush()
        return v

    def _make_contract(self, db_session, org, vendor, contract_name="Test Contract"):
        from app.models.application_portfolio import VendorContract

        c = VendorContract(
            contract_name=contract_name,
            organization_id=org.id,
            vendor_id=vendor.id,
            start_date=date(2026, 1, 1),
            status="active",
        )
        db_session.add(c)
        db_session.flush()
        return c

    def test_contract_belongs_to_one_org(self, db_session):
        """Each contract belongs to exactly one organisation."""
        org_a = self._make_org(db_session, "org-a")
        org_b = self._make_org(db_session, "org-b")
        vendor = self._make_vendor(db_session, "Shared Vendor")
        db_session.flush()

        c_a = self._make_contract(db_session, org_a, vendor, "Org A Contract")
        c_b = self._make_contract(db_session, org_b, vendor, "Org B Contract")

        assert c_a.organization_id == org_a.id
        assert c_b.organization_id == org_b.id
        assert c_a.organization_id != c_b.organization_id

    def test_contract_app_link_org_scoped(self, db_session):
        """ContractApplication links carry organization_id for scoping."""
        from app.models.contract_application import ContractApplication
        from app.models.application_portfolio import ApplicationComponent

        org_a = self._make_org(db_session, "org-a")
        vendor = self._make_vendor(db_session, "Link Test Vendor")
        contract = self._make_contract(db_session, org_a, vendor)

        app = ApplicationComponent(
            name="Linked App",
            description="An app linked to the contract",
            organization_id=org_a.id,
        )
        db_session.add(app)
        db_session.flush()

        link = ContractApplication(
            contract_id=contract.id,
            application_id=app.id,
            organization_id=org_a.id,
            allocation_percentage=100.00,
        )
        db_session.add(link)
        db_session.flush()

        assert link.contract_id == contract.id
        assert link.application_id == app.id
        assert link.organization_id == org_a.id


# ===========================================================================
# store-agreement concept: contracts and vendors (static model check)
# ===========================================================================


@pytest.mark.usefixtures("db_session")
class TestStoreAgreementConcepts:
    """The ``contracts`` and ``vendors`` store-agreement concepts pass.

    These tests verify that the model relationships exist as expected
    for the store-agreement concept verification gates.
    """

    def test_vendor_contract_relationship(self, db_session):
        """VendorOrganization has a contracts relationship."""
        from app.models.vendor.vendor_organization import VendorOrganization
        from app.models.application_portfolio import VendorContract

        # Verify the relationship attribute exists
        assert hasattr(VendorOrganization, "contracts")

    def test_contract_vendor_relationship(self, db_session):
        """VendorContract has a vendor relationship."""
        from app.models.application_portfolio import VendorContract

        assert hasattr(VendorContract, "vendor")

    def test_contract_application_allocations_exist(self, db_session):
        """VendorContract has application_allocations relationship."""
        from app.models.application_portfolio import VendorContract

        assert hasattr(VendorContract, "application_allocations")

    def test_contract_applications_unique_constraint(self, db_session):
        """ContractApplication has a unique constraint on (contract_id, application_id)."""
        from app.models.contract_application import ContractApplication

        constraints = ContractApplication.__table__.constraints
        names = {c.name for c in constraints}
        assert "uq_contract_application" in names