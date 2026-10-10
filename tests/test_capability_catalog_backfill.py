"""Behavioural tests for backfilling the four remaining capability catalogues
(`capabilities`, `enterprise_capabilities`, `archimate_capabilities`,
`technical_capabilities`) into `unified_capabilities`.

Each test works in a transaction-local cloned schema, following the pattern
`tests/test_capability_projection.py` established for the sibling
`business_capability` projection: the shared test database's real
`unified_capabilities` is never written to here.

Fixtures come from `tests/conftest.py` (`db_session`, `make_org`) -- `db_session`
runs each test inside a transaction that is always rolled back.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.commands.backfill_capability_catalogs import run_backfill


pytestmark = pytest.mark.usefixtures("db_session")


_CLONED_TABLES = (
    "organizations",
    "capabilities",
    "enterprise_capabilities",
    "archimate_capabilities",
    "technical_capabilities",
    "unified_capabilities",
)


@pytest.fixture
def backfill_schema(db_session):
    """Clone only the tables this backfill touches into a disposable schema.

    ``error_events`` is deliberately NOT cloned: the schema's search_path
    falls back to ``public`` for it, and the whole transaction (including any
    row written there) is rolled back by the outer ``db_session`` fixture at
    teardown, exactly like every other test that writes ``ErrorEvent`` inside
    ``db_session``.
    """

    schema = f"capcat_{uuid.uuid4().hex[:12]}"
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
    # (app/models/unified_capability.py) so a real collision behaves here
    # exactly as it would in production.
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
    # The post-cutover scope/owner CHECK (cutover_capability_tenancy.py). Every
    # row this backfill writes must satisfy it: scope and organization_id are
    # always set together, never left NULL.
    connection.execute(text(
        "ALTER TABLE unified_capabilities "
        "DROP CONSTRAINT IF EXISTS ck_unified_capabilities_scope_owner, "
        "ADD CONSTRAINT ck_unified_capabilities_scope_owner CHECK ("
        "scope IS NOT NULL AND ("
        "(scope = 'reference' AND organization_id IS NULL) OR "
        "(scope = 'tenant' AND organization_id IS NOT NULL)))"
    ))
    return connection


def _make_org(connection, org_id: int, name: str) -> int:
    connection.execute(
        text("INSERT INTO organizations (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": org_id, "name": name, "slug": f"{name}-{org_id}"},
    )
    return org_id


def _insert_capability(connection, *, id, org_id, name, archimate_id=None):
    connection.execute(
        text(
            "INSERT INTO capabilities (id, organization_id, name, level, archimate_id, "
            "archimate_layer, source_type) "
            "VALUES (:id, :org_id, :name, 1, :archimate_id, 'strategy', 'business_capability')"
        ),
        {"id": id, "org_id": org_id, "name": name, "archimate_id": archimate_id},
    )


def _insert_enterprise(connection, *, id, name, business_capability_id=None):
    connection.execute(
        text(
            "INSERT INTO enterprise_capabilities (id, name, business_capability_id) "
            "VALUES (:id, :name, :bc_id)"
        ),
        {"id": id, "name": name, "bc_id": business_capability_id},
    )


def _insert_archimate(connection, *, id, name, archimate_id=None, business_capability_id=None):
    connection.execute(
        text(
            "INSERT INTO archimate_capabilities (id, name, archimate_id, business_capability_id) "
            "VALUES (:id, :name, :archimate_id, :bc_id)"
        ),
        {"id": id, "name": name, "archimate_id": archimate_id, "bc_id": business_capability_id},
    )


def _insert_technical(connection, *, id, name, code=None):
    connection.execute(
        text("INSERT INTO technical_capabilities (id, name, code, acm_domain, level) "
             "VALUES (:id, :name, :code, 'APPLICATION-SERVICES', 'L1')"),
        {"id": id, "name": name, "code": code},
    )


def _insert_projected_business_capability(connection, *, unified_id, business_capability_id, org_id, name):
    """Simulate a row `flask project-capabilities` already produced."""
    connection.execute(
        text(
            "INSERT INTO unified_capabilities (id, name, level, scope, organization_id, "
            "source_table, source_id, source_org_id) "
            "VALUES (:id, :name, 1, 'tenant', :org_id, 'business_capability', :source_id, :org_id)"
        ),
        {"id": unified_id, "name": name, "org_id": org_id, "source_id": str(business_capability_id)},
    )


def _retired_into(connection, table, row_id):
    return connection.execute(
        text(f"SELECT retired_into_id FROM {table} WHERE id = :id"),  # nosec B608 -- table is one of a fixed set of test literals
        {"id": row_id},
    ).scalar_one()


def _unified(connection, unified_id):
    return connection.execute(
        text("SELECT id, name, scope, organization_id, source_table, source_id "
             "FROM unified_capabilities WHERE id = :id"),
        {"id": unified_id},
    ).mappings().one()


# ------------------------------------------------------------- (a) tenancy


def test_two_organisations_each_keep_their_own_capabilities_row(backfill_schema):
    """Each organisation's `capabilities` row gets its own canonical row."""

    connection = backfill_schema
    org_a = _make_org(connection, 8101, "org-a")
    org_b = _make_org(connection, 8102, "org-b")
    _insert_capability(connection, id=1, org_id=org_a, name="Order Management", archimate_id="CAP-A")
    _insert_capability(connection, id=2, org_id=org_b, name="Order Management", archimate_id="CAP-B")

    report = run_backfill(connection, apply=True)
    assert report["merged"] == 0
    assert report["canonical"] == 2
    assert report["unreconciled"] == 0

    target_a = _retired_into(connection, "capabilities", 1)
    target_b = _retired_into(connection, "capabilities", 2)
    assert target_a != target_b, "two organisations' rows must never merge into one"

    row_a, row_b = _unified(connection, target_a), _unified(connection, target_b)
    assert row_a["organization_id"] == org_a
    assert row_b["organization_id"] == org_b
    assert row_a["scope"] == "tenant" and row_b["scope"] == "tenant"

    # Organisation B's canonical row is never returned by a query scoped to A.
    org_a_rows = connection.execute(
        text("SELECT id FROM unified_capabilities WHERE organization_id = :org_id"),
        {"org_id": org_a},
    ).scalars().all()
    assert target_b not in org_a_rows


