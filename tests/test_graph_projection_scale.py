"""Scale budget for the graph projection: three hops in a 100,000-element model.

The measured scope is the whole call a caller makes, ``graph_projection.query``:
the lookup of the organisation's cached projection, the catch-up read of the
event-log tail (the one SQL statement the call may issue), and the traversal.
The build of the projection is measured separately and is not part of the
per-call budget (it happens once per process, then from the stored snapshot).

Rows are inserted with set-based SQL inside the test's rolled-back transaction,
the way a bulk import writes them, so no event is queued for them; the
projection is built from the relational store.

The model is seeded ONCE (one fixture use, one test) and with the per-row history
write skipped for the insert only, via the transaction-local
``archie.skip_entity_history`` setting the ``entity_history_record_version()``
trigger itself honours (no trigger is disabled; this one still fires, and chooses
not to write). With the write on, every one of the 405,000 rows is copied into
``entity_history`` as a JSON snapshot, which doubled the disk the CI shard needed
and ran it out of space. The seed lives and dies inside the test's rolled-back
transaction, so nothing is left behind. The history table is not needed here: the
budget is measured on current-state traversal and on the snapshot round trip.
"""

from __future__ import annotations

import statistics
import time
import uuid

import pytest
from sqlalchemy import event, text

from app import db
from app.models.organization import Organization
from app.modules.intelligence.services import graph_projection as gp

ELEMENTS = 100_000
BUDGET_P95_SECONDS = 0.5
ROOTS = 300


def _seed_big_model(db_session):
    suffix = uuid.uuid4().hex[:10]
    org = Organization(name=f"Scale {suffix}", slug=f"scale-{suffix}")
    other = Organization(name=f"Scale other {suffix}", slug=f"scale-o-{suffix}")
    db_session.add_all([org, other])
    db_session.flush()
    # No history rows for the seed (see the module note); restored below.
    db_session.execute(text("SELECT set_config('archie.skip_entity_history', 'on', true)"))
    db_session.execute(
        text(
            "INSERT INTO archimate_elements (name, type, layer, organization_id) "
            "SELECT 'e' || g, CASE WHEN g % 3 = 0 THEN 'ApplicationComponent' ELSE 'Node' END, "
            "CASE WHEN g % 3 = 0 THEN 'application' ELSE 'technology' END, :o "
            "FROM generate_series(1, :n) AS g"
        ),
        {"o": org.id, "n": ELEMENTS},
    )
    ids = [r[0] for r in db_session.execute(
        text("SELECT id FROM archimate_elements WHERE organization_id = :o ORDER BY id"), {"o": org.id})]
    assert len(ids) == ELEMENTS
    first = ids[0]
    # About three relationships per element, plus one hub with 5,000 direct dependants.
    db_session.execute(
        text(
            "INSERT INTO archimate_relationships (type, source_id, target_id, organization_id) "
            "SELECT 'Serving', :base + k, :base + ((k * 7 + 1) % :n), :o FROM generate_series(0, :m) AS k "
            "UNION ALL SELECT 'Flow', :base + k, :base + ((k * 13 + 5) % :n), :o FROM generate_series(0, :m) AS k "
            "UNION ALL SELECT 'Realization', :base + k, :base + (k + 1) % :n, :o FROM generate_series(0, :m) AS k "
            "UNION ALL SELECT 'Serving', :base, :base + k, :o FROM generate_series(1, 5000) AS k"
        ),
        {"o": org.id, "base": first, "n": ELEMENTS, "m": ELEMENTS - 1},
    )
    # A second organisation with its own small graph, to be sure it stays out.
    db_session.execute(
        text(
            "INSERT INTO archimate_elements (name, type, layer, organization_id) "
            "SELECT 'o' || g, 'Node', 'technology', :o FROM generate_series(1, 50) AS g"
        ),
        {"o": other.id},
    )
    db_session.execute(text("SELECT set_config('archie.skip_entity_history', 'off', true)"))
    db_session.commit()
    return {"org": org, "other": other, "ids": ids}


def _p95(samples):
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


def test_three_hops_in_a_100k_element_model_answer_under_half_a_second_at_p95(db_session):
    big_model = _seed_big_model(db_session)
    org, ids = big_model["org"], big_model["ids"]
    gp.clear_cache()

    started = time.perf_counter()
    projection = gp.get_projection(org.id)
    build_seconds = time.perf_counter() - started
    assert projection.node_count == ELEMENTS
    assert projection.edge_count >= 3 * ELEMENTS
    # The other organisation's 50 elements are not in this projection (the count above is exact).

    # Roots spread through the model, the hub first (the heaviest case).
    step = max(1, ELEMENTS // ROOTS)
    roots = [ids[0]] + ids[1::step][:ROOTS]

    statements = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _count)
    samples, reached = [], []
    try:
        for root in roots:
            statements.clear()
            started = time.perf_counter()
            result = gp.query(org.id, root, 3, "downstream", kinds=("explicit",))
            samples.append(time.perf_counter() - started)
            reached.append(len(result.rows))
            # The per-call work is the catch-up read of the log tail and nothing else.
            assert len(statements) <= 1, statements
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    p95 = _p95(samples)
    print(
        f"graph projection scale: {ELEMENTS} elements, {projection.edge_count} edges, "
        f"build {build_seconds:.2f}s, {len(roots)} roots, median {statistics.median(samples) * 1000:.1f} ms, "
        f"p95 {p95 * 1000:.1f} ms, max {max(samples) * 1000:.1f} ms, reach median {statistics.median(reached)}"
    )
    assert max(reached) >= 5000, "the hub root must make the heaviest case heavy"
    assert statistics.median(reached) > 10, "the roots must have something to traverse"
    assert p95 < BUDGET_P95_SECONDS

    # The same seeded model: a snapshot of it restores identically.
    _check_snapshot_round_trip(db_session, org)


def _check_snapshot_round_trip(db_session, org):
    started = time.perf_counter()
    built = gp.build_projection(org.id)
    rebuild_seconds = time.perf_counter() - started
    gp.save_snapshot(built)
    db_session.commit()

    started = time.perf_counter()
    loaded = gp.load_snapshot(org.id)
    load_seconds = time.perf_counter() - started
    print(f"snapshot: rebuild {rebuild_seconds:.2f}s, load {load_seconds:.2f}s")
    assert loaded is not None and loaded.same_content_as(built)
    assert loaded.node_count == ELEMENTS
