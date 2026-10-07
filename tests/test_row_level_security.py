"""Row-level security enforcement tests (PR 3).

These tests verify that the database itself enforces tenant isolation via
PostgreSQL row-level security policies, as a second layer behind the ORM-level
do_orm_execute filter. The key property: even raw SQL that forgets the
organisation predicate returns only the session organisation's rows.

Two organisations, each with their own rows. The session organisation is set
via set_database_tenant_context (the same mechanism the ORM listeners use).

RLS only enforces for non-superusers. These tests:
- Create test data as postgres (superuser, bypasses RLS)
- Switch to archie_app role (non-superuser) for the actual test assertions
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture(autouse=True)
def _reset_role_after_test(db_session):
    """Every test in this file switches role away from postgres (SET ROLE
    archie_app/archie_platform) to exercise RLS as a non-superuser. Without
    an unconditional reset here, a role left switched by one test leaks into
    the next test's own setup (its make_org()/make_user() calls run before
    that test's own leading RESET ROLE line), which fails with 'permission
    denied' on an ordinary table the leaked role was never granted.
    """
    yield
    db_session.execute(text("RESET ROLE"))


def _make_app_component_as_postgres(db_session, org_id, name):
    """Insert an ApplicationComponent as postgres (bypasses RLS)."""
    from app.models.application_portfolio import ApplicationComponent

    row = ApplicationComponent(name=name, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _make_reference_model_as_postgres(db_session, org_id, name, code):
    """Insert a ReferenceModel as postgres (bypasses RLS)."""
    from app.models.reference_models import ReferenceModel

    row = ReferenceModel(name=name, code=code, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _set_session_org(db_session, org_id):
    """Set the session organisation for the current transaction.

    This is the same function the ORM listeners call. It uses
    SET LOCAL (via set_config(..., true)) so the value is transaction-scoped
    and clears on commit/rollback.
    """
    from app.middleware.tenant_isolation import set_database_tenant_context

    set_database_tenant_context(db_session.connection(), org_id)


@pytest.fixture(autouse=True)
def _set_app_role(db_session):
    """Run RLS tests as the application role (non-superuser) so policies enforce.

    The test database connects as postgres (superuser), which bypasses RLS.
    SET ROLE archie_app switches to a non-superuser role for the transaction.
    """
    db_session.execute(text("SET ROLE archie_app"))
    yield
    db_session.rollback()  # Clear any failed transaction state
    db_session.execute(text("RESET ROLE"))


# --------------------------------------------------------------------------- #
# Raw SQL without org predicate — must return only session org's rows
# --------------------------------------------------------------------------- #


def test_raw_sql_without_org_predicate_is_scoped_by_rls(db_session, make_org):
    """Raw SQL with no WHERE organization_id clause returns only the session org.

    This is the core RLS guarantee: the database refuses a query that forgets
    the organisation. The ORM listener (do_orm_execute) adds the predicate for
    ORM queries; RLS adds it for *every* query, including raw SQL.
    """
    from app.models.application_portfolio import ApplicationComponent

    db_session.execute(text("RESET ROLE"))
    org_a, org_b = make_org("rls-a"), make_org("rls-b")
    org_a_id, org_b_id = org_a.id, org_b.id  # capture before any post-commit expiry reload

    # Create data as postgres (bypasses RLS)
    a_row = _make_app_component_as_postgres(db_session, org_a_id, "App A")
    b_row = _make_app_component_as_postgres(db_session, org_b_id, "App B")
    a_id, b_id = a_row.id, b_row.id  # Capture IDs before commit/role switch
    db_session.commit()
    db_session.execute(text("SET ROLE archie_app"))

    # Set session to org A
    _set_session_org(db_session, org_a_id)

    # Raw SQL with NO organisation predicate — RLS must filter it
    result = db_session.execute(
        text("SELECT id, name FROM application_components")
    ).fetchall()

    ids = {row[0] for row in result}
    assert a_id in ids, "own org's row must be visible"
    assert b_id not in ids, (
        "RLS FAILURE: raw SQL without org predicate returned another org's row. "
        "The row-level security policy on application_components is not enforcing."
    )


def test_raw_sql_update_without_org_predicate_is_scoped_by_rls(db_session, make_org):
    """Raw SQL UPDATE without org predicate only affects session org's rows."""
    from app.models.application_portfolio import ApplicationComponent

    db_session.execute(text("RESET ROLE"))
    org_a, org_b = make_org("rls-upd-a"), make_org("rls-upd-b")
    org_a_id, org_b_id = org_a.id, org_b.id  # capture before any post-commit expiry reload

    # Create data as postgres
    a_row = _make_app_component_as_postgres(db_session, org_a_id, "Original A")
    b_row = _make_app_component_as_postgres(db_session, org_b_id, "Original B")
    a_id, b_id = a_row.id, b_row.id  # Capture IDs before commit/role switch
    db_session.commit()
    db_session.execute(text("SET ROLE archie_app"))

    # Set session to org A
    _set_session_org(db_session, org_a_id)

    # Raw SQL UPDATE with NO organisation predicate
    db_session.execute(
        text("UPDATE application_components SET name = 'Updated by A'")
    )
    db_session.commit()

    # Verify as org B — their row must be unchanged
    _set_session_org(db_session, org_b_id)
    b_after = db_session.execute(
        text("SELECT name FROM application_components WHERE id = :id"),
        {"id": b_id},
    ).scalar()
    assert b_after == "Original B", (
        "RLS FAILURE: raw UPDATE without org predicate modified another org's row."
    )

    # Verify as org A — their row must be updated
    _set_session_org(db_session, org_a_id)
    a_after = db_session.execute(
        text("SELECT name FROM application_components WHERE id = :id"),
        {"id": a_id},
    ).scalar()
    assert a_after == "Updated by A", "own org's row must be updated"