def test_identifier_collision_across_organisations_never_merges(backfill_schema):
    """An identifier match is scoped to one organisation, even when a different
    organisation's existing row happens to carry the identical `archimate_id`
    (plausible: two independently-run legacy systems minting the same string).
    """

    connection = backfill_schema
    org_a = _make_org(connection, 8111, "org-a")
    org_b = _make_org(connection, 8112, "org-b")
    # Org B already has a capability under this identifier, from some other
    # already-projected source (e.g. business_capability).
    connection.execute(
        text(
            "INSERT INTO unified_capabilities (id, name, level, scope, organization_id, "
            "archimate_id, source_table, source_id, source_org_id) "
            "VALUES (901, 'Order Management (B)', 1, 'tenant', :org_b, 'CAP-SHARED', "
            "'business_capability', '1', :org_b)"
        ),
        {"org_b": org_b},
    )
    _insert_capability(connection, id=1, org_id=org_a, name="Order Management", archimate_id="CAP-SHARED")

    report = run_backfill(connection, apply=True)
    assert report["canonical"] == 1
    assert report["merged"] == 0

    target_a = _retired_into(connection, "capabilities", 1)
    assert target_a != 901, "org A's row must never be retired into org B's row"
    row_a = _unified(connection, target_a)
    assert row_a["organization_id"] == org_a
    assert row_a["scope"] == "tenant"


def test_rerunning_the_backfill_changes_nothing(backfill_schema):
    connection = backfill_schema
    org = _make_org(connection, 8201, "org-a")
    _insert_capability(connection, id=1, org_id=org, name="Finance Management")

    first = run_backfill(connection, apply=True)
    assert first["canonical"] == 1
    before_snapshot = connection.execute(
        text("SELECT count(*) FROM unified_capabilities")
    ).scalar_one()

    second = run_backfill(connection, apply=True)
    assert second["merged"] == 0
    assert second["canonical"] == 0
    assert second["quarantined"] == 0
    after_snapshot = connection.execute(
        text("SELECT count(*) FROM unified_capabilities")
    ).scalar_one()
    assert before_snapshot == after_snapshot


# ------------------------------------------------- (b) merge by business_capability_id


def test_archimate_capability_linked_to_a_projected_business_capability_merges(backfill_schema):
    connection = backfill_schema
    org = _make_org(connection, 8301, "org-a")
    _insert_projected_business_capability(
        connection, unified_id=500, business_capability_id=42, org_id=org, name="Claims Handling",
    )
    _insert_archimate(connection, id=1, name="Claims Handling (legacy view)", business_capability_id=42)

    report = run_backfill(connection, apply=True)
    assert report["merged"] == 1
    assert report["canonical"] == 0
    assert _retired_into(connection, "archimate_capabilities", 1) == 500
    # No new unified_capabilities row was created for the legacy duplicate.
    assert connection.execute(
        text("SELECT count(*) FROM unified_capabilities")
    ).scalar_one() == 1


