"""Tests for the per-organisation domain-element backfill command.

Creates missing ArchiMate elements for every domain record type in one
organisation. Must persist what it reports (fresh sessions see the elements
and the back-links), isolate one bad row per record (a savepoint, not a whole
batch rollback), write elements through the same helper the flush listener
uses, and never silently remove elements.
"""

import pytest

from datetime import date

from app.models.archimate_core import ArchiMateElement
from app.models.application_portfolio import ApplicationComponent
from app.models.business_capabilities import BusinessCapability
from app.models.risk import Risk


def _clear_element(db_session, record):
    """Remove the auto-created ArchiMateElement from a record and flush."""
    if record.archimate_element_id:
        el = db_session.get(ArchiMateElement, record.archimate_element_id)
        if el:
            record.archimate_element_id = None
            db_session.flush()
            db_session.delete(el)
            db_session.flush()


def _seed_orgs(app, labels):
    """Create one organisation per label, really committed.

    The persistence tests below cannot use the db_session fixture: they must
    prove the command's work is visible to a fresh session on another
    connection, which requires a real commit of the seed as well.
    """
    from app import db
    from app.models.organization import Organization
    import uuid

    suffix = uuid.uuid4().hex[:8]
    orgs = {}
    with app.app_context():
        for label in labels:
            org = Organization(name=f"Test {label} {suffix}", slug=f"test-{label}-{suffix}")
            db.session.add(org)
            orgs[label] = org
        db.session.commit()
        return {label: org.id for label, org in orgs.items()}


def _seed_contract(app, org_id, contract_name, description=None):
    """Insert a VendorContract with plain SQL.

    The SQL insert bypasses the model's before_insert element mirror, so the
    row reaches the database without an element -- exactly the legacy data the
    backfill exists for. Really committed.
    """
    from app import db
    from sqlalchemy import text

    with app.app_context():
        row_id = db.session.execute(
            text(
                """
                INSERT INTO vendor_contracts
                    (contract_name, contract_description, start_date, organization_id)
                VALUES
                    (:name, :description, '2026-01-01', :org_id)
                RETURNING id
                """
            ),
            {"name": contract_name, "description": description, "org_id": org_id},
        ).scalar()
        db.session.commit()
        return row_id


def _seed_interface(app, org_id, name, interface_type, description=None):
    """Insert an ApplicationInterface with plain SQL (no element, needs backfill)."""
    from app import db
    from sqlalchemy import text

    with app.app_context():
        row_id = db.session.execute(
            text(
                """
                INSERT INTO application_interfaces
                    (name, interface_type, description, organization_id)
                VALUES
                    (:name, :interface_type, :description, :org_id)
                RETURNING id
                """
            ),
            {
                "name": name,
                "interface_type": interface_type,
                "description": description,
                "org_id": org_id,
            },
        ).scalar()
        db.session.commit()
        return row_id


def _seed_capability(app, org_id, name, description=None):
    """Insert a BusinessCapability with plain SQL (no element, needs backfill)."""
    from app import db
    from sqlalchemy import text

    with app.app_context():
        row_id = db.session.execute(
            text(
                """
                INSERT INTO business_capability
                    (name, level, description, organization_id)
                VALUES
                    (:name, 1, :description, :org_id)
                RETURNING id
                """
            ),
            {"name": name, "description": description, "org_id": org_id},
        ).scalar()
        db.session.commit()
        return row_id


def _seed_application(app, org_id, name, description=None):
    """Insert an ApplicationComponent with plain SQL (no element, needs backfill)."""
    from app import db
    from sqlalchemy import text

    with app.app_context():
        row_id = db.session.execute(
            text(
                """
                INSERT INTO application_components
                    (name, description, organization_id)
                VALUES
                    (:name, :description, :org_id)
                RETURNING id
                """
            ),
            {"name": name, "description": description, "org_id": org_id},
        ).scalar()
        db.session.commit()
        return row_id


def _seed_data_domain_and_maybe_entity(app, org_id, domain_name, entity_name=None, entity_description=None):
    """Insert a DataDomain (and optionally a DataEntity) with plain SQL.

    Returns ``(domain_id, entity_id_or_None)``. Rows written this way bypass
    the flush listener, so a returned entity has no element and needs the
    backfill.
    """
    from app import db
    from sqlalchemy import text

    with app.app_context():
        domain_id = db.session.execute(
            text(
                """
                INSERT INTO data_domains (name, organization_id)
                VALUES (:name, :org_id)
                RETURNING id
                """
            ),
            {"name": domain_name, "org_id": org_id},
        ).scalar()
        entity_id = None
        if entity_name is not None:
            entity_id = db.session.execute(
                text(
                    """
                    INSERT INTO data_entities (name, domain_id, description, organization_id)
                    VALUES (:name, :domain_id, :description, :org_id)
                    RETURNING id
                    """
                ),
                {
                    "name": entity_name,
                    "domain_id": domain_id,
                    "description": entity_description,
                    "org_id": org_id,
                },
            ).scalar()
        db.session.commit()
        return domain_id, entity_id


def _cleanup(app, org_ids):
    """Delete everything the app-context tests created, organisation by organisation."""
    from app import db
    from app.models.organization import Organization
    from app.services.org_deletion_service import OrganizationDeletionService

    with app.app_context():
        for org_id in org_ids:
            if db.session.get(Organization, org_id) is not None:
                OrganizationDeletionService.delete_organization(org_id)