def test_raw_sql_delete_without_org_predicate_is_scoped_by_rls(db_session, make_org):
    """Raw SQL DELETE without org predicate only affects session org's rows."""
    from app.models.application_portfolio import ApplicationComponent

    db_session.execute(text("RESET ROLE"))
    org_a, org_b = make_org("rls-del-a"), make_org("rls-del-b")
    org_a_id, org_b_id = org_a.id, org_b.id  # capture before any post-commit expiry reload

    # Create data as postgres
    a_row = _make_app_component_as_postgres(db_session, org_a_id, "App A")
    b_row = _make_app_component_as_postgres(db_session, org_b_id, "App B")
    a_id, b_id = a_row.id, b_row.id  # Capture IDs before commit/role switch
    db_session.commit()
    db_session.execute(text("SET ROLE archie_app"))

    # Set session to org A
    _set_session_org(db_session, org_a_id)

    # Raw SQL DELETE with NO organisation predicate
    db_session.execute(text("DELETE FROM application_components"))
    db_session.commit()

    # Verify as org B — their row must still exist
    _set_session_org(db_session, org_b_id)
    b_exists = db_session.execute(
        text("SELECT 1 FROM application_components WHERE id = :id"),
        {"id": b_id},
    ).scalar()
    assert b_exists == 1, (
        "RLS FAILURE: raw DELETE without org predicate removed another org's row."
    )

    # Verify as org A — their row must be gone
    _set_session_org(db_session, org_a_id)
    a_exists = db_session.execute(
        text("SELECT 1 FROM application_components WHERE id = :id"),
        {"id": a_id},
    ).scalar()
    assert a_exists is None, "own org's row must be deleted"


# --------------------------------------------------------------------------- #
# CLI / scheduler / worker path that forgets session org — fails closed
# --------------------------------------------------------------------------- #


