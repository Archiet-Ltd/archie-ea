"""Decision H — tests beyond C and E: the loader's upsert and projection
rules, tenant isolation, fabrication and licence checks (11)-(17)."""
from __future__ import annotations

from pathlib import Path

from app.models.reference_pack import ReferencePack
from app.modules.reference_packs.services.pack_files import load_pack_file, validate_pack
from app.modules.reference_packs.services.pack_loader import load_packs

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"

# The 38 element names the four deleted vendor-template-group lists held,
# copied here (before their deletion) so this test proves the projection
# still carries every one of them after the fold — 10 SAP + 11 Dynamics 365 +
# 9 Power Platform + 8 Salesforce.
LEGACY_TEMPLATE_ELEMENT_NAMES = {
    "SAP": [
        "SAP S/4HANA Application Server",
        "SAP HANA Primary Database",
        "SAP Business Technology Platform",
        "SAP Gateway",
        "SAP Fiori Launchpad",
        "SAP Integration Suite",
        "SAP Event Mesh",
        "SAP Web Dispatcher",
        "SAP HANA Secondary Database",
        "SAP Fiori Frontend Server",
    ],
    "MICROSOFT_DYNAMICS": [
        "Microsoft Dynamics 365 Application Server",
        "Azure SQL Database",
        "Azure Active Directory",
        "Dynamics 365 Finance Module",
        "Dynamics 365 SCM Module",
        "Azure API Management",
        "Dynamics 365 Customer Engagement",
        "Azure Service Bus",
        "Azure Key Vault",
        "Azure Monitor",
        "Power Platform Environment",
    ],
    "MICROSOFT_POWER": [
        "Power Platform Environment",
        "Dataverse Instance",
        "Azure Active Directory",
        "Power Apps Service",
        "Power Automate Service",
        "Power BI Service",
        "Azure API Management",
        "On-Premises Data Gateway",
        "Azure Key Vault",
    ],
    "SALESFORCE": [
        "Salesforce Core Platform",
        "Salesforce Lightning Experience",
        "Salesforce Identity and SSO",
        "Salesforce REST and Bulk API",
        "Salesforce Event Bus",
        "Salesforce Einstein",
        "Salesforce Data Cloud",
        "Heroku Runtime",
    ],
}
assert sum(len(v) for v in LEGACY_TEMPLATE_ELEMENT_NAMES.values()) == 38


class TestLoadPacksOnTheThirteenFiles:
    """(11): thirteen rows, idempotent second run (FR-P3)."""

    def test_first_load_publishes_all_thirteen(self, app, db_session):
        report = load_packs()
        assert report.errors == [], report.errors
        assert report.packs_loaded == 13
        assert report.packs_skipped_unchanged == 0
        count = ReferencePack.query.filter_by(status="published").count()
        assert count == 13

    def test_second_load_is_idempotent(self, app, db_session):
        load_packs()
        before = ReferencePack.query.count()
        report = load_packs()
        assert report.packs_loaded == 0
        assert report.packs_skipped_unchanged == 13
        assert ReferencePack.query.count() == before

    def test_dry_run_writes_no_row_and_still_reports(self, app, db_session):
        """(17): --dry-run writes no row and prints the report."""
        report = load_packs(dry_run=True)
        assert report.packs_loaded == 13
        assert ReferencePack.query.count() == 0


class TestProjectionAfterLoad:
    """(12): the projection keeps every legacy name with element_id IS NULL,
    populate_from_vendor still runs, and a manually linked element_id
    survives a second load."""

    def test_legacy_vendor_keys_projected_with_all_names(self, app, db_session):
        from app.models.vendor.vendor_organization import VendorArchiMateTemplate

        load_packs()
        for vendor_key, names in LEGACY_TEMPLATE_ELEMENT_NAMES.items():
            rows = VendorArchiMateTemplate.query.filter_by(vendor_key=vendor_key).all()
            row_by_name = {r.element_name: r for r in rows}
            missing = set(names) - set(row_by_name)
            assert not missing, f"{vendor_key} missing projected names: {missing}"
            for name in names:
                assert row_by_name[name].element_id is None, (
                    f"{vendor_key}.{name} should have element_id IS NULL after the loader"
                )

    def test_populate_from_vendor_runs_green_against_sap(self, app, db_session, make_org, tenant_ctx):
        from app.models.vendor.vendor_organization import VendorOrganization, VendorProduct
        from app.modules.solutions_strategic.v2.services.vendor_template_service import (
            VendorTemplateService,
        )

        load_packs()

        vendor_org = VendorOrganization.query.filter(
            VendorOrganization.name.ilike("%SAP%")
        ).first()
        if vendor_org is None:
            vendor_org = VendorOrganization(name="SAP")
            db_session.add(vendor_org)
            db_session.flush()

        vp = VendorProduct(vendor_organization_id=vendor_org.id, name="SAP S/4HANA (test)")
        db_session.add(vp)
        db_session.flush()

        org = make_org("pack-loader-populate")
        with tenant_ctx(org.id):
            # The projection's rows all have element_id IS NULL (the loader
            # never links a real ArchiMate element), so every template is
            # skipped by design — "runs green" means it resolves the vendor
            # key, finds the templates, and returns cleanly rather than
            # raising, not that it links rows with no target element.
            result = VendorTemplateService.populate_from_vendor(
                solution_id=-1, vendor_product_id=vp.id, user_id=1
            )
        assert result["linked"] == 0
        assert result["skipped"] >= len(LEGACY_TEMPLATE_ELEMENT_NAMES["SAP"])

    def test_projection_row_given_an_element_id_survives_a_second_load(
        self, app, db_session, make_org, tenant_ctx
    ):
        from app.models import ArchiMateElement
        from app.models.vendor.vendor_organization import VendorArchiMateTemplate
        from app.modules.reference_packs.services.pack_files import DEFAULT_ROOT
        from app.modules.reference_packs.services.pack_loader import refresh_projection

        load_packs()
        row = VendorArchiMateTemplate.query.filter_by(
            vendor_key="SAP", element_name="SAP Gateway"
        ).first()
        assert row is not None

        # A real link sets element_id to a genuine archimate_elements row —
        # create one so the FK constraint holds, then prove the loader's
        # upsert path leaves a non-NULL element_id untouched on a second run.
        org = make_org("pack-loader-elem-id")
        with tenant_ctx(org.id):
            elem = ArchiMateElement(name="SAP Gateway (linked)", type="ApplicationComponent", layer="Application")
            db_session.add(elem)
            db_session.flush()
            row.element_id = elem.id
            db_session.flush()

            sap_path = DEFAULT_ROOT / "sap-s4hana" / "2025.1.yml"
            pack = load_pack_file(sap_path)
            assert validate_pack(pack) == []

            refresh_projection(pack)
            db_session.flush()
            reloaded = VendorArchiMateTemplate.query.filter_by(
                vendor_key="SAP", element_name="SAP Gateway"
            ).first()
            assert reloaded.element_id == elem.id