def test_backfill_creates_missing_elements(db_session, make_org, tenant_ctx):
    """Records without an element get one created."""
    from app.commands.backfill_domain_elements import backfill_domain_elements

    org = make_org("bf-app")
    with tenant_ctx(org.id):
        # Create an app via ORM, then delete the element to simulate missing
        app = ApplicationComponent(
            name="Backfilled App", organization_id=org.id
        )
        db_session.add(app)
        db_session.flush()
        _clear_element(db_session, app)

        stats = backfill_domain_elements(org.id, session=db_session)
        app_stats = stats.get("ApplicationComponent", {})
        assert app_stats.get("created", 0) >= 1

        db_session.expire_all()
        app = db_session.query(ApplicationComponent).filter(
            ApplicationComponent.name == "Backfilled App"
        ).first()
        assert app is not None
        assert app.archimate_element_id is not None


def test_backfill_is_idempotent(db_session, make_org, tenant_ctx):
    """Running backfill again on a fully-linked org creates nothing."""
    from app.commands.backfill_domain_elements import backfill_domain_elements

    org = make_org("bf-idem")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="Idempotent App", organization_id=org.id
        )
        db_session.add(app)
        db_session.flush()
        _clear_element(db_session, app)

        stats1 = backfill_domain_elements(org.id, session=db_session)
        assert stats1.get("ApplicationComponent", {}).get("created", 0) >= 1

        db_session.expire_all()

        stats2 = backfill_domain_elements(org.id, session=db_session)
        assert stats2.get("ApplicationComponent", {}).get("created", 0) == 0


def test_backfill_respects_tenant_boundary(db_session, make_org, tenant_ctx):
    """Backfill for org A must not create elements in org B."""
    from app.commands.backfill_domain_elements import backfill_domain_elements

    org_a = make_org("bf-iso-a")
    org_b = make_org("bf-iso-b")

    with tenant_ctx(org_a.id):
        app_a = ApplicationComponent(
            name="App in A", organization_id=org_a.id
        )
        db_session.add(app_a)
        db_session.flush()
        _clear_element(db_session, app_a)

    with tenant_ctx(org_b.id):
        app_b = ApplicationComponent(
            name="App in B", organization_id=org_b.id
        )
        db_session.add(app_b)
        db_session.flush()
        _clear_element(db_session, app_b)

    # Backfill only org A — run inside org A's tenant context so the
    # automatic tenant filter does not scope the query to org B
    with tenant_ctx(org_a.id):
        backfill_domain_elements(org_a.id, session=db_session)

    # Check that only org A's records got elements
    db_session.expire_all()
    a_apps = db_session.query(ApplicationComponent).filter(
        ApplicationComponent.organization_id == org_a.id,
        ApplicationComponent.archimate_element_id.isnot(None),
    ).count()
    b_apps = db_session.query(ApplicationComponent).filter(
        ApplicationComponent.organization_id == org_b.id,
        ApplicationComponent.archimate_element_id.isnot(None),
    ).count()

    assert a_apps >= 1, "org A records should get elements"
    assert b_apps == 0, "org B records must NOT get elements from org A's backfill"


def test_backfill_dry_run_changes_nothing(db_session, make_org, tenant_ctx):
    """--dry-run reports counts without creating any elements."""
    from app.commands.backfill_domain_elements import backfill_domain_elements

    org = make_org("bf-dry")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="Dry Run App", organization_id=org.id
        )
        db_session.add(app)
        db_session.flush()
        _clear_element(db_session, app)

        stats = backfill_domain_elements(org.id, dry_run=True, session=db_session)
        assert stats.get("ApplicationComponent", {}).get("created", 0) == 0
        assert stats.get("ApplicationComponent", {}).get("scanned", 0) >= 1

        db_session.expire_all()
        app = db_session.query(ApplicationComponent).filter(
            ApplicationComponent.name == "Dry Run App"
        ).first()
        assert app is not None
        assert app.archimate_element_id is None


def test_backfill_dry_run_cli_exits_zero_and_prints_total(app):
    """The CLI --dry-run flag exits 0 and prints TOTAL in the output."""
    from click.testing import CliRunner

    from app.commands.backfill_domain_elements import (
        backfill_domain_elements_command,
    )
    from app.models.application_portfolio import VendorContract

    orgs = _seed_orgs(app, ["bf-cli-dry"])
    org_id = orgs["bf-cli-dry"]
    try:
        with app.app_context():
            _seed_contract(app, org_id, "CLI Dry Run Target")

        runner = CliRunner()
        result = runner.invoke(
            backfill_domain_elements_command,
            ["--org-id", str(org_id), "--dry-run"],
        )
        assert result.exit_code == 0, (
            f"CLI dry-run exited {result.exit_code}: {result.output}"
        )
        assert "TOTAL" in result.output

        # Fresh session: nothing was written.
        with app.app_context():
            from app import db
            from app.models.archimate_core import ArchiMateElement

            elements = db.session.query(ArchiMateElement).filter(
                ArchiMateElement.organization_id == org_id,
            ).count()
            assert elements == 0, "dry run must not create any elements"
    finally:
        _cleanup(app, [org_id])