def test_cli_path_without_tenant_scope_fails_closed(db_session, make_org):
    """A CLI-style code path that forgets to set the session org sees nothing.

    With RLS enabled and FORCE ROW LEVEL SECURITY, a query run without
    setting archie.organization_id returns zero rows (the setting is NULL,
    and organization_id = NULL is never true). This is "fail closed" —
    better than returning every organisation's rows.
    """
    from app.models.application_portfolio import ApplicationComponent

    db_session.execute(text("RESET ROLE"))
    org_a, org_b = make_org("cli-a"), make_org("cli-b")
    org_a_id, org_b_id = org_a.id, org_b.id  # capture before any post-commit expiry reload

    # Create data as postgres (bypasses RLS)
    _make_app_component_as_postgres(db_session, org_a_id, "App A")
    _make_app_component_as_postgres(db_session, org_b_id, "App B")
    db_session.commit()
    db_session.execute(text("SET ROLE archie_app"))

    # NO tenant context set — simulating a CLI command that forgot tenant_scope
    # The setting archie.organization_id is NULL (or empty string)
    result = db_session.execute(
        text("SELECT id FROM application_components")
    ).fetchall()

    assert len(result) == 0, (
        "FAIL OPEN: a query with no session organisation returned rows. "
        "RLS with FORCE ROW LEVEL SECURITY must return zero rows when "
        "archie.organization_id is not set, not all rows."
    )


def test_scheduler_job_without_tenant_scope_fails_closed(db_session, make_org):
    """A scheduled job that forgets tenant_scope sees nothing, not everything."""
    from app.models.application_portfolio import ApplicationComponent

    db_session.execute(text("RESET ROLE"))
    org_a, org_b = make_org("sched-a"), make_org("sched-b")
    org_a_id, org_b_id = org_a.id, org_b.id  # capture before any post-commit expiry reload

    # Create data as postgres
    _make_app_component_as_postgres(db_session, org_a_id, "App A")
    _make_app_component_as_postgres(db_session, org_b_id, "App B")
    db_session.commit()
    db_session.execute(text("SET ROLE archie_app"))

    # Simulate a naive scheduled job: app_context but no tenant_scope
    # g.current_org_id is None, so set_database_tenant_context is not called
    result = db_session.execute(
        text("SELECT id FROM application_components")
    ).fetchall()

    assert len(result) == 0, (
        "FAIL OPEN: a scheduled job with no tenant_scope returned rows. "
        "Must fail closed (zero rows) rather than leaking all tenants."
    )


def test_worker_path_without_tenant_scope_fails_closed(db_session, make_org):
    """A background worker that forgets to resolve its organisation sees nothing."""
    from app.models.application_portfolio import ApplicationComponent

    db_session.execute(text("RESET ROLE"))
    org_a, org_b = make_org("worker-a"), make_org("worker-b")
    org_a_id, org_b_id = org_a.id, org_b.id  # capture before any post-commit expiry reload

    # Create data as postgres
    _make_app_component_as_postgres(db_session, org_a_id, "App A")
    _make_app_component_as_postgres(db_session, org_b_id, "App B")
    db_session.commit()
    db_session.execute(text("SET ROLE archie_app"))

    # Simulate a worker that was given a record ID but failed to resolve
    # the owner and call tenant_scope — no session org is set
    result = db_session.execute(
        text("SELECT id FROM application_components")
    ).fetchall()

    assert len(result) == 0, (
        "FAIL OPEN: a worker with no tenant_scope returned rows. "
        "Must fail closed (zero rows) rather than leaking all tenants."
    )


# --------------------------------------------------------------------------- #
# HybridTenantMixin tables — shared rows visible, foreign tenant rows hidden
# --------------------------------------------------------------------------- #


def test_hybrid_table_shared_rows_visible_to_all_tenants(db_session, make_org):
    """Shared catalogue rows (organization_id IS NULL) are readable by every org."""
    from app.models.reference_models import ReferenceModel

    db_session.execute(text("RESET ROLE"))
    org_a, org_b = make_org("hybrid-a"), make_org("hybrid-b")
    org_a_id, org_b_id = org_a.id, org_b.id  # capture before any post-commit expiry reload

    # Create shared row as postgres (bypasses RLS)
    shared = _make_reference_model_as_postgres(db_session, None, "Shared Reference", f"SHARED-{uuid.uuid4().hex[:8]}")
    shared_id = shared.id
    db_session.commit()
    db_session.execute(text("SET ROLE archie_app"))

    # Org A reads
    _set_session_org(db_session, org_a_id)
    result_a = db_session.execute(
        text("SELECT id FROM reference_model WHERE id = :id"),
        {"id": shared_id},
    ).fetchall()
    assert len(result_a) == 1, "org A must see shared row"

    # Org B reads
    _set_session_org(db_session, org_b_id)
    result_b = db_session.execute(
        text("SELECT id FROM reference_model WHERE id = :id"),
        {"id": shared_id},
    ).fetchall()
    assert len(result_b) == 1, "org B must see shared row"


