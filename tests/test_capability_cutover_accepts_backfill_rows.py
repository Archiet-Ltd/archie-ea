"""Regression: the tenancy cutover must accept rows an earlier deploy's
project-capabilities and backfill-capability-catalogs already classified.

Production sequence that exposed this: a deploy with no backup marker
skipped the cutover but still ran project-capabilities and
backfill-capability-catalogs, each of which sets scope/organization_id
directly on every row it writes. The next deploy, once a backup marker was
available, ran the cutover first and it refused with "capabilities have
ambiguous active links" -- the classifier re-derives scope from relationship
links and provenance alone, ignoring any scope already set, and a
reference-scope row from the catalogue backfill (no relationship owners,
source_org_id NULL by design) had no branch recognising it as already done.

Each test works in a transaction-local cloned schema, following
tests/test_capability_projection.py and tests/test_capability_catalog_backfill.py's
established pattern: the shared test database's real unified_capabilities is
never written to here.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.commands.backfill_capability_catalogs import run_backfill
from app.commands.cutover_capability_tenancy import (
    CutoverBlocked,
    install_cutover_constraints,
    run_cutover,
)
from app.commands.project_capabilities import PROVENANCE_INDEX, run_projection


pytestmark = pytest.mark.usefixtures("db_session")


_CLONED_TABLES = (
    "organizations",
    "business_capability",
    "capabilities",
    "enterprise_capabilities",
    "archimate_capabilities",
    "technical_capabilities",
    "unified_capabilities",
)


@pytest.fixture
def cutover_schema(db_session):
    """Clone the tables the full project -> backfill -> cutover chain touches.

    Deliberately does NOT pre-install ck_unified_capabilities_scope_owner:
    production's first deploy ran project-capabilities and the catalogue
    backfill against a database that had never been cut over, and this
    reproduces that -- the cutover installs the constraint for the first
    time here, exactly as it did (eventually) in production.
    """

    schema = f"capcut2_{uuid.uuid4().hex[:12]}"
    connection = db_session.connection()
    connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    for table in _CLONED_TABLES:
        connection.execute(
            text(
                f'CREATE TABLE "{schema}"."{table}" '
                f'(LIKE public."{table}" INCLUDING DEFAULTS INCLUDING CONSTRAINTS)'
            )
        )
    connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
    for table in _CLONED_TABLES:
        connection.execute(text(f'ALTER TABLE "{table}" ADD PRIMARY KEY (id)'))

    # The four partial unique indexes the model declares
    # (app/models/unified_capability.py), present so a real collision behaves
    # here exactly as it would in production.
    connection.execute(text(
        "CREATE UNIQUE INDEX ON unified_capabilities (organization_id, code) "
        "WHERE organization_id IS NOT NULL"
    ))
    connection.execute(text(
        "CREATE UNIQUE INDEX ON unified_capabilities (code) "
        "WHERE organization_id IS NULL"
    ))
    connection.execute(text(
        "CREATE UNIQUE INDEX ON unified_capabilities (organization_id, archimate_id) "
        "WHERE organization_id IS NOT NULL AND archimate_id IS NOT NULL"
    ))
    connection.execute(text(
        "CREATE UNIQUE INDEX ON unified_capabilities (archimate_id) "
        "WHERE organization_id IS NULL AND archimate_id IS NOT NULL"
    ))
    # project-capabilities refuses to run without this (its own idempotency
    # arbiter on (source_table, source_id)).
    connection.execute(text(
        f"CREATE UNIQUE INDEX {PROVENANCE_INDEX} ON unified_capabilities "
        "(source_table, source_id) WHERE source_table IS NOT NULL AND source_id IS NOT NULL"
    ))
    return connection


def _make_org(connection, org_id: int, name: str) -> int:
    connection.execute(
        text("INSERT INTO organizations (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": org_id, "name": name, "slug": f"{name}-{org_id}"},
    )
    return org_id


def _insert_business_capability(connection, *, id, org_id, name):
    connection.execute(
        text(
            "INSERT INTO business_capability (id, organization_id, name, level, is_deprecated) "
            "VALUES (:id, :org_id, :name, 1, false)"
        ),
        {"id": id, "org_id": org_id, "name": name},
    )


def _insert_technical(connection, *, id, name):
    connection.execute(
        text(
            "INSERT INTO technical_capabilities (id, name, acm_domain, level) "
            "VALUES (:id, :name, 'APPLICATION-SERVICES', 'L1')"
        ),
        {"id": id, "name": name},
    )


def test_cutover_accepts_rows_the_backfill_already_resolved_two_organisations(
    cutover_schema, tmp_path
):
    """The exact production sequence: project-capabilities and the catalogue
    backfill run first (as they did in the deploy with no backup marker), the
    cutover runs after (as it did once a backup marker became available), and
    it must not refuse rows either of them already classified correctly."""

    connection = cutover_schema
    org_a = _make_org(connection, 9801, "org-a")
    org_b = _make_org(connection, 9802, "org-b")

    # Tenant data in two real organisations.
    _insert_business_capability(connection, id=1, org_id=org_a, name="Order Management")
    _insert_business_capability(connection, id=2, org_id=org_b, name="Claims Handling")
    # Reference catalogue data the backfill's one positive marker resolves.
    for i in range(1, 4):
        _insert_technical(connection, id=i, name=f"Technical Capability {i}")

    projection = run_projection(connection, apply=True, row_limit=None)
    assert projection["writes"]["inserted_or_updated"] == 2

    backfill = run_backfill(connection, apply=True)
    assert backfill["canonical"] == 3
    assert backfill["quarantined"] == 0

    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"backup_path": "/tmp/fake.dump"}')

    # Before the fix this raised CutoverBlocked("... ambiguous active
    # links..."). After it, every row project-capabilities and the backfill
    # already scoped correctly is accepted without reclassification.
    report = run_cutover(connection, apply=True, backup_manifest=manifest)

    assert report["counts"]["ambiguous"] == 0
    assert report["already_classified"] == 5
    assert report["malformed_existing_scope"] == 0
    assert report["constraint_swap"] is True

    rows = connection.execute(
        text("SELECT id, scope, organization_id, source_table FROM unified_capabilities ORDER BY id")
    ).mappings().all()
    assert len(rows) == 5
    tenant_rows = {row["id"]: row["organization_id"] for row in rows if row["scope"] == "tenant"}
    reference_rows = [row for row in rows if row["scope"] == "reference"]
    assert len(tenant_rows) == 2
    assert len(reference_rows) == 3
    # Each organisation's own row stays its own -- never merged or reattributed.
    assert org_a in tenant_rows.values()
    assert org_b in tenant_rows.values()
    assert all(row["organization_id"] is None for row in reference_rows)

    # Zero rows left in the pre-contract NULL/NULL state.
    assert connection.execute(
        text(
            "SELECT count(*) FROM unified_capabilities "
            "WHERE organization_id IS NULL AND scope IS NULL"
        )
    ).scalar_one() == 0

    # The constraint the cutover installs is live and enforced.
    constraint = connection.execute(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = to_regclass('unified_capabilities') "
            "AND conname = 'ck_unified_capabilities_scope_owner'"
        )
    ).scalar_one_or_none()
    assert constraint == "ck_unified_capabilities_scope_owner"


def test_cutover_still_refuses_a_row_with_an_inconsistent_existing_scope(cutover_schema, tmp_path):
    """Trusting an already-set scope is validated, not assumed: a row whose
    existing scope/organization_id pairing is inconsistent must still block
    the cutover by name, never sail through to publish a tenant row to every
    organisation."""

    connection = cutover_schema
    org = _make_org(connection, 9811, "org-a")
    # A row claiming 'reference' scope but carrying a real owner -- exactly
    # the shape that would make a tenant row readable by every organisation
    # if silently trusted.
    connection.execute(
        text(
            "INSERT INTO unified_capabilities (id, name, level, scope, organization_id) "
            "VALUES (1, 'Malformed Row', 1, 'reference', :org_id)"
        ),
        {"org_id": org},
    )

    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"backup_path": "/tmp/fake.dump"}')

    with pytest.raises(CutoverBlocked) as blocked:
        run_cutover(connection, apply=True, backup_manifest=manifest)

    assert "inconsistent" in str(blocked.value)
    assert "id=1" in str(blocked.value)
    # Nothing was installed -- the constraint swap never started.
    constraint = connection.execute(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = to_regclass('unified_capabilities') "
            "AND conname = 'ck_unified_capabilities_scope_owner'"
        )
    ).scalar_one_or_none()
    assert constraint is None


def test_cutover_still_refuses_a_genuinely_ambiguous_scope_null_row(cutover_schema, tmp_path):
    """The fix narrows which rows skip reclassification; it must not weaken
    classify_capability's own refusal for a row that is still genuinely
    ambiguous (scope IS NULL, no relationship owner, no usable provenance)."""

    connection = cutover_schema
    connection.execute(
        text(
            "INSERT INTO unified_capabilities (id, name, level, source_table, source_id) "
            "VALUES (1, 'Orphan Row', 1, 'hand_rolled_import', '1')"
        )
    )

    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"backup_path": "/tmp/fake.dump"}')

    with pytest.raises(CutoverBlocked) as blocked:
        run_cutover(connection, apply=True, backup_manifest=manifest)

    assert "ambiguous active links" in str(blocked.value)
