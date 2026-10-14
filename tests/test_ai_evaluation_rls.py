"""ai_evaluation_plan and ai_evaluation_run are fenced by row-level security.

Every assertion runs as the test-only runtime role (not the table owner, not a
superuser, not BYPASSRLS), with two organisations: a session sees only its own
organisation's plans and runs, cannot change or delete the other's, cannot
write a row for the other, and with no organisation set sees nothing.
"""
import importlib.util
import json
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

from tests.test_row_level_security import RlsEnv, rls, world  # noqa: F401  (shared fixtures)

REVISION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261013_ai_evaluation_rls.py"


@pytest.fixture
def fenced(rls):  # noqa: F811
    spec = importlib.util.spec_from_file_location("ai_evaluation_rls_under_test", REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with rls.owner.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
    return rls


def _element(owner, org_id):
    with owner.begin() as connection:
        return connection.execute(
            text(
                "INSERT INTO archimate_elements (name, type, layer, organization_id) "
                "VALUES ('RLS AI system', 'ApplicationComponent', 'application', :o) RETURNING id"
            ),
            {"o": org_id},
        ).scalar_one()


def _plan(owner, org_id, element_id):
    with owner.begin() as connection:
        return connection.execute(
            text(
                "INSERT INTO ai_evaluation_plan (organization_id, ai_system_id, version, "
                "test_set_name, test_set_size, thresholds, created_at) "
                "VALUES (:o, :e, 1, 'RLS set', 10, :t, now()) RETURNING id"
            ),
            {"o": org_id, "e": element_id, "t": json.dumps({"groundedness": 0.8})},
        ).scalar_one()


def _run(owner, org_id, element_id, plan_id):
    with owner.begin() as connection:
        return connection.execute(
            text(
                "INSERT INTO ai_evaluation_run (organization_id, ai_system_id, plan_id, plan_version, "
                "metrics, cases_run, created_at) "
                "VALUES (:o, :e, :p, 1, :m, 10, now()) RETURNING id"
            ),
            {"o": org_id, "e": element_id, "p": plan_id, "m": json.dumps({"groundedness": 0.9})},
        ).scalar_one()


def _two_organisations(fenced, world):  # noqa: F811
    org_a, org_b = world.org("a"), world.org("b")
    el_a, el_b = _element(fenced.owner, org_a), _element(fenced.owner, org_b)
    plan_a, plan_b = _plan(fenced.owner, org_a, el_a), _plan(fenced.owner, org_b, el_b)
    run_a, run_b = _run(fenced.owner, org_a, el_a, plan_a), _run(fenced.owner, org_b, el_b, plan_b)
    # Children are deleted first; the organisations are removed by the world fixture.
    world.extra_deletes.extend([
        ("ai_evaluation_run", "id", [run_a, run_b]),
        ("ai_evaluation_plan", "id", [plan_a, plan_b]),
        ("archimate_elements", "id", [el_a, el_b]),
    ])
    return org_a, org_b, el_a, el_b, plan_a, plan_b, run_a, run_b


def test_the_runtime_role_is_not_the_owner_a_superuser_or_bypassrls(fenced):  # noqa: F811
    with fenced.runtime.connect() as connection:
        is_super, bypass = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        owner = connection.execute(
            text("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'public.ai_evaluation_plan'::regclass")
        ).scalar_one()
        enabled = connection.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE oid = 'public.ai_evaluation_run'::regclass")
        ).scalar_one()
    assert is_super is False and bypass is False
    assert owner != "archie_rls_test_runtime"
    assert enabled is True


def test_one_organisation_cannot_read_change_or_delete_anothers_plans_and_runs(fenced, world):  # noqa: F811
    org_a, org_b, el_a, el_b, plan_a, plan_b, run_a, run_b = _two_organisations(fenced, world)

    with fenced.runtime_tx(org_a) as connection:
        plans = connection.execute(
            text("SELECT id FROM ai_evaluation_plan WHERE id = ANY(:i)"), {"i": [plan_a, plan_b]})
        assert [r[0] for r in plans] == [plan_a]
        runs = connection.execute(
            text("SELECT id FROM ai_evaluation_run WHERE id = ANY(:i)"), {"i": [run_a, run_b]})
        assert [r[0] for r in runs] == [run_a]
        # B's rows exist (checked below as the owner) but A cannot touch them.
        assert connection.execute(
            text("UPDATE ai_evaluation_plan SET test_set_name = 'hijacked' WHERE id = :i"),
            {"i": plan_b}).rowcount == 0
        assert connection.execute(
            text("UPDATE ai_evaluation_run SET cases_run = 1 WHERE id = :i"), {"i": run_b}).rowcount == 0
        assert connection.execute(
            text("DELETE FROM ai_evaluation_run WHERE id = :i"), {"i": run_b}).rowcount == 0
        assert connection.execute(
            text("DELETE FROM ai_evaluation_plan WHERE id = :i"), {"i": plan_b}).rowcount == 0

    with fenced.owner.connect() as connection:
        assert connection.execute(
            text("SELECT test_set_name FROM ai_evaluation_plan WHERE id = :i"), {"i": plan_b}
        ).scalar() == "RLS set"
        assert connection.execute(
            text("SELECT cases_run FROM ai_evaluation_run WHERE id = :i"), {"i": run_b}).scalar() == 10


def test_one_organisation_cannot_write_a_plan_or_run_into_another(fenced, world):  # noqa: F811
    org_a, org_b, el_a, el_b, plan_a, plan_b, run_a, run_b = _two_organisations(fenced, world)
    with fenced.runtime_tx(org_a) as connection:
        with pytest.raises(Exception):
            connection.execute(
                text(
                    "INSERT INTO ai_evaluation_plan (organization_id, ai_system_id, version, "
                    "test_set_name, test_set_size, thresholds, created_at) "
                    "VALUES (:o, :e, 1, 'planted', 5, '{}', now())"
                ),
                {"o": org_b, "e": el_a},
            )
    with fenced.runtime_tx(org_a) as connection:
        with pytest.raises(Exception):
            connection.execute(
                text(
                    "INSERT INTO ai_evaluation_run (organization_id, ai_system_id, plan_id, "
                    "plan_version, metrics, cases_run, created_at) "
                    "VALUES (:o, :e, :p, 1, '{}', 5, now())"
                ),
                {"o": org_b, "e": el_a, "p": plan_a},
            )
    with fenced.owner.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM ai_evaluation_plan WHERE test_set_name = 'planted'")).scalar() == 0


def test_a_session_with_no_organisation_sees_no_plans_or_runs(fenced, world):  # noqa: F811
    _two_organisations(fenced, world)
    with fenced.runtime_tx(None) as connection:
        assert connection.execute(text("SELECT count(*) FROM ai_evaluation_plan")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM ai_evaluation_run")).scalar_one() == 0
    with fenced.runtime_tx(None, platform=True) as connection:
        assert connection.execute(text("SELECT count(*) FROM ai_evaluation_plan")).scalar_one() >= 2
        assert connection.execute(text("SELECT count(*) FROM ai_evaluation_run")).scalar_one() >= 2