def test_archimate_capability_without_a_projected_business_capability_is_quarantined(backfill_schema):
    connection = backfill_schema
    _insert_archimate(connection, id=7, name="Underwriting", business_capability_id=999)

    report = run_backfill(connection, apply=True)
    assert report["quarantined"] == 1
    # Quarantined is accounted for -- it does not, by itself, keep this
    # non-zero forever; a human resolves it at /admin/errors, not by re-running.
    assert report["unreconciled"] == 0
    assert _retired_into(connection, "archimate_capabilities", 7) is None

    from app.models.error_event import ErrorEvent

    event = ErrorEvent.query.filter_by(
        fingerprint="backfill-capability-catalogs:archimate_capabilities:7"
    ).one()
    assert event.resolved is False
    assert "business_capability #999" in event.message

    # Resolve the dependency and re-run: the row merges and the event closes.
    org = _make_org(connection, 8401, "org-a")
    _insert_projected_business_capability(
        connection, unified_id=501, business_capability_id=999, org_id=org, name="Underwriting",
    )
    second = run_backfill(connection, apply=True)
    assert second["merged"] == 1
    assert second["unreconciled"] == 0
    assert _retired_into(connection, "archimate_capabilities", 7) == 501

    # A fresh query autoflushes the pending resolution first; expiring here
    # instead would discard that unflushed change before it is ever written.
    event = ErrorEvent.query.filter_by(
        fingerprint="backfill-capability-catalogs:archimate_capabilities:7"
    ).one()
    assert event.resolved is True


# ------------------------------------------------------- (c) fail-closed ownership


def test_enterprise_and_archimate_rows_with_no_ownership_evidence_are_quarantined(backfill_schema):
    """Fail closed (lead ruling): no business_capability_id and no marker
    proving shared catalogue status must never fall back to scope='reference'
    just because the table has no organisation column.

    `enterprise_capabilities` and `archimate_capabilities` carry no field at
    all that distinguishes a genuine framework definition from a row
    describing one tenant's confidential capability, so every such row is
    quarantined rather than guessed -- verified here with two real
    organisations present, neither of which can see it.
    """

    connection = backfill_schema
    org_a = _make_org(connection, 8501, "org-a")
    org_b = _make_org(connection, 8502, "org-b")
    _insert_enterprise(connection, id=1, name="Secret Merger Capability")
    _insert_archimate(connection, id=1, name="Confidential Divestiture Plan")

    report = run_backfill(connection, apply=True)
    assert report["canonical"] == 0
    assert report["merged"] == 0
    assert report["quarantined"] == 2
    # Fail-closed is also a *terminal*, accounted-for outcome: a quarantined
    # row does not, by itself, keep the command reporting non-zero forever.
    assert report["unreconciled"] == 0

    assert _retired_into(connection, "enterprise_capabilities", 1) is None
    assert _retired_into(connection, "archimate_capabilities", 1) is None
    # Nothing was ever written for either row -- there is no unified_capabilities
    # row for either organisation, or anyone else, to read.
    assert connection.execute(
        text("SELECT count(*) FROM unified_capabilities")
    ).scalar_one() == 0
    for org_id in (org_a, org_b):
        assert connection.execute(
            text(
                "SELECT count(*) FROM unified_capabilities "
                "WHERE organization_id = :org_id "
                "AND name IN ('Secret Merger Capability', 'Confidential Divestiture Plan')"
            ),
            {"org_id": org_id},
        ).scalar_one() == 0

    from app.models.error_event import ErrorEvent

    messages = {
        event.fingerprint: event.message
        for event in ErrorEvent.query.filter(
            ErrorEvent.fingerprint.in_([
                "backfill-capability-catalogs:enterprise_capabilities:1",
                "backfill-capability-catalogs:archimate_capabilities:1",
            ])
        ).all()
    }
    assert len(messages) == 2
    for message in messages.values():
        assert "no marker identifying it as shared catalogue data" in message


def test_technical_capability_with_an_unrecognised_acm_domain_is_quarantined(backfill_schema):
    """The acm_domain marker is a closed allow-list, not a rubber stamp: a
    value outside ACMDomain.ALL_DOMAINS gets no benefit of the doubt."""

    connection = backfill_schema
    connection.execute(
        text(
            "INSERT INTO technical_capabilities (id, name, acm_domain, level) "
            "VALUES (1, 'Mystery Capability', 'NOT-A-REAL-ACM-DOMAIN', 'L1')"
        )
    )

    report = run_backfill(connection, apply=True)
    assert report["quarantined"] == 1
    assert report["canonical"] == 0
    assert report["unreconciled"] == 0
    assert _retired_into(connection, "technical_capabilities", 1) is None
    assert connection.execute(
        text("SELECT count(*) FROM unified_capabilities")
    ).scalar_one() == 0