def test_hybrid_table_foreign_tenant_rows_hidden(db_session, make_org):
    """A tenant's override rows are not visible to another tenant."""
    from app.models.reference_models import ReferenceModel

    db_session.execute(text("RESET ROLE"))
    org_a, org_b = make_org("hybrid-a"), make_org("hybrid-b")
    org_a_id, org_b_id = org_a.id, org_b.id  # capture before any post-commit expiry reload

    # Create override rows as postgres
    a_override = _make_reference_model_as_postgres(db_session, org_a_id, "Org A Override", f"A-{uuid.uuid4().hex[:8]}")
    b_override = _make_reference_model_as_postgres(db_session, org_b_id, "Org B Override", f"B-{uuid.uuid4().hex[:8]}")
    a_id, b_id = a_override.id, b_override.id
    db_session.commit()
    db_session.execute(text("SET ROLE archie_app"))

    # Org A reads — must see own override, not B's
    _set_session_org(db_session, org_a_id)
    result = db_session.execute(
        text("SELECT id, organization_id FROM reference_model WHERE id IN (:a, :b)"),
        {"a": a_id, "b": b_id},
    ).fetchall()
    ids = {row[0] for row in result}
    org_ids = {row[1] for row in result}
    assert a_id in ids, "org A must see its own override"
    assert b_id not in ids, "org A must NOT see org B's override"
    assert org_ids == {org_a_id}, "only org A's rows returned"


def test_hybrid_table_tenant_cannot_write_shared_row(db_session, make_org):
    """A tenant cannot INSERT/UPDATE/DELETE a shared (organization_id IS NULL) row.

    INSERT with NULL organization_id is rejected by WITH CHECK policy (raises).
    UPDATE/DELETE on shared rows affect 0 rows because USING clause filters them out.
    """
    from app.models.reference_models import ReferenceModel

    db_session.execute(text("RESET ROLE"))
    org = make_org("hybrid-write")
    org_id = org.id  # capture before any post-commit expiry reload

    # Create shared row as postgres
    shared = _make_reference_model_as_postgres(db_session, None, "Immutable Shared", f"IMM-{uuid.uuid4().hex[:8]}")
    shared_id = shared.id
    db_session.commit()
    db_session.execute(text("SET ROLE archie_app"))

    _set_session_org(db_session, org_id)

    # INSERT with organization_id IS NULL must be rejected by policy
    with pytest.raises(Exception, match="violates row-level security policy"):
        db_session.execute(
            text(
                "INSERT INTO reference_model (name, code, organization_id) "
                "VALUES ('Tenant tried shared', 'BAD', NULL)"
            )
        )
        db_session.commit()
    db_session.rollback()  # Clear aborted transaction state
    # The rollback above lands on a savepoint taken before SET ROLE archie_app
    # was issued, silently reverting the connection to the postgres superuser
    # -- which always bypasses row-level security. Both the role and the
    # tenant setting must be re-applied after any rollback in this test.
    db_session.execute(text("SET ROLE archie_app"))
    _set_session_org(db_session, org_id)

    # UPDATE shared row affects 0 rows (USING clause filters out NULL organization_id)
    result = db_session.execute(
        text("UPDATE reference_model SET name = 'Tampered' WHERE id = :id"),
        {"id": shared_id},
    )
    assert result.rowcount == 0, "UPDATE on shared row must affect 0 rows"
    db_session.commit()

    # DELETE shared row affects 0 rows (USING clause filters out NULL organization_id)
    result = db_session.execute(
        text("DELETE FROM reference_model WHERE id = :id"),
        {"id": shared_id},
    )
    assert result.rowcount == 0, "DELETE on shared row must affect 0 rows"
    db_session.commit()

    # Verify shared row is untouched
    _set_session_org(db_session, org_id)
    name = db_session.execute(
        text("SELECT name FROM reference_model WHERE id = :id"),
        {"id": shared_id},
    ).scalar()
    assert name == "Immutable Shared", "shared row must be unchanged"