def test_backfill_handles_multiple_types(db_session, make_org, tenant_ctx):
    """Backfill creates missing elements for multiple domain record types."""
    from app.commands.backfill_domain_elements import backfill_domain_elements

    org = make_org("bf-multi")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="Multi App", organization_id=org.id
        )
        db_session.add(app)
        db_session.flush()
        _clear_element(db_session, app)

        risk = Risk(
            title="Multi Risk", organization_id=org.id,
            likelihood=3, impact=3
        )
        db_session.add(risk)
        db_session.flush()
        _clear_element(db_session, risk)

        cap = BusinessCapability(
            name="Multi Cap", organization_id=org.id, level=1
        )
        db_session.add(cap)
        db_session.flush()
        _clear_element(db_session, cap)

        stats = backfill_domain_elements(org.id, session=db_session)

        total_created = sum(s.get("created", 0) for s in stats.values())
        assert total_created >= 3


def test_every_record_type_has_a_handler():
    """The backfill command must list every domain record type we know about."""
    from app.commands.backfill_domain_elements import _DOMAIN_TYPES

    expected_types = {
        "ApplicationComponent",
        "BusinessCapability",
        "Risk",
        "VendorContract",
        "ApplicationInterface",
        "DataEntity",
        "WorkPackage",
        "ApplicationComplianceControl",
    }
    actual_types = {path.rsplit(".", 1)[1] for path in _DOMAIN_TYPES}
    assert expected_types == actual_types, (
        f"backfill must handle all domain types. "
        f"Missing: {expected_types - actual_types}. "
        f"Extra: {actual_types - expected_types}."
    )


def test_backfill_reports_duplicates(db_session, make_org, tenant_ctx):
    """Orphan ArchiMateElement records with the same name are reported as duplicates."""
    from app.commands.backfill_domain_elements import backfill_domain_elements

    org = make_org("bf-dup")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="Dup App", organization_id=org.id
        )
        db_session.add(app)
        db_session.flush()
        _clear_element(db_session, app)

        # Create orphan elements (same name, type, org, not linked to any record)
        el1 = ArchiMateElement(
            name="Orphan Duplicate", type="ApplicationComponent",
            layer="Application", organization_id=org.id,
        )
        db_session.add(el1)
        db_session.flush()
        el2 = ArchiMateElement(
            name="Orphan Duplicate", type="ApplicationComponent",
            layer="Application", organization_id=org.id,
        )
        db_session.add(el2)
        db_session.flush()

        stats = backfill_domain_elements(org.id, session=db_session)
        app_stats = stats.get("ApplicationComponent", {})
        dups = app_stats.get("duplicates", [])
        assert len(dups) >= 1, "should report duplicate orphan elements"
        dup = dups[0]
        assert dup["name"] == "Orphan Duplicate"
        assert dup["kept_id"] in (el1.id, el2.id)
        assert len(dup["surplus_ids"]) == 1
        assert dup["kept_id"] == min(el1.id, el2.id)
        assert dup["surplus_ids"] == [max(el1.id, el2.id)]


def test_backfill_reports_duplicates_with_kept_and_surplus_ids(
    db_session, make_org, tenant_ctx,
):
    """Duplicate report includes kept_id and surplus_ids for each group."""
    from app.commands.backfill_domain_elements import backfill_domain_elements

    org = make_org("bf-dup2")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="Dup App 2", organization_id=org.id
        )
        db_session.add(app)
        db_session.flush()
        _clear_element(db_session, app)

        # Three orphans with the same name
        ids = []
        for _ in range(3):
            el = ArchiMateElement(
                name="Triple Orphan", type="ApplicationComponent",
                layer="Application", organization_id=org.id,
            )
            db_session.add(el)
            db_session.flush()
            ids.append(el.id)

        stats = backfill_domain_elements(org.id, session=db_session)
        app_stats = stats.get("ApplicationComponent", {})
        dups = app_stats.get("duplicates", [])
        assert len(dups) >= 1
        dup = dups[0]
        assert dup["kept_id"] == min(ids)
        assert sorted(dup["surplus_ids"]) == sorted(ids[1:])


def test_backfill_commits_and_fresh_session_sees_elements_and_links(app):
    """The command persists its work: elements and back-links are visible to a
    session created after the run, from outside the session the command used
    (fails on the no-commit revision: zero persisted)."""
    from sqlalchemy import text

    from app import db
    from app.commands.backfill_domain_elements import backfill_domain_elements
    from app.models.application_portfolio import VendorContract

    orgs = _seed_orgs(app, ["bf-e2e-a", "bf-e2e-b"])
    try:
        with app.app_context():
            _seed_contract(app, orgs["bf-e2e-a"], "End to End A", "Desc A")
            _seed_contract(app, orgs["bf-e2e-b"], "End to End B", "Desc B")

            stats = backfill_domain_elements(orgs["bf-e2e-a"])
            assert stats["VendorContract"]["created"] == 1

            # Fresh query outside the session the command ran in.
            fresh = db.session.session_factory()
            try:
                a_row = fresh.query(VendorContract).filter_by(
                    organization_id=orgs["bf-e2e-a"],
                ).one()
                assert a_row.archimate_element_id is not None
                element = fresh.get(ArchiMateElement, a_row.archimate_element_id)
                assert element is not None
                assert element.organization_id == orgs["bf-e2e-a"]
                assert element.name == "End to End A"
                assert element.type == "Contract"

                # The other organisation's row is never returned, changed or named.
                b_row = fresh.query(VendorContract).filter_by(
                    organization_id=orgs["bf-e2e-b"],
                ).one()
                assert b_row.archimate_element_id is None
                other_elements = fresh.query(ArchiMateElement).filter_by(
                    organization_id=orgs["bf-e2e-b"],
                ).count()
                assert other_elements == 0
            finally:
                fresh.close()
    finally:
        _cleanup(app, list(orgs.values()))


