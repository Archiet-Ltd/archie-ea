"""cost_facts is fenced by row-level security: the runtime role sees only its organisation."""
import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

from tests.test_row_level_security import RlsEnv, rls, world  # noqa: F401  (shared fixtures)

REVISION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261010_cost_facts_rls.py"


@pytest.fixture
def fenced(rls):  # noqa: F811
    spec = importlib.util.spec_from_file_location("cost_facts_rls_under_test", REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with rls.owner.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
    return rls


def _fact(owner, org_id, element_id):
    with owner.begin() as connection:
        return connection.execute(
            text(
                "INSERT INTO cost_facts (organization_id, element_type, element_id, category, kind, "
                "amount, currency, period, source, source_id) "
                "VALUES (:o, 'application', :e, 'total_cost_of_ownership', 'actual', 100, 'USD', "
                "'annual', 'test', '') RETURNING id"
            ),
            {"o": org_id, "e": element_id},
        ).scalar_one()


def test_one_organisation_cannot_read_change_or_delete_anothers_cost_facts(fenced, world):  # noqa: F811
    org_a, org_b = world.org("a"), world.org("b")
    world.extra_deletes.append(("cost_facts", "organization_id", [org_a, org_b]))
    fact_a, fact_b = _fact(fenced.owner, org_a, 1), _fact(fenced.owner, org_b, 2)

    with fenced.runtime_tx(org_a) as connection:
        seen = connection.execute(text("SELECT id FROM cost_facts WHERE id = ANY(:i)"), {"i": [fact_a, fact_b]})
        assert [r[0] for r in seen] == [fact_a]
        assert connection.execute(
            text("UPDATE cost_facts SET amount = 1 WHERE id = :i"), {"i": fact_b}).rowcount == 0
        assert connection.execute(text("DELETE FROM cost_facts WHERE id = :i"), {"i": fact_b}).rowcount == 0
        with pytest.raises(Exception):
            connection.execute(
                text(
                    "INSERT INTO cost_facts (organization_id, element_type, element_id, category, kind, "
                    "amount, currency, period, source, source_id) "
                    "VALUES (:o, 'application', 9, 'total_cost_of_ownership', 'actual', 1, 'USD', "
                    "'annual', 'test', '')"
                ),
                {"o": org_b},
            )

    with fenced.owner.connect() as connection:
        row = connection.execute(text("SELECT amount FROM cost_facts WHERE id = :i"), {"i": fact_b}).scalar()
    assert row == 100


def test_a_session_with_no_organisation_sees_no_cost_facts(fenced, world):  # noqa: F811
    org_a = world.org("a")
    world.extra_deletes.append(("cost_facts", "organization_id", [org_a]))
    _fact(fenced.owner, org_a, 1)
    with fenced.runtime_tx(None) as connection:
        assert connection.execute(text("SELECT count(*) FROM cost_facts")).scalar_one() == 0
    with fenced.runtime_tx(None, platform=True) as connection:
        assert connection.execute(text("SELECT count(*) FROM cost_facts")).scalar_one() >= 1
