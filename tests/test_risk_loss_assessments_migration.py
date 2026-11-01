"""A database built from migrations has the risk yearly-loss table.

risk_loss_assessments used to be created only by init-db, so a database upgraded
with the migrations alone had nowhere to keep the ranges behind a risk's yearly
loss. These drive the deploy's own commands against throwaway databases.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.test_schema_migrations import (  # noqa: F401  (fixture re-exported)
    _conn_scalar,
    _flask,
    scratch_databases,
)

REVISION = "20261101_risk_loss_assessments"
PREVIOUS = "20261101_seed_default_roles"
TABLE = "risk_loss_assessments"

_FACTORS = '{"frequency": [1, 2, 3]}'


def _exists(url):
    return _conn_scalar(url, f"SELECT to_regclass('public.{TABLE}') IS NOT NULL")


def _owner_run(url, sql, **params):
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql), params)
            return result.scalar() if result.returns_rows else None
    finally:
        engine.dispose()


def _org(url, label):
    suffix = uuid.uuid4().hex[:8]
    return _owner_run(
        url, "INSERT INTO organizations (name, slug) VALUES (:n, :s) RETURNING id",
        n=f"{label} {suffix}", s=f"{label}-{suffix}",
    )


def _risk(url, org_id, title):
    return _owner_run(
        url, "INSERT INTO risks (title, likelihood, impact, status, organization_id) VALUES (:t, 1, 1, 'OPEN', :o) RETURNING id",
        t=title, o=org_id,
    )


def _assessment(url, org_id, risk_id):
    return _owner_run(
        url,
        f"INSERT INTO {TABLE} (organization_id, risk_id, factors, trials, seed, currency, entered_at) "
        "VALUES (:o, :r, CAST(:f AS json), 100, 1, 'GBP', now()) RETURNING id",
        o=org_id, r=risk_id, f=_FACTORS,
    )


@pytest.fixture
def migrated_db(scratch_databases):
    url = scratch_databases("riskloss")
    _flask(url, ["schema-upgrade"])
    return url


@pytest.fixture
def deployed_db(migrated_db):
    """A database as production is: at the previous revision, without the table."""
    _owner_run(migrated_db, f"DROP TABLE {TABLE}")
    _owner_run(migrated_db, f"UPDATE alembic_version SET version_num = '{PREVIOUS}'")
    _flask(migrated_db, ["db", "upgrade"])
    return migrated_db


def test_table_exists_after_upgrade_with_the_models_columns(deployed_db):
    migrated_db = deployed_db
    from app.models.risk_loss_assessment import RiskLossAssessment

    assert _exists(migrated_db) is True
    engine = create_engine(migrated_db)
    try:
        with engine.connect() as conn:
            live = {
                r[0]: r[1] for r in conn.execute(text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    f"WHERE table_schema = 'public' AND table_name = '{TABLE}'"
                ))
            }
    finally:
        engine.dispose()
    modelled = {c.name: ("YES" if c.nullable else "NO") for c in RiskLossAssessment.__table__.columns}
    assert live == modelled


def test_upgrade_downgrade_upgrade_cycle_with_data(migrated_db):
    org = _org(migrated_db, "cycle")
    risk = _risk(migrated_db, org, "Cycle risk")
    _assessment(migrated_db, org, risk)

    _flask(migrated_db, ["db", "downgrade", PREVIOUS])
    assert _exists(migrated_db) is False
    # The risk it described is untouched.
    assert _conn_scalar(migrated_db, f"SELECT count(*) FROM risks WHERE id = {risk}") == 1

    _flask(migrated_db, ["schema-upgrade"])
    assert _exists(migrated_db) is True
    assert _conn_scalar(migrated_db, f"SELECT count(*) FROM {TABLE}") == 0


def _cost_fact(url, org_id, element_type, element_id, source, source_table):
    return _owner_run(
        url,
        "INSERT INTO cost_facts (organization_id, element_type, element_id, category, kind, source, "
        "source_table, source_id, amount, currency, period) "
        "VALUES (:o, :t, :e, 'yearly_loss_likely', 'forecast', :s, :st, :e, 100, 'GBP', 'annual') RETURNING id",
        o=org_id, t=element_type, e=element_id, s=source, st=source_table,
    )


def test_downgrade_removes_the_cost_facts_derived_from_the_assessments(migrated_db):
    org = _org(migrated_db, "orphan")
    other_org = _org(migrated_db, "orphan-other")
    risk = _risk(migrated_db, org, "Orphan risk")
    other_risk = _risk(migrated_db, other_org, "Other orphan risk")
    _assessment(migrated_db, org, risk)
    _assessment(migrated_db, other_org, other_risk)
    derived = _cost_fact(migrated_db, org, "risk", risk, "open_fair", TABLE)
    derived_other = _cost_fact(migrated_db, other_org, "risk", other_risk, "open_fair", TABLE)
    unrelated = _cost_fact(migrated_db, org, "risk", risk, "manual", None)

    def count(ids):
        return _conn_scalar(
            migrated_db, "SELECT count(*) FROM cost_facts WHERE id IN (%s)" % ",".join(str(i) for i in ids))

    _flask(migrated_db, ["db", "downgrade", PREVIOUS])
    assert _exists(migrated_db) is False
    assert count([derived, derived_other]) == 0
    assert count([unrelated]) == 1

    _flask(migrated_db, ["schema-upgrade"])
    assert _exists(migrated_db) is True
    assert _conn_scalar(migrated_db, f"SELECT count(*) FROM {TABLE}") == 0
    assert count([derived, derived_other]) == 0
    assert _conn_scalar(migrated_db, f"SELECT count(*) FROM risks WHERE id = {risk}") == 1


def test_revision_on_a_database_that_already_has_the_table_keeps_its_rows(migrated_db):
    org = _org(migrated_db, "keep")
    risk = _risk(migrated_db, org, "Keep risk")
    _assessment(migrated_db, org, risk)
    _owner_run(migrated_db, f"UPDATE alembic_version SET version_num = '{PREVIOUS}'")

    _flask(migrated_db, ["db", "upgrade", REVISION])

    assert _conn_scalar(migrated_db, "SELECT version_num FROM alembic_version") == REVISION
    assert _conn_scalar(migrated_db, f"SELECT count(*) FROM {TABLE}") == 1


def test_one_organisation_cannot_read_or_change_anothers_assessments_as_the_runtime_role(deployed_db):
    migrated_db = deployed_db
    org_a, org_b = _org(migrated_db, "a"), _org(migrated_db, "b")
    risk_a, risk_b = _risk(migrated_db, org_a, "A risk"), _risk(migrated_db, org_b, "B risk")
    row_a, row_b = _assessment(migrated_db, org_a, risk_a), _assessment(migrated_db, org_b, risk_b)

    role = f"riskloss_rt_{uuid.uuid4().hex[:8]}"
    url = make_url(migrated_db)
    owner = create_engine(url)
    with owner.begin() as conn:
        conn.execute(text(
            f"CREATE ROLE {role} LOGIN PASSWORD 'rt-pass' NOSUPERUSER NOBYPASSRLS"  # nosec B608 - test-only role
        ))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))  # nosec B608
        conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO {role}"))  # nosec B608
    runtime = create_engine(url.set(username=role, password="rt-pass"))
    try:
        with runtime.begin() as conn:
            conn.execute(text("SELECT set_config('archie.organization_id', :o, true)"), {"o": str(org_a)})
            seen = [r[0] for r in conn.execute(text(f"SELECT id FROM {TABLE}"))]
            assert seen == [row_a]
            assert conn.execute(
                text(f"UPDATE {TABLE} SET trials = 1 WHERE id = :i"), {"i": row_b}).rowcount == 0
            assert conn.execute(text(f"DELETE FROM {TABLE} WHERE id = :i"), {"i": row_b}).rowcount == 0
        with pytest.raises(Exception):
            with runtime.begin() as conn:
                conn.execute(text("SELECT set_config('archie.organization_id', :o, true)"), {"o": str(org_a)})
                conn.execute(text(
                    f"INSERT INTO {TABLE} (organization_id, risk_id, factors, trials, seed, currency, entered_at) "
                    "VALUES (:o, :r, CAST(:f AS json), 1, 1, 'GBP', now())"),
                    {"o": org_b, "r": risk_b, "f": _FACTORS})
        with runtime.begin() as conn:
            assert conn.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar() == 0
    finally:
        runtime.dispose()
        with owner.begin() as conn:
            conn.execute(text(f"DROP OWNED BY {role}"))  # nosec B608
            conn.execute(text(f"DROP ROLE {role}"))  # nosec B608
        owner.dispose()
    assert _conn_scalar(migrated_db, f"SELECT count(*) FROM {TABLE} WHERE trials = 100") == 2