def test_backfill_one_bad_row_does_not_discard_the_batch(app):
    """One unrepresentable row (no name to mirror) is rolled back on its own
    and counted as an error; every good row before and after it is persisted.
    Counts reflect the persisted rows, not the attempted ones (fails on the
    per-record full-rollback revision: zero persisted)."""
    from app import db
    from app.commands.backfill_domain_elements import backfill_domain_elements
    from app.models.application_portfolio import VendorContract

    orgs = _seed_orgs(app, ["bf-batch-a", "bf-batch-b"])
    try:
        with app.app_context():
            _seed_contract(app, orgs["bf-batch-a"], "Batch Good A")
            _seed_contract(app, orgs["bf-batch-a"], "")
            _seed_contract(app, orgs["bf-batch-a"], "Batch Good B")
            _seed_contract(app, orgs["bf-batch-b"], "Batch Other Org")

            stats = backfill_domain_elements(orgs["bf-batch-a"])
            vc = stats["VendorContract"]
            assert vc["created"] == 2, "good rows only"
            assert vc["errors"] == 1, "the unnamed row is an error, not a silent skip"
            assert vc["duplicates"] == []
            # Error details must include the unnamed row's id.
            assert len(vc["error_details"]) == 1
            err_id, err_reason = vc["error_details"][0]
            assert err_id is not None, "error details must include the record id"
            assert "name" in err_reason.lower() or "title" in err_reason.lower(), (
                "error reason must explain why the row failed"
            )

            fresh = db.session.session_factory()
            try:
                rows = {
                    r.contract_name: r
                    for r in fresh.query(VendorContract).all()
                }
                assert rows["Batch Good A"].archimate_element_id is not None
                assert rows["Batch Good B"].archimate_element_id is not None
                assert rows[""].archimate_element_id is None

                group = fresh.query(ArchiMateElement).filter(
                    ArchiMateElement.organization_id == orgs["bf-batch-a"],
                ).all()
                assert len(group) == 2, "persisted rows only"

                # The bad batch in org A never touches org B's rows.
                assert fresh.query(ArchiMateElement).filter_by(
                    organization_id=orgs["bf-batch-b"],
                ).count() == 0
            finally:
                fresh.close()
    finally:
        _cleanup(app, list(orgs.values()))


def test_backfill_uses_the_same_writer_as_a_listener_created_element(
    db_session, make_org, tenant_ctx,
):
    """A backfilled element carries the same name fallback, description and
    provenance property as the element the shared writer -- the helper the
    flush listener uses -- produces for an identical row (fails on the
    fallback-writer revision)."""
    from app.commands.backfill_domain_elements import backfill_domain_elements
    from app.models.application_portfolio import ApplicationComponent
    from app.services.archimate_backbone import sync_archimate_element

    org = make_org("bf-one-writer")
    with tenant_ctx(org.id):
        a = ApplicationComponent(
            name="Writer", description="Shared description", organization_id=org.id,
        )
        b = ApplicationComponent(
            name="Writer", description="Shared description", organization_id=org.id,
        )
        db_session.add_all([a, b])
        db_session.flush()
        _clear_element(db_session, a)
        _clear_element(db_session, b)

        # Reference element: created by the one writer the flush listener uses.
        reference = sync_archimate_element(a, session=db_session)
        assert reference is not None

        stats = backfill_domain_elements(org.id, session=db_session)
        assert stats["ApplicationComponent"]["created"] == 1

        backfilled = db_session.get(ArchiMateElement, b.archimate_element_id)
        assert backfilled is not None
        assert backfilled.name == reference.name == "Writer"
        assert backfilled.description == reference.description == "Shared description"
        assert backfilled.type == reference.type == "ApplicationComponent"
        assert backfilled.layer == reference.layer == "Application"
        assert backfilled.organization_id == reference.organization_id == org.id
        assert backfilled.custom_properties == reference.custom_properties
        assert backfilled.custom_properties == {"source_model": "ApplicationComponent"}