def test_technical_capability_with_a_recognised_acm_domain_becomes_shared_reference(backfill_schema):
    """The one positive marker this backfill trusts: a pre-existing,
    code-defined allow-list (ACMDomain.ALL_DOMAINS) that already existed for
    an unrelated purpose, not one invented to justify a reference fallback.
    Verified readable by two real organisations, owned by neither.
    """

    connection = backfill_schema
    org_a = _make_org(connection, 8511, "org-a")
    org_b = _make_org(connection, 8512, "org-b")
    _insert_technical(connection, id=1, name="Message Queue Pattern", code="COMM-01")

    run_backfill(connection, apply=True)
    target = _retired_into(connection, "technical_capabilities", 1)
    row = _unified(connection, target)
    assert row["scope"] == "reference"
    assert row["organization_id"] is None

    # Readable regardless of which organisation is asking; writable by
    # neither is enforced by the existing `_protect_reference_capability_writes`
    # listener on UnifiedCapability, exercised by
    # tests/test_capability_tenancy_cutover.py and unchanged here.
    for org_id in (org_a, org_b):
        visible = connection.execute(
            text(
                "SELECT id FROM unified_capabilities "
                "WHERE id = :id AND (organization_id = :org_id OR "
                "(scope = 'reference' AND organization_id IS NULL))"
            ),
            {"id": target, "org_id": org_id},
        ).scalar_one_or_none()
        assert visible == target


def test_technical_capability_becomes_a_reference_row_carrying_its_code(backfill_schema):
    connection = backfill_schema
    _insert_technical(connection, id=1, name="API Gateway", code="APP-SVC-01")

    report = run_backfill(connection, apply=True)
    assert report["canonical"] == 1

    target = _retired_into(connection, "technical_capabilities", 1)
    row = connection.execute(
        text("SELECT scope, organization_id, code FROM unified_capabilities WHERE id = :id"),
        {"id": target},
    ).mappings().one()
    assert row["scope"] == "reference"
    assert row["organization_id"] is None
    assert row["code"] == "APP-SVC-01"


def test_technical_capability_merges_into_an_existing_row_with_the_same_code(backfill_schema):
    """`technical_capabilities.code` is already unique on its own table, so two
    rows there can never collide with each other -- the collision this proves
    is against a `unified_capabilities` reference row a different source
    (or an earlier run) already created with that code."""

    connection = backfill_schema
    connection.execute(
        text(
            "INSERT INTO unified_capabilities (id, name, level, scope, code) "
            "VALUES (900, 'API Gateway', 1, 'reference', 'APP-SVC-01')"
        )
    )
    _insert_technical(connection, id=1, name="API Gateway (ACM view)", code="APP-SVC-01")

    report = run_backfill(connection, apply=True)
    assert report["merged"] == 1
    assert report["canonical"] == 0
    assert _retired_into(connection, "technical_capabilities", 1) == 900


def test_normalised_name_fallback_merges_two_reference_rows(backfill_schema):
    """The normalised-name fallback only ever runs after a row has already
    cleared fail-closed ownership classification (here: both rows carry the
    recognised acm_domain marker), never as a way to smuggle an unowned row
    into reference scope by matching names."""

    connection = backfill_schema
    _insert_technical(connection, id=1, name="Order   Management")
    _insert_technical(connection, id=2, name="  order management  ")

    report = run_backfill(connection, apply=True)
    assert report["canonical"] == 1
    assert report["merged"] == 1
    assert report["quarantined"] == 0

    target_1 = _retired_into(connection, "technical_capabilities", 1)
    target_2 = _retired_into(connection, "technical_capabilities", 2)
    assert target_1 == target_2


def test_dry_run_writes_nothing(backfill_schema):
    connection = backfill_schema
    org = _make_org(connection, 8601, "org-a")
    _insert_capability(connection, id=1, org_id=org, name="Something")

    report = run_backfill(connection, apply=False)
    assert report["mode"] == "dry-run"
    assert report["pending_before"] == 1
    assert connection.execute(
        text("SELECT count(*) FROM unified_capabilities")
    ).scalar_one() == 0
    assert _retired_into(connection, "capabilities", 1) is None
