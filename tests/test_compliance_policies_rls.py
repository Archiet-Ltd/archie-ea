"""compliance_policies and compliance_violations are fenced by row-level security.

Every assertion runs as the non-owner, non-superuser, NOBYPASSRLS test runtime
role from tests/test_row_level_security.py, with two organisations.
"""
import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

from tests.test_row_level_security import RlsEnv, rls, world  # noqa: F401  (shared fixtures)

REVISION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261016_compliance_org_rls.py"


@pytest.fixture
def fenced(rls):  # noqa: F811
    spec = importlib.util.spec_from_file_location("compliance_org_rls_under_test", REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with rls.owner.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
    return rls


_POLICY = (
    "INSERT INTO compliance_policies (organization_id, name, policy_type, is_active, created_at, updated_at) "
    "VALUES (:o, :n, 'NIST', true, now(), now()) RETURNING id"
)
_VIOLATION = (
    "INSERT INTO compliance_violations (organization_id, policy_id, severity, description, status, "
    "created_at, updated_at) VALUES (:o, :p, 'High', 'rls probe', 'Open', now(), now()) RETURNING id"
)


def _seed(owner, org_id, name):
    with owner.begin() as connection:
        policy = connection.execute(text(_POLICY), {"o": org_id, "n": name}).scalar_one()
        violation = connection.execute(text(_VIOLATION), {"o": org_id, "p": policy}).scalar_one()
    return policy, violation


def test_one_organisation_cannot_read_change_or_delete_anothers_policies_and_violations(fenced, world):  # noqa: F811
    org_a, org_b = world.org("a"), world.org("b")
    policy_a, violation_a = _seed(fenced.owner, org_a, "rls policy a")
    policy_b, violation_b = _seed(fenced.owner, org_b, "rls policy b")
    world.extra_deletes.append(("compliance_violations", "organization_id", [org_a, org_b]))
    world.extra_deletes.append(("compliance_policies", "organization_id", [org_a, org_b]))

    with fenced.runtime_tx(org_a) as connection:
        policies = connection.execute(
            text("SELECT id FROM compliance_policies WHERE id = ANY(:i)"), {"i": [policy_a, policy_b]}
        )
        assert [r[0] for r in policies] == [policy_a]
        violations = connection.execute(
            text("SELECT id FROM compliance_violations WHERE id = ANY(:i)"), {"i": [violation_a, violation_b]}
        )
        assert [r[0] for r in violations] == [violation_a]
        assert connection.execute(
            text("UPDATE compliance_policies SET description = 'x' WHERE id = :i"), {"i": policy_b}
        ).rowcount == 0
        assert connection.execute(
            text("UPDATE compliance_violations SET status = 'Resolved' WHERE id = :i"), {"i": violation_b}
        ).rowcount == 0
        assert connection.execute(
            text("DELETE FROM compliance_violations WHERE id = :i"), {"i": violation_b}
        ).rowcount == 0
        assert connection.execute(
            text("DELETE FROM compliance_policies WHERE id = :i"), {"i": policy_b}
        ).rowcount == 0
        with pytest.raises(Exception):
            connection.execute(text(_POLICY), {"o": org_b, "n": "planted"})

    with fenced.owner.connect() as connection:
        status = connection.execute(
            text("SELECT status FROM compliance_violations WHERE id = :i"), {"i": violation_b}
        ).scalar()
    assert status == "Open", "the other organisation's row is untouched"


def test_with_no_organisation_the_tables_read_empty(fenced, world):  # noqa: F811
    org_a = world.org("a")
    _seed(fenced.owner, org_a, "rls policy none")
    world.extra_deletes.append(("compliance_violations", "organization_id", [org_a]))
    world.extra_deletes.append(("compliance_policies", "organization_id", [org_a]))
    with fenced.runtime_tx(None) as connection:
        assert connection.execute(text("SELECT count(*) FROM compliance_policies")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM compliance_violations")).scalar_one() == 0