def test_backfill_vendor_contract_parity(app):
    """A VendorContract created through the ORM and one seeded with plain SQL
    produce elements with equal description and equal provenance properties
    after backfill (fails when the per-model raw-insert listener is still
    active: the ORM-created row gets a different description format)."""
    from app import db
    from app.commands.backfill_domain_elements import backfill_domain_elements
    from app.models.application_portfolio import VendorContract
    from app.services.archimate_backbone import sync_archimate_element

    orgs = _seed_orgs(app, ["bf-vc-parity"])
    org_id = orgs["bf-vc-parity"]
    try:
        with app.app_context():
            # ORM-created contract: goes through the flush listener.
            orm_contract = VendorContract(
                contract_name="ORM Contract",
                contract_description="Description from ORM",
                organization_id=org_id,
                start_date=date(2026, 1, 1),
            )
            db.session.add(orm_contract)
            db.session.flush()
            orm_element_id = orm_contract.archimate_element_id
            assert orm_element_id is not None

            # SQL-seeded contract: no element, needs backfill.
            _seed_contract(app, org_id, "SQL Contract", "Description from SQL")

            # Backfill creates an element for the SQL-seeded row.
            stats = backfill_domain_elements(org_id)
            assert stats["VendorContract"]["created"] == 1

            fresh = db.session.session_factory()
            try:
                orm_row = fresh.query(VendorContract).filter_by(
                    contract_name="ORM Contract",
                ).one()
                sql_row = fresh.query(VendorContract).filter_by(
                    contract_name="SQL Contract",
                ).one()

                orm_el = fresh.get(ArchiMateElement, orm_row.archimate_element_id)
                sql_el = fresh.get(ArchiMateElement, sql_row.archimate_element_id)

                assert orm_el is not None
                assert sql_el is not None

                # Same description format (both use the shared writer).
                assert orm_el.description == "Description from ORM"
                assert sql_el.description == "Description from SQL"

                # Same provenance property.
                assert orm_el.custom_properties == {"source_model": "VendorContract"}
                assert sql_el.custom_properties == {"source_model": "VendorContract"}
            finally:
                fresh.close()
    finally:
        _cleanup(app, [org_id])


def test_backfill_application_interface_parity(app):
    """An ApplicationInterface created through the ORM and one seeded with
    plain SQL produce elements with equal description and equal provenance
    properties after backfill (fails when the shared writer is not the one
    used for one of the two creation paths)."""
    from app import db
    from app.commands.backfill_domain_elements import backfill_domain_elements
    from app.models.application_layer import ApplicationInterface

    orgs = _seed_orgs(app, ["bf-ai-parity"])
    org_id = orgs["bf-ai-parity"]
    try:
        with app.app_context():
            # ORM-created interface: goes through the flush listener.
            orm_interface = ApplicationInterface(
                name="ORM Interface",
                interface_type="REST",
                description="Shared interface description",
                organization_id=org_id,
            )
            db.session.add(orm_interface)
            db.session.flush()
            orm_element_id = orm_interface.archimate_element_id
            assert orm_element_id is not None

            # SQL-seeded interface: no element, needs backfill.
            _seed_interface(
                app, org_id, "SQL Interface", "REST", "Shared interface description"
            )

            stats = backfill_domain_elements(org_id)
            assert stats["ApplicationInterface"]["created"] == 1

            fresh = db.session.session_factory()
            try:
                orm_row = fresh.query(ApplicationInterface).filter_by(
                    name="ORM Interface",
                ).one()
                sql_row = fresh.query(ApplicationInterface).filter_by(
                    name="SQL Interface",
                ).one()

                orm_el = fresh.get(ArchiMateElement, orm_row.archimate_element_id)
                sql_el = fresh.get(ArchiMateElement, sql_row.archimate_element_id)

                assert orm_el is not None
                assert sql_el is not None

                # Equal description and provenance: both paths use the shared
                # writer, so the two elements carry identical metadata.
                assert orm_el.description == sql_el.description == "Shared interface description"
                assert orm_el.custom_properties == {"source_model": "ApplicationInterface"}
                assert sql_el.custom_properties == {"source_model": "ApplicationInterface"}
                assert orm_el.type == sql_el.type == "ApplicationInterface"
                assert orm_el.layer == sql_el.layer == "Application"
            finally:
                fresh.close()
    finally:
        _cleanup(app, [org_id])


def test_backfill_business_capability_parity(app):
    """A BusinessCapability created through the ORM and one seeded with plain
    SQL produce elements with equal description and equal provenance properties
    after backfill (fails when the shared writer is not the one used for one
    of the two creation paths)."""
    from app import db
    from app.commands.backfill_domain_elements import backfill_domain_elements
    from app.models.business_capabilities import BusinessCapability

    orgs = _seed_orgs(app, ["bf-bc-parity"])
    org_id = orgs["bf-bc-parity"]
    try:
        with app.app_context():
            # ORM-created capability: goes through the flush listener.
            orm_capability = BusinessCapability(
                name="ORM Capability",
                description="Shared capability description",
                organization_id=org_id,
            )
            db.session.add(orm_capability)
            db.session.flush()
            orm_element_id = orm_capability.archimate_element_id
            assert orm_element_id is not None

            # SQL-seeded capability: no element, needs backfill.
            _seed_capability(app, org_id, "SQL Capability", "Shared capability description")

            stats = backfill_domain_elements(org_id)
            assert stats["BusinessCapability"]["created"] == 1

            fresh = db.session.session_factory()
            try:
                orm_row = fresh.query(BusinessCapability).filter_by(
                    name="ORM Capability",
                ).one()
                sql_row = fresh.query(BusinessCapability).filter_by(
                    name="SQL Capability",
                ).one()

                orm_el = fresh.get(ArchiMateElement, orm_row.archimate_element_id)
                sql_el = fresh.get(ArchiMateElement, sql_row.archimate_element_id)

                assert orm_el is not None
                assert sql_el is not None

                # Equal description and provenance: both paths use the shared
                # writer, so the two elements carry identical metadata.
                assert orm_el.description == sql_el.description == "Shared capability description"
                assert orm_el.custom_properties == {"source_model": "BusinessCapability"}
                assert sql_el.custom_properties == {"source_model": "BusinessCapability"}
                assert orm_el.type == sql_el.type == "Capability"
                assert orm_el.layer == sql_el.layer == "Strategy"
            finally:
                fresh.close()
    finally:
        _cleanup(app, [org_id])


