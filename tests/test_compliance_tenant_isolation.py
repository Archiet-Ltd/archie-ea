"""Cross-tenant isolation for compliance_policies/compliance_violations.

Before TenantMixin, `enterprise_crud_routes.py` queried/counted/created
CompliancePolicy and ComplianceViolation rows with no org filter at all, and
CompliancePolicy.name carried a GLOBAL unique=True — two organizations could
not both have a "NIST" policy, the same cross-tenant collision shape closed
for SSOGroupRoleMapping (PR#102). ComplianceControl is deliberately NOT
touched here: it is global regulatory-framework reference data (NIST-800-53
AC-2, ISO 27001 A.9.2.1 etc.), not per-org data — see its own docstring.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _make_policy(db_session, org_id, name):
    from app.models.compliance_models import CompliancePolicy

    row = CompliancePolicy(name=name, policy_type="NIST", organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _make_violation(db_session, org_id, policy_id, description="test violation"):
    from app.models.compliance_models import ComplianceViolation

    row = ComplianceViolation(
        policy_id=policy_id,
        description=description,
        organization_id=org_id,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_compliance_policy_select_is_scoped_to_current_org(db_session, make_org, tenant_ctx):
    """The specific leak this fix closes: org A must not see org B's compliance
    policies when enterprise_crud_routes.py queries CompliancePolicy with no filter."""
    from app.models.compliance_models import CompliancePolicy

    org_a, org_b = make_org("a"), make_org("b")
    _make_policy(db_session, org_a.id, "NIST 800-53")
    b_policy = _make_policy(db_session, org_b.id, "ISO 27001")

    with tenant_ctx(org_a.id):
        visible_ids = {p.id for p in CompliancePolicy.query.all()}

    assert b_policy.id not in visible_ids, (
        "TENANT LEAK: org A can see org B's compliance policy."
    )


def test_two_orgs_can_use_the_same_policy_name(db_session, make_org, tenant_ctx):
    """Direct regression for the collision the old global unique=True caused:
    both orgs must be able to have their own "NIST" policy independently."""
    from app.models.compliance_models import CompliancePolicy

    org_a, org_b = make_org("a"), make_org("b")
    a_policy = _make_policy(db_session, org_a.id, "NIST")
    b_policy = _make_policy(db_session, org_b.id, "NIST")

    assert a_policy.id != b_policy.id
    with tenant_ctx(org_a.id):
        assert {p.id for p in CompliancePolicy.query.filter_by(name="NIST").all()} == {a_policy.id}
    with tenant_ctx(org_b.id):
        assert {p.id for p in CompliancePolicy.query.filter_by(name="NIST").all()} == {b_policy.id}


def test_compliance_violation_select_is_scoped_to_current_org(db_session, make_org, tenant_ctx):
    """Violations carry affected_system/root_cause/evidence_link — real
    per-org security-posture data, not reference content."""
    from app.models.compliance_models import ComplianceViolation

    org_a, org_b = make_org("a"), make_org("b")
    a_policy = _make_policy(db_session, org_a.id, "NIST")
    b_policy = _make_policy(db_session, org_b.id, "NIST")
    _make_violation(db_session, org_a.id, a_policy.id, "org a violation")
    b_violation = _make_violation(db_session, org_b.id, b_policy.id, "org b violation")

    with tenant_ctx(org_a.id):
        visible_ids = {v.id for v in ComplianceViolation.query.all()}

    assert b_violation.id not in visible_ids, (
        "TENANT LEAK: org A can read org B's compliance violation, "
        "including affected_system/root_cause/evidence_link."
    )


def test_compliance_control_remains_global_reference_data(db_session, make_org, tenant_ctx):
    """ComplianceControl is deliberately NOT tenant-scoped — it is the shared
    regulatory-framework catalog (control_code/title per framework), not
    per-org data. This test documents that decision so a future session
    doesn't "fix" it without re-reading why."""
    from app.models.compliance_models import ComplianceControl

    assert not hasattr(ComplianceControl, "organization_id"), (
        "ComplianceControl gained an organization_id — if this is intentional, "
        "update this test and the tier-1 checklist row together; if not, revert."
    )


# --- HTTP-level coverage of the routes this fix is about ---------------------


