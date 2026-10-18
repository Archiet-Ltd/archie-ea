"""graph_projection_snapshots is fenced by row-level security: the runtime role sees only its organisation.

Every assertion runs as the non-owner, non-superuser runtime role the row-level
security module creates, with two organisations whose snapshots both exist.
"""
import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

from tests.test_row_level_security import RlsEnv, rls, world  # noqa: F401  (shared fixtures)

REVISION = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261013_graph_projection.py"

_INSERT = (
    "INSERT INTO graph_projection_snapshots (organization_id, format_version, last_ordinal, node_count, "
    "edge_count, fingerprint, payload, built_at) "
    "VALUES (:o, 1, 7, 3, 2, :f, :p, now())"
)


@pytest.fixture
def fenced(rls):  # noqa: F811
    spec = importlib.util.spec_from_file_location("graph_projection_rls_under_test", REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with rls.owner.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
    return rls


def _snapshot(owner, org_id, marker):
    with owner.begin() as connection:
        return connection.execute(
            text(_INSERT + " RETURNING id"), {"o": org_id, "f": marker * 64, "p": b"payload-" + marker.encode()},
        ).scalar_one()


def test_one_organisation_cannot_read_change_or_delete_anothers_snapshot(fenced, world):  # noqa: F811
    org_a, org_b = world.org("a"), world.org("b")
    world.extra_deletes.append(("graph_projection_snapshots", "organization_id", [org_a, org_b]))
    snap_a, snap_b = _snapshot(fenced.owner, org_a, "a"), _snapshot(fenced.owner, org_b, "b")

    with fenced.runtime_tx(org_a) as connection:
        seen = connection.execute(
            text("SELECT id FROM graph_projection_snapshots WHERE id = ANY(:i)"), {"i": [snap_a, snap_b]})
        assert [r[0] for r in seen] == [snap_a]
        assert connection.execute(
            text("UPDATE graph_projection_snapshots SET node_count = 99 WHERE id = :i"), {"i": snap_b}).rowcount == 0
        assert connection.execute(
            text("DELETE FROM graph_projection_snapshots WHERE id = :i"), {"i": snap_b}).rowcount == 0
        with pytest.raises(Exception):
            connection.execute(text(_INSERT), {"o": org_b, "f": "c" * 64, "p": b"x"})

    with fenced.owner.connect() as connection:
        survivors = connection.execute(
            text("SELECT node_count, payload FROM graph_projection_snapshots WHERE id = :i"), {"i": snap_b}
        ).one()
    assert survivors.node_count == 3
    assert bytes(survivors.payload) == b"payload-b"


def test_a_session_with_no_organisation_sees_no_snapshot(fenced, world):  # noqa: F811
    org_a = world.org("a")
    world.extra_deletes.append(("graph_projection_snapshots", "organization_id", [org_a]))
    _snapshot(fenced.owner, org_a, "a")
    with fenced.runtime_tx(None) as connection:
        assert connection.execute(text("SELECT count(*) FROM graph_projection_snapshots")).scalar_one() == 0
        with pytest.raises(Exception):
            connection.execute(text(_INSERT), {"o": org_a, "f": "d" * 64, "p": b"x"})
    with fenced.runtime_tx(None, platform=True) as connection:
        assert connection.execute(text("SELECT count(*) FROM graph_projection_snapshots")).scalar_one() >= 1


def test_the_application_reads_its_own_snapshot_under_the_runtime_role(app, fenced, world):  # noqa: F811
    """The service itself (not only raw SQL) is fenced when the connection is the runtime role's."""
    from sqlalchemy.orm import Session

    org_a, org_b = world.org("a"), world.org("b")
    world.extra_deletes.append(("graph_projection_snapshots", "organization_id", [org_a, org_b]))
    _snapshot(fenced.owner, org_a, "a")
    _snapshot(fenced.owner, org_b, "b")
    from app.models.graph_projection_snapshot import GraphProjectionSnapshot

    with fenced.runtime_tx(org_a) as connection:
        rows = Session(bind=connection).query(GraphProjectionSnapshot.organization_id).all()
    assert [r[0] for r in rows] == [org_a]