def test_backfill_data_entity_parity(app):
    """A DataEntity created through the ORM and one seeded with plain SQL
    produce elements with equal description and equal provenance properties
    after backfill (fails when the shared writer is not the one used for one
    of the two creation paths)."""
    from app import db
    from app.commands.backfill_domain_elements import backfill_domain_elements
    from app.models.process_data import DataDomain, DataEntity

    orgs = _seed_orgs(app, ["bf-de-parity"])
    org_id = orgs["bf-de-parity"]
    try:
        with app.app_context():
            # ORM-created entity (and its domain): the entity goes through the
            # flush listener; a DataDomain is not one of the element types.
            orm_domain = DataDomain(
                name="ORM Domain", organization_id=org_id,
            )
            db.session.add(orm_domain)
            db.session.flush()
            orm_entity = DataEntity(
                name="ORM Entity",
                domain_id=orm_domain.id,
                description="Shared entity description",
                organization_id=org_id,
            )
            db.session.add(orm_entity)
            db.session.flush()
            orm_element_id = orm_entity.archimate_element_id
            assert orm_element_id is not None

            # SQL-seeded domain + entity: no element, needs backfill.
            _seed_data_domain_and_maybe_entity(
                app, org_id, "SQL Domain", "SQL Entity", "Shared entity description"
            )

            stats = backfill_domain_elements(org_id)
            assert stats["DataEntity"]["created"] == 1

            fresh = db.session.session_factory()
            try:
                orm_row = fresh.query(DataEntity).filter_by(name="ORM Entity").one()
                sql_row = fresh.query(DataEntity).filter_by(name="SQL Entity").one()

                orm_el = fresh.get(ArchiMateElement, orm_row.archimate_element_id)
                sql_el = fresh.get(ArchiMateElement, sql_row.archimate_element_id)

                assert orm_el is not None
                assert sql_el is not None

                # Equal description and provenance: both paths use the shared
                # writer, so the two elements carry identical metadata.
                assert orm_el.description == sql_el.description == "Shared entity description"
                assert orm_el.custom_properties == {"source_model": "DataEntity"}
                assert sql_el.custom_properties == {"source_model": "DataEntity"}
                assert orm_el.type == sql_el.type == "DataObject"
                assert orm_el.layer == sql_el.layer == "application"
            finally:
                fresh.close()
    finally:
        _cleanup(app, [org_id])


def test_backfill_application_component_parity(app):
    """An ApplicationComponent created through the ORM and one seeded with
    plain SQL produce elements with equal description and equal provenance
    properties after backfill (fails when the shared writer is not the one
    used for one of the two creation paths)."""
    from app import db
    from app.commands.backfill_domain_elements import backfill_domain_elements
    from app.models.application_portfolio import ApplicationComponent

    orgs = _seed_orgs(app, ["bf-ac-parity"])
    org_id = orgs["bf-ac-parity"]
    try:
        with app.app_context():
            # ORM-created application: goes through the flush listener.
            orm_app = ApplicationComponent(
                name="ORM Application",
                description="Shared application description",
                organization_id=org_id,
            )
            db.session.add(orm_app)
            db.session.flush()
            orm_element_id = orm_app.archimate_element_id
            assert orm_element_id is not None

            # SQL-seeded application: no element, needs backfill.
            _seed_application(app, org_id, "SQL Application", "Shared application description")

            stats = backfill_domain_elements(org_id)
            assert stats["ApplicationComponent"]["created"] == 1

            fresh = db.session.session_factory()
            try:
                orm_row = fresh.query(ApplicationComponent).filter_by(
                    name="ORM Application",
                ).one()
                sql_row = fresh.query(ApplicationComponent).filter_by(
                    name="SQL Application",
                ).one()

                orm_el = fresh.get(ArchiMateElement, orm_row.archimate_element_id)
                sql_el = fresh.get(ArchiMateElement, sql_row.archimate_element_id)

                assert orm_el is not None
                assert sql_el is not None

                # Equal description and provenance: both paths use the shared
                # writer, so the two elements carry identical metadata.
                assert orm_el.description == sql_el.description == "Shared application description"
                assert orm_el.custom_properties == {"source_model": "ApplicationComponent"}
                assert sql_el.custom_properties == {"source_model": "ApplicationComponent"}
                assert orm_el.type == sql_el.type == "ApplicationComponent"
                assert orm_el.layer == sql_el.layer == "Application"
            finally:
                fresh.close()
    finally:
        _cleanup(app, [org_id])