class TestTenantIsolation:
    """(13): packs are global — no organisation column, no tenant predicate."""

    def test_reference_pack_has_no_organization_id(self):
        assert not hasattr(ReferencePack, "organization_id")

    def test_two_tenants_load_byte_identical_rows(self, app, db_session, make_org, tenant_ctx):
        org_a = make_org("pack-tenant-a")
        org_b = make_org("pack-tenant-b")

        with tenant_ctx(org_a.id):
            load_packs()
            rows_a = {
                (r.pack_key, r.pack_version): r.content_hash
                for r in ReferencePack.query.filter_by(status="published").all()
            }

        with tenant_ctx(org_b.id):
            report_b = load_packs()
            rows_b = {
                (r.pack_key, r.pack_version): r.content_hash
                for r in ReferencePack.query.filter_by(status="published").all()
            }

        assert rows_a == rows_b
        assert report_b.packs_skipped_unchanged == 13

    def test_read_from_inside_a_tenant_context_returns_all_thirteen(
        self, app, db_session, make_org, tenant_ctx
    ):
        load_packs()
        org = make_org("pack-tenant-read")
        with tenant_ctx(org.id):
            rows = ReferencePack.query.filter_by(status="published").all()
        assert len(rows) == 13


class TestFabrication:
    """(14): the loader creates no ArchiMate model rows, and every loaded
    pack's content still passes the forbidden-key check (C item 6)."""

    def test_load_creates_no_archimate_elements_or_relationships(self, app, db_session):
        from app.models import ArchiMateElement, ArchiMateRelationship

        before_elements = ArchiMateElement.query.count()
        before_relationships = ArchiMateRelationship.query.count()
        load_packs()
        assert ArchiMateElement.query.count() == before_elements
        assert ArchiMateRelationship.query.count() == before_relationships

    def test_loaded_content_still_passes_the_forbidden_key_check(self, app, db_session):
        from app.modules.reference_packs.services.pack_files import FORBIDDEN_KEYS

        load_packs()
        for record in ReferencePack.query.filter_by(status="published").all():
            content = record.content
            for element in content.get("elements", []):
                assert not (set(element.keys()) & FORBIDDEN_KEYS), (
                    record.pack_key,
                    element.get("element_key"),
                )
                assert not (set((element.get("properties") or {}).keys()) & FORBIDDEN_KEYS)


class TestLicence:
    """(15): decision C items 5, 7 and 9 on all thirteen files; a fixture
    pack with a missing licence fails item 5 naming the pack key."""

    def test_all_thirteen_files_pass_licence_checks(self, app, db_session):
        report = load_packs()
        assert report.errors == []

    def test_fixture_with_missing_licence_fails_naming_the_pack_key(self):
        broken = load_pack_file(FIXTURES_ROOT / "fixture-broken" / "1.0.yml")
        errors = validate_pack(broken)
        licence_errors = [e for e in errors if "licence" in e]
        assert licence_errors, errors
        assert all(e.startswith("fixture-broken:") for e in licence_errors)


class TestMatrixRefusal:
    """(16): a fixture pack whose relationship the matrix refuses fails
    item 2 naming the triple."""

    def test_fixture_with_a_refused_relationship_names_the_triple(self):
        broken = load_pack_file(FIXTURES_ROOT / "fixture-broken" / "1.0.yml")
        errors = validate_pack(broken)
        matrix_errors = [e for e in errors if "refused by the matrix" in e]
        assert matrix_errors, errors
        assert any(
            "application.broken-core" in e and "strategy.broken-capability" in e and "realization" in e
            for e in matrix_errors
        )


class TestValidFixtureIsAPositiveControl:
    def test_valid_fixture_has_no_errors(self):
        valid = load_pack_file(FIXTURES_ROOT / "fixture-valid" / "1.0.yml")
        assert validate_pack(valid) == []