# --------------------------------------------------------------------------- #
# Platform role bypasses RLS (for migrations only)
# --------------------------------------------------------------------------- #


def test_platform_role_bypasses_rls(db_session, make_org):
    """The archie_platform role (used by migrations) bypasses all RLS policies.

    This test uses SET ROLE on the existing connection to become the platform
    role and verifies it can read all organisations' rows without setting
    archie.organization_id.
    """
    from app.models.application_portfolio import ApplicationComponent

    db_session.execute(text("RESET ROLE"))
    org_a, org_b = make_org("plat-a"), make_org("plat-b")
    org_a_id, org_b_id = org_a.id, org_b.id  # capture before any post-commit expiry reload

    # Create data as postgres
    a_row = _make_app_component_as_postgres(db_session, org_a_id, "App A")
    b_row = _make_app_component_as_postgres(db_session, org_b_id, "App B")
    a_id, b_id = a_row.id, b_row.id
    db_session.commit()

    # Switch to platform role (BYPASSRLS) on the existing connection
    # The role is NOLOGIN, so we must use SET ROLE, not a new connection
    db_session.execute(text("SET ROLE archie_platform"))

    try:
        # No SET archie.organization_id — platform role bypasses RLS
        result = db_session.execute(
            text("SELECT id FROM application_components WHERE id IN (:a, :b)"),
            {"a": a_id, "b": b_id},
        ).fetchall()
        ids = {row[0] for row in result}

        assert a_id in ids, "platform role must see org A's row"
        assert b_id in ids, "platform role must see org B's row"
        assert len(ids) == 2, "platform role must see both rows (bypass RLS)"
    finally:
        # Return to archie_app for the fixture teardown
        db_session.execute(text("RESET ROLE"))
        db_session.execute(text("SET ROLE archie_app"))


# --------------------------------------------------------------------------- #
# Session organisation clears at transaction boundary
# --------------------------------------------------------------------------- #


def test_session_org_clears_at_transaction_boundary(db_session, make_org, app):
    """The archie.organization_id setting clears on commit/rollback.

    This prevents a pooled connection from carrying one request's tenant
    onward to the next.

    Uses a raw engine connection (outside the db_session fixture's savepoint
    wrapping) to prove the clearing behaviour at a real transaction boundary.
    """
    from app.models.application_portfolio import ApplicationComponent
    from app.middleware.tenant_isolation import set_database_tenant_context
    from app import db

    db_session.execute(text("RESET ROLE"))
    org = make_org("boundary")
    org_id = org.id  # capture before any post-commit expiry reload

    # Create data as postgres
    _make_app_component_as_postgres(db_session, org_id, "App")
    db_session.commit()
    db_session.execute(text("SET ROLE archie_app"))

    # Use a raw connection from the engine (not the savepoint-wrapped session)
    # to test real transaction boundary behaviour
    with db.engine.connect() as conn:
        # Transaction 1: set the org
        trans1 = conn.begin()
        set_database_tenant_context(conn, org_id)
        val1 = conn.execute(
            text("SELECT current_setting('archie.organization_id', true)")
        ).scalar()
        assert val1 == str(org_id), f"Expected {org_id}, got {val1!r}"
        trans1.commit()

        # Transaction 2: setting must be cleared
        trans2 = conn.begin()
        val2 = conn.execute(
            text("SELECT current_setting('archie.organization_id', true)")
        ).scalar()
        trans2.rollback()  # Don't leave transaction open
        assert val2 in (None, "", "0"), (
            f"archie.organization_id must clear at transaction boundary, got {val2!r}"
        )