@pytest.mark.timeout(120)
def test_cli_backfill_application_and_capability_use_shared_writer(app):
    """`flask backfill-archimate-elements` gives an application and a capability
    that have no element exactly one element each, written by the shared writer
    (provenance in custom_properties, the row's own description), and a row the
    writer refuses does not stall the run (fails on 7e7ff671, where the command
    built the elements by hand without provenance)."""
    from app import db
    from app.commands.backfill_archimate_elements import backfill_archimate_elements_command
    from sqlalchemy import text

    orgs = _seed_orgs(app, ["cli-bf"])
    org_id = orgs["cli-bf"]
    try:
        app_id = _seed_application(app, org_id, "CLI App", "CLI application description")
        cap_id = _seed_capability(app, org_id, "CLI Capability", "CLI capability description")
        # A nameless application cannot be mirrored: the writer raises for it.
        refused_id = _seed_application(app, org_id, "", None)

        result = app.test_cli_runner().invoke(backfill_archimate_elements_command, [])
        assert result.exit_code == 0, result.output

        with app.app_context():
            rows = db.session.execute(
                text(
                    "SELECT e.type, e.layer, e.description, e.custom_properties "
                    "FROM application_components a JOIN archimate_elements e "
                    "ON e.id = a.archimate_element_id WHERE a.id = :i"
                ),
                {"i": app_id},
            ).mappings().all()
            assert len(rows) == 1
            assert rows[0]["type"] == "ApplicationComponent"
            assert rows[0]["description"] == "CLI application description"
            assert rows[0]["custom_properties"] == {"source_model": "ApplicationComponent"}

            rows = db.session.execute(
                text(
                    "SELECT e.type, e.description, e.custom_properties "
                    "FROM business_capability c JOIN archimate_elements e "
                    "ON e.id = c.archimate_element_id WHERE c.id = :i"
                ),
                {"i": cap_id},
            ).mappings().all()
            assert len(rows) == 1
            assert rows[0]["description"] == "CLI capability description"
            assert rows[0]["custom_properties"] == {"source_model": "BusinessCapability"}

            # One element per row, none for the refused row, none duplicated.
            assert db.session.execute(
                text("SELECT archimate_element_id FROM application_components WHERE id = :i"),
                {"i": refused_id},
            ).scalar() is None
            assert db.session.execute(
                text("SELECT count(*) FROM archimate_elements WHERE organization_id = :o"),
                {"o": org_id},
            ).scalar() == 2

            # A second run changes nothing.
            result = app.test_cli_runner().invoke(backfill_archimate_elements_command, [])
            assert result.exit_code == 0, result.output
            assert db.session.execute(
                text("SELECT count(*) FROM archimate_elements WHERE organization_id = :o"),
                {"o": org_id},
            ).scalar() == 2
    finally:
        _cleanup(app, [org_id])


def test_backfill_rollback_path_leaves_exactly_one_element_per_row(app):
    """The rollback path: a work package flushed before the command runs keeps
    its element (the command commits each type block), a work package added in
    the same session after a rollback gets exactly one element, and a row that
    is rolled back has none (fails on the no-commit revision: the flushed
    element vanishes with the rollback)."""
    from app import db
    from app.commands.backfill_domain_elements import backfill_domain_elements
    from app.models.application_portfolio import VendorContract
    from app.models.implementation_migration import WorkPackage

    orgs = _seed_orgs(app, ["bf-rb"])
    org_id = orgs["bf-rb"]
    try:
        with app.app_context():
            # A row for the command to do real work on.
            _seed_contract(app, org_id, "Backfill Target")

            wp1 = WorkPackage(
                name="Flushed First", context="application", organization_id=org_id,
            )
            db.session.add(wp1)
            db.session.flush()
            flushed_element_id = wp1.archimate_element_id
            assert flushed_element_id is not None  # element created by the flush listener

            # The command commits each type block, so the flushed element above
            # is persisted by the run itself.
            stats = backfill_domain_elements(org_id)
            assert stats["VendorContract"]["created"] == 1

            # Roll the session back. On the fixed command nothing pending is
            # lost; on the no-commit revision this discards the flushed
            # element and the run's own creations.
            db.session.rollback()
            db.session.expire_all()

            # A work package rolled back must leave nothing behind.
            doomed = WorkPackage(
                name="Rolled Back Row", context="application", organization_id=org_id,
            )
            db.session.add(doomed)
            db.session.flush()
            db.session.rollback()
            db.session.expire_all()

            # Another work package in the same session: exactly one element,
            # and the two earlier work packages keep exactly one each.
            survivor = WorkPackage(
                name="Added After", context="application", organization_id=org_id,
            )
            db.session.add(survivor)
            db.session.flush()
            assert survivor.archimate_element_id is not None
            db.session.commit()

            fresh = db.session.session_factory()
            try:
                rows = {
                    r.name: r
                    for r in fresh.query(WorkPackage).filter_by(organization_id=org_id).all()
                }
                assert set(rows) == {"Flushed First", "Added After"}
                assert rows["Flushed First"].archimate_element_id is not None
                assert rows["Added After"].archimate_element_id is not None

                wp_elements = fresh.query(ArchiMateElement).filter(
                    ArchiMateElement.organization_id == org_id,
                    ArchiMateElement.type == "WorkPackage",
                ).all()
                assert len(wp_elements) == 2  # exactly one element per row
                assert {e.id for e in wp_elements} == {
                    rows["Flushed First"].archimate_element_id,
                    rows["Added After"].archimate_element_id,
                }
                assert flushed_element_id in {e.id for e in wp_elements}

                # The command's own creations survived the rollback too.
                contract = fresh.query(VendorContract).filter_by(
                    organization_id=org_id,
                ).one()
                contract_element = fresh.get(ArchiMateElement, contract.archimate_element_id)
                assert contract_element is not None
                assert contract_element.type == "Contract"
            finally:
                fresh.close()
    finally:
        _cleanup(app, list(orgs.values()))


