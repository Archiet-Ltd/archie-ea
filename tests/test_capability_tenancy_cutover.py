"""Behavioural tests for the UnifiedCapability tenancy maintenance cutover.

Each cutover test works in a transaction-local cloned schema.  The production
schema is never classified, deduplicated, or constraint-swapped by this suite.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.commands import cutover_capability_tenancy as cutover_module
from app.commands.cutover_capability_tenancy import (
    CutoverBlocked,
    classify_capability,
    install_cutover_constraints,
    run_cutover,
)
from app.middleware.tenant_isolation import bind_tenant


pytestmark = pytest.mark.usefixtures("db_session")


def _schema_ddl(schema: str) -> list[str]:
    statements = [f'CREATE SCHEMA "{schema}"']
    for table in (
        "organizations",
        "unified_capabilities",
        "application_components",
        "unified_application_capability_mapping",
        "benefits",
        "work_packages",
    ):
        statements.append(
            f'CREATE TABLE "{schema}"."{table}" '
            f'(LIKE public."{table}" INCLUDING DEFAULTS INCLUDING CONSTRAINTS)'
        )
    statements.extend(
        (
            f'SET search_path TO "{schema}", public',
            "ALTER TABLE unified_capabilities ADD COLUMN IF NOT EXISTS organization_id INTEGER",
            "ALTER TABLE unified_capabilities ADD COLUMN IF NOT EXISTS scope VARCHAR(16)",
            "ALTER TABLE unified_capabilities ADD COLUMN IF NOT EXISTS reference_capability_id BIGINT",
            "ALTER TABLE unified_capabilities ADD COLUMN IF NOT EXISTS source_table VARCHAR(128)",
            "ALTER TABLE unified_capabilities ADD COLUMN IF NOT EXISTS source_id VARCHAR(255)",
            "ALTER TABLE unified_capabilities ADD COLUMN IF NOT EXISTS source_org_id INTEGER",
            "ALTER TABLE unified_capabilities ADD COLUMN IF NOT EXISTS source_checksum VARCHAR(64)",
            "ALTER TABLE unified_capabilities ADD COLUMN IF NOT EXISTS retired_into_id BIGINT",
            "ALTER TABLE organizations ADD PRIMARY KEY (id)",
            "ALTER TABLE unified_capabilities ADD PRIMARY KEY (id)",
            "ALTER TABLE unified_application_capability_mapping "
            "ADD CONSTRAINT fk_cutover_test_mapping_capability "
            "FOREIGN KEY (unified_capability_id) REFERENCES unified_capabilities(id)",
            "ALTER TABLE benefits ADD CONSTRAINT fk_cutover_test_benefit_capability "
            "FOREIGN KEY (capability_id) REFERENCES unified_capabilities(id)",
            "ALTER TABLE work_packages ADD CONSTRAINT fk_cutover_test_work_package_capability "
            "FOREIGN KEY (capability_id) REFERENCES unified_capabilities(id)",
        )
    )
    return statements


@pytest.fixture
def capability_schema(db_session):
    """Clone only the tables exercised by the maintenance classifier."""

    schema = f"capcut_{uuid.uuid4().hex[:12]}"
    connection = db_session.connection()
    connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    for table in (
        "organizations",
        "unified_capabilities",
        "application_components",
        "unified_application_capability_mapping",
        "benefits",
        "work_packages",
    ):
        connection.execute(
            text(
                f'CREATE TABLE "{schema}"."{table}" '
                f'(LIKE public."{table}" INCLUDING DEFAULTS INCLUDING CONSTRAINTS)'
            )
        )

    connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
    for ddl in (
        "ADD COLUMN IF NOT EXISTS organization_id INTEGER",
        "ADD COLUMN IF NOT EXISTS scope VARCHAR(16)",
        "ADD COLUMN IF NOT EXISTS reference_capability_id BIGINT",
        "ADD COLUMN IF NOT EXISTS source_table VARCHAR(128)",
        "ADD COLUMN IF NOT EXISTS source_id VARCHAR(255)",
        "ADD COLUMN IF NOT EXISTS source_org_id INTEGER",
        "ADD COLUMN IF NOT EXISTS source_checksum VARCHAR(64)",
        "ADD COLUMN IF NOT EXISTS retired_into_id BIGINT",
    ):
        connection.execute(text(f"ALTER TABLE unified_capabilities {ddl}"))
    connection.execute(text("ALTER TABLE organizations ADD PRIMARY KEY (id)"))
    connection.execute(text("ALTER TABLE unified_capabilities ADD PRIMARY KEY (id)"))
    connection.execute(
        text(
            "ALTER TABLE unified_application_capability_mapping "
            "ADD CONSTRAINT fk_cutover_test_mapping_capability "
            "FOREIGN KEY (unified_capability_id) REFERENCES unified_capabilities(id)"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE benefits ADD CONSTRAINT fk_cutover_test_benefit_capability "
            "FOREIGN KEY (capability_id) REFERENCES unified_capabilities(id)"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE work_packages ADD CONSTRAINT fk_cutover_test_work_package_capability "
            "FOREIGN KEY (capability_id) REFERENCES unified_capabilities(id)"
        )
    )
    return connection


@pytest.fixture
def standalone_capability_schema(app):
    """A committed disposable schema for standalone SQL and concurrency tests."""
    from app import db

    schema = f"capstand_{uuid.uuid4().hex[:12]}"
    raw = db.engine.raw_connection()
    raw.driver_connection.autocommit = True
    cursor = raw.cursor()
    for statement in _schema_ddl(schema):
        cursor.execute(statement)
    cursor.execute("SET statement_timeout = '2s'")
    try:
        yield raw, schema
    finally:
        try:
            raw.cursor().execute("SELECT pg_advisory_unlock_all()")
            raw.cursor().execute("RESET ALL")
        except Exception:
            pass
        raw.driver_connection.autocommit = False
        raw.close()
        cleanup = db.engine.raw_connection()
        cleanup.driver_connection.autocommit = True
        try:
            cleanup.cursor().execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            cleanup.cursor().execute("RESET ALL")
            cleanup.driver_connection.autocommit = False
            cleanup.close()


def _standalone_sql(name: str) -> str:
    path = Path(__file__).parents[1] / "scripts" / "migrations" / name
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("\\")
    )


def _organization(connection, organization_id: int):
    connection.execute(
        text(
            "INSERT INTO organizations (id, name, slug) VALUES (:id, :name, :slug) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": organization_id,
            "name": f"Organization {organization_id}",
            "slug": f"organization-{organization_id}",
        },
    )


def _capability(
    connection,
    capability_id: int,
    code: str,
    *,
    source_table: str,
    source_org_id: int | None,
    archimate_id: str | None = None,
):
    connection.execute(
        text(
            """
            INSERT INTO unified_capabilities
                (id, name, code, level, version, archimate_id, source_table,
                 source_id, source_org_id, source_checksum)
            VALUES
                (:id, :name, :code, 1, 1, :archimate_id, :source_table,
                 :source_id, :source_org_id, :source_checksum)
            """
        ),
        {
            "id": capability_id,
            "name": f"Capability {capability_id}",
            "code": code,
            "archimate_id": archimate_id,
            "source_table": source_table,
            "source_id": str(capability_id),
            "source_org_id": source_org_id,
            "source_checksum": f"{'a' if source_org_id is None else 'b'}{capability_id:063d}",
        },
    )


def _application_link(connection, mapping_id: int, capability_id: int, app_id: int, org_id: int):
    _organization(connection, org_id)
    connection.execute(
        text(
            "INSERT INTO application_components (id, name, organization_id) "
            "VALUES (:id, :name, :org_id)"
        ),
        {"id": app_id, "name": f"Application {app_id}", "org_id": org_id},
    )
    connection.execute(
        text(
            "INSERT INTO unified_application_capability_mapping "
            "(id, unified_capability_id, application_component_id, is_active) "
            "VALUES (:id, :capability_id, :app_id, true)"
        ),
        {"id": mapping_id, "capability_id": capability_id, "app_id": app_id},
    )


def test_classifier_uses_only_audited_provenance_and_relationship_owners(capability_schema):
    """Wrong ownership logic (for example name/code guessing) changes these literal results."""

    connection = capability_schema
    _capability(connection, 101, "REF-101", source_table="seeded_import", source_org_id=None)
    _capability(connection, 102, "TEN-102", source_table="tenant_import", source_org_id=41)
    _capability(connection, 103, "AMB-103", source_table="tenant_import", source_org_id=41)
    _application_link(connection, 201, 102, 301, 41)
    _application_link(connection, 202, 103, 302, 41)
    _application_link(connection, 203, 103, 303, 42)

    reference = classify_capability(connection, 101)
    tenant = classify_capability(connection, 102)
    ambiguous = classify_capability(connection, 103)

    assert (reference.scope, reference.organization_id) == ("reference", None)
    assert (tenant.scope, tenant.organization_id) == ("tenant", 41)
    assert (ambiguous.scope, ambiguous.organization_id) == ("ambiguous", None)
    assert "application_components:301:organization_id=41" in tenant.evidence
    assert "application_components:303:organization_id=42" in ambiguous.evidence


def test_projected_row_with_provenance_and_no_links_is_tenant(capability_schema):
    """A projected row is owned by its source's organisation, not ambiguous.

    `flask project-capabilities` writes rows that carry full provenance and no
    downstream relationships yet. Before this branch existed every one of them
    classified `ambiguous`, and `run_cutover` raises CutoverBlocked on any
    ambiguous row -- so projecting before the cutover permanently blocked the
    cutover. The owner here comes from a NOT NULL organization_id on the source
    row, which is stronger evidence than inferring it from relationships.
    """

    connection = capability_schema
    _capability(connection, 401, "PRJ-401", source_table="business_capability",
                source_org_id=41)

    projected = classify_capability(connection, 401)

    assert (projected.scope, projected.organization_id) == ("tenant", 41)


def test_provenance_never_overrides_a_contradicting_relationship(capability_schema):
    """Corroboration may be absent; contradiction may not be ignored.

    The new branch fires ONLY when no relationship names an owner. A link owned
    by a different organisation than the provenance is real ambiguity and must
    still block the cutover rather than be resolved in provenance's favour.
    """

    connection = capability_schema
    _capability(connection, 402, "PRJ-402", source_table="business_capability",
                source_org_id=41)
    _application_link(connection, 204, 402, 304, 42)

    contradicted = classify_capability(connection, 402)

    assert contradicted.scope == "ambiguous"
    assert contradicted.organization_id is None


def test_provenance_without_an_owner_invents_none(capability_schema):
    """No source_org_id means no owner. The classifier must not guess one."""

    connection = capability_schema
    _capability(connection, 403, "PRJ-403", source_table="business_capability",
                source_org_id=None)

    unowned = classify_capability(connection, 403)

    assert unowned.scope in {"ambiguous", "reference"}
    assert unowned.organization_id is None


def test_dry_run_reports_stable_measurements_without_writing(capability_schema):
    """A dry run must catch an accidental UPDATE or constraint swap."""

    connection = capability_schema
    _capability(connection, 111, "REF-111", source_table="seeded_import", source_org_id=None)
    _capability(connection, 112, "TEN-112", source_table="tenant_import", source_org_id=51)
    _capability(connection, 113, "AMB-113", source_table="tenant_import", source_org_id=51)
    _application_link(connection, 211, 112, 311, 51)
    _application_link(connection, 212, 113, 312, 51)
    _application_link(connection, 213, 113, 313, 52)

    report = run_cutover(connection, apply=False)

    assert report["writes"] == 0
    assert report["counts"] == {
        "classified": 3,
        "reference": 1,
        "tenant": 1,
        "ambiguous": 1,
    }
    assert report["before"]["checksum"] == report["after"]["checksum"]
    assert connection.execute(
        text("SELECT count(*) FROM unified_capabilities WHERE scope IS NOT NULL")
    ).scalar_one() == 0


def test_apply_requires_backup_manifest_and_blocks_ambiguous_active_links(
    capability_schema, tmp_path
):
    """Apply must stop before any classification write when recovery or ownership is unsafe."""

    connection = capability_schema
    _capability(connection, 121, "AMB-121", source_table="tenant_import", source_org_id=61)
    _application_link(connection, 221, 121, 321, 61)
    _application_link(connection, 222, 121, 322, 62)

    with pytest.raises(CutoverBlocked, match="backup manifest"):
        run_cutover(connection, apply=True)

    manifest = tmp_path / "backup.json"
    manifest.write_text(json.dumps({"backup_path": "s3://backups/capabilities-20260822.dump"}))
    with pytest.raises(CutoverBlocked, match="ambiguous active links"):
        run_cutover(connection, apply=True, backup_manifest=manifest)

    row = connection.execute(
        text("SELECT scope, organization_id FROM unified_capabilities WHERE id = 121")
    ).one()
    assert tuple(row) == (None, None)


def test_apply_merges_same_scope_duplicates_and_preserves_every_fk_count(
    capability_schema, tmp_path
):
    """A wrong repoint predicate or dropped relationship changes the measured FK totals."""

    connection = capability_schema
    _capability(connection, 131, "DUP-REF", source_table="seeded_import", source_org_id=None)
    _capability(connection, 132, "DUP-REF", source_table="seeded_import", source_org_id=None)
    _capability(connection, 133, "DUP-TEN", source_table="tenant_import", source_org_id=71)
    _capability(connection, 134, "DUP-TEN", source_table="tenant_import", source_org_id=71)
    _application_link(connection, 231, 133, 331, 71)
    _application_link(connection, 232, 134, 332, 71)
    connection.execute(
        text(
            "INSERT INTO benefits "
            "(id, name, capability_id, organization_id, created_at, updated_at) "
            "VALUES (233, 'Duplicate benefit', 134, 71, now(), now())"
        )
    )
    connection.execute(
        text(
            "INSERT INTO work_packages "
            "(id, name, context, capability_id, organization_id, created_at, updated_at) "
            "VALUES (234, 'Duplicate work package', 'enterprise', 134, 71, now(), now())"
        )
    )
    manifest = tmp_path / "backup.json"
    manifest.write_text(json.dumps({"backup_path": "C:/backups/capabilities.dump"}))

    report = run_cutover(connection, apply=True, backup_manifest=manifest)

    assert report["counts"]["ambiguous"] == 0
    assert report["duplicates"] == [
        {
            "source_id": 132,
            "target_id": 131,
            "scope": "reference",
            "organization_id": None,
            "code": "DUP-REF",
        },
        {
            "source_id": 134,
            "target_id": 133,
            "scope": "tenant",
            "organization_id": 71,
            "code": "DUP-TEN",
        },
    ]
    retired = connection.execute(
        text(
            "SELECT id, code, retired_into_id FROM unified_capabilities "
            "WHERE id IN (132, 134) ORDER BY id"
        )
    ).all()
    assert [tuple(row) for row in retired] == [(132, None, 131), (134, None, 133)]
    assert connection.execute(
        text(
            "SELECT unified_capability_id FROM unified_application_capability_mapping "
            "WHERE id = 232"
        )
    ).scalar_one() == 133
    assert connection.execute(
        text("SELECT capability_id FROM benefits WHERE id = 233")
    ).scalar_one() == 133
    assert connection.execute(
        text("SELECT capability_id FROM work_packages WHERE id = 234")
    ).scalar_one() == 133
    assert {
        "benefits.capability_id",
        "work_packages.capability_id",
        "unified_application_capability_mapping.unified_capability_id",
    } <= set(report["foreign_keys"]["before"])
    assert report["foreign_keys"]["before"] == report["foreign_keys"]["after"]
    assert all(count == 0 for count in report["foreign_keys"]["orphans"].values())


def test_partial_indexes_enforce_reference_and_tenant_uniqueness(capability_schema):
    """The four required uniqueness boundaries must be enforced by PostgreSQL."""

    connection = capability_schema
    install_cutover_constraints(connection)
    _organization(connection, 81)
    _organization(connection, 82)
    connection.execute(
        text(
            "INSERT INTO unified_capabilities "
            "(id, name, code, archimate_id, level, version, scope, organization_id) VALUES "
            "(141, 'Reference', 'SAME', 'ARCH-SAME', 1, 1, 'reference', NULL), "
            "(142, 'Tenant A', 'SAME', 'ARCH-SAME', 1, 1, 'tenant', 81), "
            "(143, 'Tenant B', 'SAME', 'ARCH-SAME', 1, 1, 'tenant', 82)"
        )
    )

    for values in (
        "(144, 'Reference duplicate code', 'SAME', NULL, 1, 1, 'reference', NULL)",
        "(145, 'Tenant duplicate code', 'SAME', NULL, 1, 1, 'tenant', 81)",
        "(146, 'Reference duplicate ArchiMate', 'OTHER-REF', 'ARCH-SAME', 1, 1, 'reference', NULL)",
        "(147, 'Tenant duplicate ArchiMate', 'OTHER-TEN', 'ARCH-SAME', 1, 1, 'tenant', 81)",
    ):
        nested = connection.begin_nested()
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO unified_capabilities "
                    "(id, name, code, archimate_id, level, version, scope, organization_id) "
                    f"VALUES {values}"
                )
            )
        nested.rollback()


def test_apply_installs_validated_ownership_fks_and_database_write_guard(
    capability_schema, tmp_path
):
    """The CLI constraint swap must not depend on running a second manual script."""

    connection = capability_schema
    _capability(connection, 151, "REF-151", source_table="seeded_import", source_org_id=None)
    manifest = tmp_path / "backup.json"
    manifest.write_text(json.dumps({"backup_path": "C:/backups/capabilities.dump"}))

    report = run_cutover(connection, apply=True, backup_manifest=manifest)

    constraints = dict(
        connection.execute(
            text(
                "SELECT conname, convalidated FROM pg_constraint "
                "WHERE conrelid = to_regclass('unified_capabilities') "
                "AND conname IN ("
                "'fk_unified_capabilities_organization', "
                "'fk_unified_capabilities_source_org', "
                "'fk_unified_capabilities_reference', "
                "'fk_unified_capabilities_retired_into')"
            )
        ).all()
    )
    assert constraints == {
        "fk_unified_capabilities_organization": True,
        "fk_unified_capabilities_source_org": True,
        "fk_unified_capabilities_reference": True,
        "fk_unified_capabilities_retired_into": True,
    }
    trigger_definition = connection.execute(
        text(
            "SELECT pg_get_triggerdef(oid) FROM pg_trigger "
            "WHERE tgrelid = to_regclass('unified_capabilities') "
            "AND tgname = 'trg_unified_capability_write_scope' AND NOT tgisinternal"
        )
    ).scalar_one()
    assert "DELETE" in trigger_definition
    check_definition = connection.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = to_regclass('unified_capabilities') "
            "AND conname = 'ck_unified_capabilities_scope_owner'"
        )
    ).scalar_one()
    assert "scope IS NOT NULL" in check_definition
    assert report["constraint_swap"] is True


def test_cli_rejects_apply_without_a_recorded_backup(app, tmp_path):
    """The public command cannot enter apply mode without a recovery record."""

    report = tmp_path / "report.json"
    result = app.test_cli_runner().invoke(
        args=["cutover-capability-tenancy", "--apply", "--report", str(report)]
    )

    assert result.exit_code != 0
    assert "backup manifest" in result.output.lower()
    assert not report.exists()


def test_database_guard_enforces_tenant_context_for_bulk_update_and_delete(
    capability_schema,
):
    """The PostgreSQL guard must protect raw/bulk writes, not only ORM instances."""

    connection = capability_schema
    install_cutover_constraints(connection)
    _organization(connection, 91)
    _organization(connection, 92)
    connection.execute(
        text(
            "INSERT INTO unified_capabilities "
            "(id, name, code, level, version, scope, organization_id) VALUES "
            "(161, 'Reference', 'GUARD-REF', 1, 1, 'reference', NULL), "
            "(162, 'Tenant A', 'GUARD-A', 1, 1, 'tenant', 91), "
            "(163, 'Tenant B', 'GUARD-B', 1, 1, 'tenant', 92)"
        )
    )
    connection.execute(
        text("SELECT set_config('archie.organization_id', '91', true)")
    )

    for statement in (
        "UPDATE unified_capabilities SET organization_id = 92 WHERE id = 162",
        "UPDATE unified_capabilities SET name = 'cross-tenant' WHERE id = 163",
        "DELETE FROM unified_capabilities WHERE id = 161",
        "DELETE FROM unified_capabilities WHERE id = 163",
    ):
        nested = connection.begin_nested()
        with pytest.raises(Exception, match="tenant|reference"):
            connection.execute(text(statement))
        nested.rollback()

    connection.execute(text("DELETE FROM unified_capabilities WHERE id = 162"))
    assert connection.execute(
        text("SELECT count(*) FROM unified_capabilities WHERE id = 162")
    ).scalar_one() == 0


def test_final_constraint_rejects_null_or_invalid_scope_ownership(capability_schema):
    """Once installed, the cutover invariant has no legacy NULL escape hatch."""

    connection = capability_schema
    install_cutover_constraints(connection)
    _organization(connection, 93)
    invalid_rows = (
        "(171, 'Null scope', 'NULL-SCOPE', 1, 1, NULL, NULL)",
        "(172, 'Reference owner', 'REF-OWNER', 1, 1, 'reference', 93)",
        "(173, 'Tenant no owner', 'TEN-NO-OWNER', 1, 1, 'tenant', NULL)",
        "(174, 'Unknown scope', 'UNKNOWN-SCOPE', 1, 1, 'unknown', 93)",
    )
    for values in invalid_rows:
        nested = connection.begin_nested()
        with pytest.raises(SQLAlchemyError):
            connection.execute(
                text(
                    "INSERT INTO unified_capabilities "
                    "(id, name, code, level, version, scope, organization_id) "
                    f"VALUES {values}"
                )
            )
        nested.rollback()


def test_duplicate_retirement_blocks_unknown_or_conflicting_relationship_owner(
    capability_schema, tmp_path
):
    """No FK may remain on a retired duplicate because its owner was unknowable."""

    connection = capability_schema
    connection.execute(
        text(
            "CREATE TABLE unknown_capability_links ("
            "id INTEGER PRIMARY KEY, capability_id BIGINT NOT NULL, "
            "CONSTRAINT fk_unknown_capability FOREIGN KEY (capability_id) "
            "REFERENCES unified_capabilities(id))"
        )
    )
    _capability(connection, 181, "DUP-UNKNOWN", source_table="tenant_import", source_org_id=94)
    _capability(connection, 182, "DUP-UNKNOWN", source_table="tenant_import", source_org_id=94)
    _application_link(connection, 281, 181, 381, 94)
    _application_link(connection, 282, 182, 382, 94)
    connection.execute(
        text("INSERT INTO unknown_capability_links (id, capability_id) VALUES (1, 182)")
    )
    manifest = tmp_path / "backup.json"
    manifest.write_text(json.dumps({"backup_path": "C:/backups/capabilities.dump"}))

    with pytest.raises(CutoverBlocked, match="unknown or conflicting ownership"):
        run_cutover(connection, apply=True, backup_manifest=manifest)

    assert connection.execute(
        text("SELECT retired_into_id FROM unified_capabilities WHERE id = 182")
    ).scalar_one() is None


def test_reference_duplicate_retirement_blocks_unknown_relationship_owner(
    capability_schema, tmp_path
):
    """NULL reference ownership must never make an unowned FK look eligible."""

    connection = capability_schema
    connection.execute(
        text(
            "CREATE TABLE unknown_reference_links ("
            "id INTEGER PRIMARY KEY, capability_id BIGINT NOT NULL, "
            "CONSTRAINT fk_unknown_reference_capability FOREIGN KEY (capability_id) "
            "REFERENCES unified_capabilities(id))"
        )
    )
    _capability(connection, 183, "DUP-REF-UNKNOWN", source_table="seeded_import", source_org_id=None)
    _capability(connection, 184, "DUP-REF-UNKNOWN", source_table="seeded_import", source_org_id=None)
    connection.execute(
        text("INSERT INTO unknown_reference_links (id, capability_id) VALUES (1, 184)")
    )
    manifest = tmp_path / "backup.json"
    manifest.write_text(json.dumps({"backup_path": "C:/backups/capabilities.dump"}))

    with pytest.raises(CutoverBlocked, match="unknown or conflicting ownership"):
        run_cutover(connection, apply=True, backup_manifest=manifest)

    assert connection.execute(
        text("SELECT retired_into_id FROM unified_capabilities WHERE id = 184")
    ).scalar_one() is None
    assert connection.execute(
        text("SELECT capability_id FROM unknown_reference_links WHERE id = 1")
    ).scalar_one() == 184


def test_repeat_apply_is_a_verified_no_op_with_stable_measurements(
    capability_schema, tmp_path
):
    """A completed cutover can be safely rerun without reclassifying retired rows."""

    connection = capability_schema
    _capability(connection, 191, "DUP-IDEMP", source_table="tenant_import", source_org_id=95)
    _capability(connection, 192, "DUP-IDEMP", source_table="tenant_import", source_org_id=95)
    _application_link(connection, 291, 191, 391, 95)
    _application_link(connection, 292, 192, 392, 95)
    manifest = tmp_path / "backup.json"
    manifest.write_text(json.dumps({"backup_path": "C:/backups/capabilities.dump"}))

    first = run_cutover(connection, apply=True, backup_manifest=manifest)
    second = run_cutover(connection, apply=True, backup_manifest=manifest)

    assert first["writes"] > 0
    assert second["writes"] == 0
    assert second["duplicates"] == []
    assert second["before"] == second["after"] == first["after"]


def test_python_constraint_swap_drops_legacy_named_unique_constraints(capability_schema):
    """Python and standalone SQL accept both legacy constraint and index shapes."""

    connection = capability_schema
    connection.execute(
        text(
            "ALTER TABLE unified_capabilities ADD CONSTRAINT unified_capabilities_code_key "
            "UNIQUE (code)"
        )
    )
    connection.execute(
        text(
            "ALTER TABLE unified_capabilities "
            "ADD CONSTRAINT unified_capabilities_archimate_id_key UNIQUE (archimate_id)"
        )
    )

    install_cutover_constraints(connection)

    remaining = connection.execute(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = to_regclass('unified_capabilities') "
            "AND conname IN ('unified_capabilities_code_key', "
            "'unified_capabilities_archimate_id_key')"
        )
    ).all()
    assert remaining == []


def test_report_failure_occurs_before_transaction_can_commit(
    capability_schema, tmp_path, monkeypatch
):
    """A durable audit report is a precondition of committing cutover writes."""

    connection = capability_schema
    _capability(connection, 201, "REPORT-FAIL", source_table="seeded_import", source_org_id=None)
    manifest = tmp_path / "backup.json"
    manifest.write_text(json.dumps({"backup_path": "C:/backups/capabilities.dump"}))
    report_path = tmp_path / "report.json"

    def fail_report(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(
        "app.commands.cutover_capability_tenancy._write_report_atomic", fail_report
    )
    nested = connection.begin_nested()
    with pytest.raises(OSError, match="disk full"):
        cutover_module.execute_cutover_with_report(
            connection,
            report_path=report_path,
            apply=True,
            backup_manifest=manifest,
        )
    nested.rollback()

    assert connection.execute(
        text("SELECT scope FROM unified_capabilities WHERE id = 201")
    ).scalar_one() is None
    assert not report_path.exists()


def test_atomic_report_writer_cleans_temporary_file_on_replace_failure(tmp_path, monkeypatch):
    """A filesystem failure cannot leave a partial JSON report at the final path."""

    report_path = tmp_path / "cutover.json"

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        cutover_module._write_report_atomic(report_path, {"writes": 1})

    assert not report_path.exists()
    assert list(tmp_path.glob(".cutover.json.*.tmp")) == []


class _RollbackThenFailTransaction:
    def __init__(self, transaction):
        self._transaction = transaction

    @property
    def is_active(self):
        return self._transaction.is_active

    def commit(self):
        self._transaction.rollback()
        raise RuntimeError("simulated commit failure")

    def rollback(self):
        if self._transaction.is_active:
            self._transaction.rollback()


class _CommitFailingConnection:
    def __init__(self, connection):
        self._connection = connection

    def begin(self):
        return _RollbackThenFailTransaction(self._connection.begin())

    def close(self):
        self._connection.close()

    def __getattr__(self, name):
        return getattr(self._connection, name)


class _CommitFailingEngine:
    def __init__(self, engine):
        self._engine = engine

    def connect(self):
        return _CommitFailingConnection(self._engine.connect())


def _schema_engine(app, schema):
    from app import db

    return create_engine(
        db.engine.url,
        connect_args={"options": f"-csearch_path={schema},public"},
    )


def _seed_standalone_reference(raw, capability_id: int, code: str):
    raw.cursor().execute(
        "INSERT INTO unified_capabilities "
        "(id, name, code, level, version, source_table, source_id, source_checksum) "
        "VALUES (%s, %s, %s, 1, 1, 'seeded_import', %s, %s)",
        (capability_id, f"Capability {capability_id}", code, str(capability_id), "d" * 64),
    )


def test_commit_failure_leaves_pending_report_and_rolls_back_database(
    standalone_capability_schema, tmp_path, app
):
    """A failed commit cannot leave an audit artifact claiming completion."""

    raw, schema = standalone_capability_schema
    _seed_standalone_reference(raw, 601, "COMMIT-FAIL")
    manifest = tmp_path / "backup.json"
    manifest.write_text(json.dumps({"backup_path": "C:/backups/capabilities.dump"}))
    report_path = tmp_path / "commit-failure.json"
    engine = _schema_engine(app, schema)
    try:
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            cutover_module.execute_cutover_with_audit(
                _CommitFailingEngine(engine),
                report_path=report_path,
                apply=True,
                backup_manifest=manifest,
            )
    finally:
        engine.dispose()

    cursor = raw.cursor()
    cursor.execute("SELECT scope FROM unified_capabilities WHERE id = 601")
    assert cursor.fetchone()[0] is None
    pending = json.loads(report_path.read_text(encoding="utf-8"))
    assert pending["audit_state"] == "pending_commit"
    assert pending["database_commit_confirmed"] is False
    assert not (tmp_path / "commit-failure.json.committed-report-pending.json").exists()


def test_publication_failure_retains_committed_recovery_marker(
    standalone_capability_schema, tmp_path, app, monkeypatch
):
    """A committed DB plus failed final report has an unambiguous recovery record."""

    raw, schema = standalone_capability_schema
    _seed_standalone_reference(raw, 602, "PUBLISH-FAIL")
    manifest = tmp_path / "backup.json"
    manifest.write_text(json.dumps({"backup_path": "C:/backups/capabilities.dump"}))
    report_path = tmp_path / "publication-failure.json"
    marker_path = tmp_path / "publication-failure.json.committed-report-pending.json"
    real_writer = cutover_module._write_report_atomic

    def fail_completed_report(path, payload):
        if payload.get("audit_state") == "completed":
            raise OSError("simulated final publication failure")
        return real_writer(path, payload)

    monkeypatch.setattr(cutover_module, "_write_report_atomic", fail_completed_report)
    engine = _schema_engine(app, schema)
    try:
        with pytest.raises(Exception, match="committed.*report.*pending"):
            cutover_module.execute_cutover_with_audit(
                engine,
                report_path=report_path,
                apply=True,
                backup_manifest=manifest,
            )
    finally:
        engine.dispose()

    cursor = raw.cursor()
    cursor.execute("SELECT scope FROM unified_capabilities WHERE id = 602")
    assert cursor.fetchone()[0] == "reference"
    pending = json.loads(report_path.read_text(encoding="utf-8"))
    assert pending["audit_state"] == "pending_commit"
    assert pending["database_commit_confirmed"] is False
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["audit_state"] == "committed_report_pending"
    assert marker["database_commit_confirmed"] is True
    assert marker["report_path"] == str(report_path)


def test_success_report_is_completed_only_after_database_commit(
    standalone_capability_schema, tmp_path, app
):
    """The normal path replaces pending state only after commit succeeds."""

    raw, schema = standalone_capability_schema
    _seed_standalone_reference(raw, 603, "PUBLISH-SUCCESS")
    manifest = tmp_path / "backup.json"
    manifest.write_text(json.dumps({"backup_path": "C:/backups/capabilities.dump"}))
    report_path = tmp_path / "success.json"
    marker_path = tmp_path / "success.json.committed-report-pending.json"
    engine = _schema_engine(app, schema)
    try:
        payload = cutover_module.execute_cutover_with_audit(
            engine,
            report_path=report_path,
            apply=True,
            backup_manifest=manifest,
        )
    finally:
        engine.dispose()

    assert payload["audit_state"] == "completed"
    assert payload["database_commit_confirmed"] is True
    assert json.loads(report_path.read_text(encoding="utf-8")) == payload
    assert not marker_path.exists()


def test_apply_sql_is_failure_atomic_and_preserves_legacy_protection(
    standalone_capability_schema,
):
    """A late FK validation failure rolls back indexes, constraints and triggers."""

    raw, schema = standalone_capability_schema
    cursor = raw.cursor()
    cursor.execute(
        "INSERT INTO unified_capabilities "
        "(id, name, code, level, version, scope, organization_id, source_org_id) "
        "VALUES (301, 'Orphan provenance', 'ATOMIC', 1, 1, 'reference', NULL, 999999)"
    )
    cursor.execute(
        "ALTER TABLE unified_capabilities ADD CONSTRAINT unified_capabilities_code_key "
        "UNIQUE (code)"
    )
    cursor.execute(
        "ALTER TABLE unified_capabilities "
        "ADD CONSTRAINT unified_capabilities_archimate_id_key UNIQUE (archimate_id)"
    )

    with pytest.raises(Exception):
        cursor.execute(_standalone_sql("capability_tenancy_cutover.sql"))
    cursor.execute("ROLLBACK")
    cursor.execute(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'unified_capabilities'::regclass "
        "AND conname IN ('unified_capabilities_code_key', "
        "'unified_capabilities_archimate_id_key') ORDER BY conname"
    )
    assert [row[0] for row in cursor.fetchall()] == [
        "unified_capabilities_archimate_id_key",
        "unified_capabilities_code_key",
    ]
    cursor.execute(
        "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'unified_capabilities'::regclass "
        "AND tgname = 'trg_unified_capability_write_scope' AND NOT tgisinternal"
    )
    assert cursor.fetchone()[0] == 0


def test_standalone_apply_and_reverse_are_atomic_and_reverse_refuses_before_mutation(
    standalone_capability_schema,
):
    """Executable SQL covers happy apply, refusal ordering, and happy reverse."""

    raw, schema = standalone_capability_schema
    cursor = raw.cursor()
    cursor.execute(
        "INSERT INTO organizations (id, name, slug) VALUES "
        "(401, 'Org 401', 'org-401'), (402, 'Org 402', 'org-402')"
    )
    cursor.execute(
        "INSERT INTO unified_capabilities "
        "(id, name, code, level, version, scope, organization_id) VALUES "
        "(401, 'Tenant 401', 'REV-A', 1, 1, 'tenant', 401), "
        "(402, 'Tenant 402', 'REV-B', 1, 1, 'tenant', 402)"
    )
    cursor.execute(_standalone_sql("capability_tenancy_cutover.sql"))
    cursor.execute("UPDATE unified_capabilities SET code = 'REV-DUP' WHERE id IN (401, 402)")

    with pytest.raises(Exception, match="reverse refused"):
        cursor.execute(_standalone_sql("capability_tenancy_reverse.sql"))
    cursor.execute("ROLLBACK")
    cursor.execute(
        "SELECT count(*) FROM pg_indexes WHERE schemaname = %s "
        "AND indexname LIKE 'uq_unified_capabilities_%%'",
        (schema,),
    )
    assert cursor.fetchone()[0] == 4
    cursor.execute(
        "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'unified_capabilities'::regclass "
        "AND tgname = 'trg_unified_capability_write_scope' AND NOT tgisinternal"
    )
    assert cursor.fetchone()[0] == 1

    cursor.execute("UPDATE unified_capabilities SET code = 'REV-B' WHERE id = 402")
    cursor.execute(_standalone_sql("capability_tenancy_reverse.sql"))
    cursor.execute(
        "SELECT indexname FROM pg_indexes WHERE schemaname = %s "
        "AND indexname IN ('ix_unified_capabilities_code', "
        "'ix_unified_capabilities_archimate_id') ORDER BY indexname",
        (schema,),
    )
    assert [row[0] for row in cursor.fetchall()] == [
        "ix_unified_capabilities_archimate_id",
        "ix_unified_capabilities_code",
    ]


def test_apply_holds_relationship_table_locks_until_transaction_end(
    standalone_capability_schema, tmp_path, app
):
    """A concurrent relationship reassignment cannot race classification."""
    from app import db

    raw, schema = standalone_capability_schema
    seed = raw.cursor()
    seed.execute(
        "INSERT INTO organizations (id, name, slug) VALUES (501, 'Org 501', 'org-501')"
    )
    seed.execute(
        "INSERT INTO unified_capabilities "
        "(id, name, code, level, version, source_table, source_id, source_org_id, source_checksum) "
        "VALUES (501, 'Locked', 'LOCKED', 1, 1, 'tenant_import', '501', 501, %s)",
        ("c" * 64,),
    )
    seed.execute(
        "INSERT INTO application_components (id, name, organization_id) "
        "VALUES (501, 'Locked app', 501)"
    )
    seed.execute(
        "INSERT INTO unified_application_capability_mapping "
        "(id, unified_capability_id, application_component_id, is_active) "
        "VALUES (501, 501, 501, true)"
    )
    manifest = tmp_path / "backup.json"
    manifest.write_text(json.dumps({"backup_path": "C:/backups/capabilities.dump"}))

    with db.engine.connect() as primary:
        transaction = primary.begin()
        primary.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        run_cutover(primary, apply=True, backup_manifest=manifest)

        contender = db.engine.raw_connection()
        contender.driver_connection.autocommit = True
        try:
            competing = contender.cursor()
            competing.execute(f'SET search_path TO "{schema}", public')
            competing.execute("SET statement_timeout = '150ms'")
            with pytest.raises(Exception, match="statement timeout"):
                competing.execute(
                    "UPDATE unified_application_capability_mapping "
                    "SET unified_capability_id = 501 WHERE id = 501"
                )
        finally:
            contender.cursor().execute("RESET ALL")
            contender.driver_connection.autocommit = False
            contender.close()
            transaction.rollback()