def _officer(db_session, org, label):
    from app.models.user import User

    user = User(
        email=f"officer-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Comp",
        last_name="Officer",
        confirmed=True,
        organization_id=org.id,
        role_archetype="compliance_officer",
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_policy_list_route_shows_only_the_callers_organisation(
    db_session, make_org, client, login_as
):
    org_a, org_b = make_org("a"), make_org("b")
    _make_policy(db_session, org_a.id, "Org A policy")
    _make_policy(db_session, org_b.id, "Org B policy")
    login_as(client, _officer(db_session, org_a, "a"))

    resp = client.get("/enterprise/compliance/policies")

    body = resp.get_data(as_text=True)
    assert resp.status_code == 200, body
    assert "Org A policy" in body
    assert "Org B policy" not in body, "TENANT LEAK: the list route shows another org's policy"


def test_two_organisations_can_create_the_same_policy_name_over_http(
    db_session, make_org, client, login_as
):
    org_a, org_b = make_org("a"), make_org("b")
    user_a, user_b = _officer(db_session, org_a, "a"), _officer(db_session, org_b, "b")
    payload = {"name": "NIST", "type": "NIST"}

    login_as(client, user_a)
    first = client.post("/enterprise/compliance/policies", json=payload)
    again = client.post("/enterprise/compliance/policies", json=payload)
    login_as(client, user_b)
    other_org = client.post("/enterprise/compliance/policies", json=payload)

    assert first.status_code == 201, first.get_data(as_text=True)
    assert again.status_code == 409, "the same organisation still cannot repeat a name"
    assert other_org.status_code == 201, (
        "a different organisation must be able to use the same policy name"
    )


def test_a_raced_duplicate_is_a_409_not_a_500(db_session, make_org, client, login_as):
    """Two same-organisation requests can both pass the duplicate check. The
    per-organisation unique constraint refuses the second at commit; that must
    read as the same 409 the pre-check gives, not a generic 500."""
    from unittest.mock import MagicMock, patch

    from app.models.compliance_models import CompliancePolicy

    org = make_org("race")
    _make_policy(db_session, org.id, "NIST")
    login_as(client, _officer(db_session, org, "race"))

    no_match = MagicMock()
    no_match.filter_by.return_value.first.return_value = None  # the check "misses"
    with patch.object(CompliancePolicy, "query", no_match):
        resp = client.post("/enterprise/compliance/policies", json={"name": "NIST", "type": "NIST"})

    assert resp.status_code == 409, resp.get_data(as_text=True)


# --- backfill: a policy whose violations span organisations is never guessed --


@pytest.fixture
def nullable_tenant_columns(app):
    """Let compliance rows exist without an organisation, as they do on a
    database that predates the fix.

    The column is NOT NULL on a fresh database (and nullable once
    reconcile-schema has run). This changes it on its own autocommit connection,
    before the test's transaction opens and after it has rolled back -- doing the
    DDL inside the test's transaction would hold an exclusive table lock that the
    backfill's own engine-level introspection then waits on forever. It must be
    requested BEFORE db_session so it is torn down AFTER db_session rolls back.
    """
    from sqlalchemy import text

    from app import db

    tables = ("compliance_policies", "compliance_violations")
    changed = []
    with app.app_context():
        with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            for table in tables:
                not_null = conn.execute(
                    text(
                        "SELECT attnotnull FROM pg_attribute "
                        "WHERE attrelid = CAST(:t AS regclass) AND attname = 'organization_id'"
                    ),
                    {"t": table},
                ).scalar()
                if not_null:
                    conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN organization_id DROP NOT NULL"))
                    changed.append(table)
    yield
    with app.app_context():
        with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            for table in changed:
                conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN organization_id SET NOT NULL"))


def test_backfill_leaves_a_policy_null_when_its_violations_span_organisations(
    nullable_tenant_columns, db_session, make_org
):
    from sqlalchemy import text

    from app import db
    from app.commands.reconcile_schema import _backfill_compliance_organizations
    from app.models.compliance_models import CompliancePolicy, ComplianceViolation

    org_a, org_b = make_org("a"), make_org("b")
    user_a, user_b = _officer(db_session, org_a, "a"), _officer(db_session, org_b, "b")
    shared = CompliancePolicy(name="Shared", policy_type="NIST", organization_id=None)
    single = CompliancePolicy(name="Single", policy_type="NIST", organization_id=None)
    db_session.add_all([shared, single])
    db_session.flush()
    for policy, user in ((shared, user_a), (shared, user_b), (single, user_a)):
        db_session.add(
            ComplianceViolation(
                policy_id=policy.id, description="x", created_by_id=user.id, organization_id=None
            )
        )
    db_session.flush()

    added, failed = [], []
    _backfill_compliance_organizations(
        dry_run=False,
        existing_tables={"compliance_policies", "compliance_violations", "users"},
        added=added,
        failed=failed,
    )

    org_of = {
        row[0]: row[1]
        for row in db.session.execute(
            text("SELECT name, organization_id FROM compliance_policies WHERE name IN ('Shared','Single')")
        )
    }
    assert org_of["Single"] == org_a.id, "an unambiguous policy is resolved"
    assert org_of["Shared"] is None, "an ambiguous policy must be left NULL, not guessed"
    assert any("more than one organisation" in line for line in failed), failed
