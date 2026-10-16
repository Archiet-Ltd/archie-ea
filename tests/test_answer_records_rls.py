"""answer_records is fenced by row-level security: the runtime role sees only its
organisation's stored answers (R1-B30).

Every assertion runs as the non-owner, non-superuser, NOBYPASSRLS test runtime
role from tests/test_row_level_security.py, with two organisations.
"""
import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

from tests.test_row_level_security import RlsEnv, rls, world  # noqa: F401  (shared fixtures)

REVISION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261013_answer_records_rls.py"


@pytest.fixture
def fenced(rls):  # noqa: F811
    spec = importlib.util.spec_from_file_location("answer_records_rls_under_test", REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with rls.owner.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
    return rls


_INSERT = (
    "INSERT INTO answer_records (organization_id, address, question_kind, params, confidence_basis, rows, "
    "summary, reasons, elements, citations, freshness, truncated, computed_at, created_at) "
    "VALUES (:o, :a, 'set_impact', '{}', 'no_rows', '[]', '{}', '[]', '{}', '[]', '{}', false, now(), now()) "
    "RETURNING id"
)


def _answer(owner, org_id):
    with owner.begin() as connection:
        return connection.execute(text(_INSERT), {"o": org_id, "a": uuid.uuid4().hex}).scalar_one()


def test_one_organisation_cannot_read_change_or_delete_anothers_answers(fenced, world):  # noqa: F811
    org_a, org_b = world.org("a"), world.org("b")
    world.extra_deletes.append(("answer_records", "organization_id", [org_a, org_b]))
    answer_a, answer_b = _answer(fenced.owner, org_a), _answer(fenced.owner, org_b)

    with fenced.runtime_tx(org_a) as connection:
        seen = connection.execute(
            text("SELECT id FROM answer_records WHERE id = ANY(:i)"), {"i": [answer_a, answer_b]}
        )
        assert [r[0] for r in seen] == [answer_a]
        assert connection.execute(
            text("UPDATE answer_records SET question_kind = 'impact' WHERE id = :i"), {"i": answer_b}
        ).rowcount == 0
        assert connection.execute(text("DELETE FROM answer_records WHERE id = :i"), {"i": answer_b}).rowcount == 0
        with pytest.raises(Exception):
            connection.execute(text(_INSERT), {"o": org_b, "a": uuid.uuid4().hex})

    with fenced.owner.connect() as connection:
        kind = connection.execute(
            text("SELECT question_kind FROM answer_records WHERE id = :i"), {"i": answer_b}
        ).scalar()
    assert kind == "set_impact", "the other organisation's row is untouched"


def test_an_organisation_can_write_and_read_its_own_answers(fenced, world):  # noqa: F811
    org_a = world.org("a")
    world.extra_deletes.append(("answer_records", "organization_id", [org_a]))

    with fenced.runtime_tx(org_a, commit=True) as connection:
        mine = connection.execute(text(_INSERT), {"o": org_a, "a": uuid.uuid4().hex}).scalar_one()

    with fenced.runtime_tx(org_a) as connection:
        assert connection.execute(
            text("SELECT count(*) FROM answer_records WHERE id = :i"), {"i": mine}
        ).scalar_one() == 1


def test_a_session_with_no_organisation_sees_no_answers(fenced, world):  # noqa: F811
    org_a = world.org("a")
    world.extra_deletes.append(("answer_records", "organization_id", [org_a]))
    _answer(fenced.owner, org_a)
    with fenced.runtime_tx(None) as connection:
        assert connection.execute(text("SELECT count(*) FROM answer_records")).scalar_one() == 0
    with fenced.runtime_tx(None, platform=True) as connection:
        assert connection.execute(text("SELECT count(*) FROM answer_records")).scalar_one() >= 1