def test_backfill_flush_failure_clears_pending_map(app):
    """A flush that fails after before_flush has queued an element clears the
    pending map for that session; a following work package in the same session
    gets exactly one element (fails when the after_rollback listener is
    removed: stale entries in _PENDING cause the next work package to get
    zero or two elements)."""
    from app import db
    from app.models.implementation_migration import WorkPackage
    from app.services import archimate_backbone

    orgs = _seed_orgs(app, ["bf-flush-fail"])
    org_id = orgs["bf-flush-fail"]
    try:
        with app.app_context():
            # A work package that will be queued in before_flush.
            wp1 = WorkPackage(
                name="Queued Then Failed",
                context="application",
                organization_id=org_id,
            )
            db.session.add(wp1)

            # A second work package with the same materialisation_key
            # violates the unique constraint uq_work_package_materialisation.
            wp2 = WorkPackage(
                name="Constraint Violator",
                context="application",
                organization_id=org_id,
                materialisation_key="duplicate-key",
            )
            db.session.add(wp2)
            wp3 = WorkPackage(
                name="Also Duplicate Key",
                context="application",
                organization_id=org_id,
                materialisation_key="duplicate-key",
            )
            db.session.add(wp3)

            # The backbone keys _PENDING and _SYNCING by the id of the
            # underlying Session passed to the flush events, never by the
            # Flask-SQLAlchemy scoped-session proxy. Key the assertion the
            # same way so it is not vacuously true.
            sid = id(db.session.registry())
            # The flush fails because of the unique constraint violation.
            with pytest.raises(Exception):
                db.session.flush()

            # Roll back.
            db.session.rollback()

            # The pending map for this session must be empty.
            assert sid not in archimate_backbone._PENDING, (
                "pending map must be cleared after rollback"
            )

            # A following work package in the same session gets exactly one element.
            survivor = WorkPackage(
                name="Survivor After Rollback",
                context="application",
                organization_id=org_id,
            )
            db.session.add(survivor)
            db.session.flush()
            assert survivor.archimate_element_id is not None, (
                "survivor must get exactly one element"
            )
            db.session.expire_all()
            survivor = db.session.query(WorkPackage).filter_by(
                name="Survivor After Rollback",
            ).first()
            assert survivor is not None
            assert survivor.archimate_element_id is not None

            # Verify only one element was created for the survivor.
            from app.models.archimate_core import ArchiMateElement
            elements = db.session.query(ArchiMateElement).filter(
                ArchiMateElement.organization_id == org_id,
                ArchiMateElement.type == "WorkPackage",
            ).all()
            assert len(elements) == 1, (
                "exactly one element for the survivor, not stale entries"
            )
    finally:
        _cleanup(app, [org_id])


def test_backfill_reentry_guard_is_per_session(app):
    """The re-entry guard is keyed by (session_id, obj_id); a rollback in one
    session clears only that session's guard entries and leaves the other
    session's held entry in place (fails when the guard is module-global or
    the rollback clears the whole map)."""
    from app import db
    from app.models.implementation_migration import WorkPackage
    from app.services import archimate_backbone

    def _hold_guard(session, obj):
        key = (id(session), id(obj))
        if isinstance(archimate_backbone._SYNCING, dict):
            archimate_backbone._SYNCING[key] = True
        else:
            archimate_backbone._SYNCING.add(key)
        return key

    orgs = _seed_orgs(app, ["bf-reentry"])
    org_id = orgs["bf-reentry"]
    try:
        with app.app_context():
            session_a = db.session.session_factory()
            session_b = db.session.session_factory()
            try:
                # Session A holds a re-entry guard entry for its work package,
                # as it would while sync_archimate_element is mid-flight.
                wp_a = WorkPackage(
                    name="Session A WP",
                    context="application",
                    organization_id=org_id,
                )
                session_a.add(wp_a)
                key_a = _hold_guard(session_a, wp_a)

                # Session B also holds one, then rolls back. Under the fixed
                # guard only session B's keys are cleared; session A's held
                # entry must survive the other session's rollback.
                wp_b = WorkPackage(
                    name="Session B WP",
                    context="application",
                    organization_id=org_id,
                )
                session_b.add(wp_b)
                key_b = _hold_guard(session_b, wp_b)

                session_b.rollback()

                assert key_b not in archimate_backbone._SYNCING, (
                    "session B's rollback must clear only session B's guard entries"
                )
                assert key_a in archimate_backbone._SYNCING, (
                    "session A's guard entry must survive session B's rollback"
                )
            finally:
                archimate_backbone._SYNCING.clear()
                session_a.close()
                session_b.close()
    finally:
        _cleanup(app, [org_id])