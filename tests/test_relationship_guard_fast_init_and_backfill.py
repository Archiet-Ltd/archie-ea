"""No code path stores a relationship whose two ends belong to different
organisations: not the model used under the fast-init flag, and not the
backfill command, which writes with plain SQL."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_FAST_INIT_SCRIPT = textwrap.dedent(
    """
    import os, uuid
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    import app.models.application_component_fast  # noqa: F401  (mapper targets)
    from app.models.archimate_core import ArchiMateRelationship

    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    with engine.connect() as conn:
        outer = conn.begin()
        try:
            def org(label):
                tag = uuid.uuid4().hex[:8]
                return conn.execute(text(
                    "INSERT INTO organizations (name, slug) VALUES (:n, :s) RETURNING id"
                ), {"n": f"FI {label} {tag}", "s": f"fi-{label}-{tag}"}).scalar_one()

            def element(org_id, label):
                return conn.execute(text(
                    "INSERT INTO archimate_elements (name, type, layer, organization_id) "
                    "VALUES (:n, 'ApplicationComponent', 'application', :o) RETURNING id"
                ), {"n": f"{label} {uuid.uuid4().hex[:6]}", "o": org_id}).scalar_one()

            mine, theirs = org("mine"), org("theirs")
            a, b = element(mine, "a"), element(mine, "b")
            far = element(theirs, "far")

            session = Session(bind=conn, join_transaction_mode="create_savepoint")
            session.add(ArchiMateRelationship(
                source_id=a, target_id=b, type="flow", organization_id=mine))
            session.flush()  # same organisation: stored

            session.add(ArchiMateRelationship(
                source_id=a, target_id=far, type="flow", organization_id=mine))
            try:
                session.flush()
            except ValueError as exc:
                print("REFUSED" if type(exc).__name__ == "CrossOrganisationRelationship"
                      else "OTHER " + repr(exc))
            else:
                print("STORED")
        finally:
            outer.rollback()
    """
)


def test_fast_init_relationship_model_refuses_a_crossing_row():
    env = dict(os.environ, APP_FAST_INIT="1", FLASK_CONFIG="testing")
    result = subprocess.run(
        [sys.executable, "-c", _FAST_INIT_SCRIPT],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().endswith("REFUSED"), result.stdout + result.stderr[-1000:]


def _org(db_session, label):
    from app.models.organization import Organization

    tag = uuid.uuid4().hex[:8]
    org = Organization(name=f"BF {label} {tag}", slug=f"bf-{label}-{tag}")
    db_session.add(org)
    db_session.flush()
    return org


def _element(db_session, org, kind):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(
        name=f"{kind} {uuid.uuid4().hex[:6]}",
        type=kind,
        layer="application",
        organization_id=org.id,
    )
    db_session.add(el)
    db_session.flush()
    return el


def _plain_edge(db_session, org, a, b, kind):
    from sqlalchemy import text

    db_session.execute(
        text(
            "INSERT INTO archimate_relationships "
            "(organization_id, source_id, target_id, type, created_at, updated_at) "
            "VALUES (:o, :s, :t, :k, now(), now())"
        ),
        {"o": org.id, "s": a.id, "t": b.id, "k": kind},
    )


def _edges_from(db_session, source):
    from sqlalchemy import text

    return db_session.execute(
        text("SELECT target_id, type FROM archimate_relationships WHERE source_id = :s"),
        {"s": source.id},
    ).all()


def test_backfill_skips_and_reports_a_crossing_candidate(app, db_session):
    mine, theirs = _org(db_session, "mine"), _org(db_session, "theirs")
    # Inside one organisation: app serves process, process realizes service.
    a, b, c = (_element(db_session, mine, k) for k in
               ("ApplicationComponent", "BusinessProcess", "BusinessService"))
    _plain_edge(db_session, mine, a, b, "serving")
    _plain_edge(db_session, mine, b, c, "realization")
    # The same shape whose far end belongs to the other organisation, as
    # rows written before the guard existed.
    a2, b2 = (_element(db_session, mine, k) for k in ("ApplicationComponent", "BusinessProcess"))
    foreign = _element(db_session, theirs, "BusinessService")
    _plain_edge(db_session, mine, a2, b2, "serving")
    _plain_edge(db_session, mine, b2, foreign, "realization")

    from app.commands.backfill_archimate_relationships import (
        backfill_archimate_relationships_command,
    )

    result = app.test_cli_runner().invoke(
        backfill_archimate_relationships_command, ["--transitive"]
    )
    assert result.exit_code == 0, result.output

    assert (c.id, "serving") in {tuple(r) for r in _edges_from(db_session, a)}, (
        "the same-organisation inference was not created"
    )
    assert foreign.id not in {r[0] for r in _edges_from(db_session, a2)}, (
        "a relationship to another organisation's element was stored"
    )
    skipped = [
        line for line in result.output.splitlines()
        if line.startswith("Skipped ") and "different organisations" in line
    ]
    assert skipped and int(skipped[0].split()[1]) >= 1, result.output


def test_backfill_solution_pass_skips_a_solution_holding_another_organisations_element(
    app, db_session
):
    from sqlalchemy import text

    mine, theirs = _org(db_session, "mine"), _org(db_session, "theirs")
    solution_id = db_session.execute(
        text(
            "INSERT INTO solutions (name, organization_id, created_at, updated_at) "
            "VALUES (:n, :o, now(), now()) RETURNING id"
        ),
        {"n": f"BF solution {uuid.uuid4().hex[:6]}", "o": mine.id},
    ).scalar_one()
    own_app = _element(db_session, mine, "ApplicationComponent")
    own_process = _element(db_session, mine, "BusinessProcess")
    foreign_process = _element(db_session, theirs, "BusinessProcess")
    for el in (own_app, own_process, foreign_process):
        db_session.execute(
            text(
                "INSERT INTO solution_archimate_elements "
                "(solution_id, element_id, element_role) "
                "VALUES (:s, :e, 'primary')"
            ),
            {"s": solution_id, "e": el.id},
        )

    from app.commands.backfill_archimate_relationships import (
        backfill_archimate_relationships_command,
    )

    result = app.test_cli_runner().invoke(
        backfill_archimate_relationships_command, ["--solution-id", str(solution_id)]
    )
    assert result.exit_code == 0, result.output

    stored = {r[0] for r in _edges_from(db_session, own_app)}
    assert own_process.id in stored, "the same-organisation relationship was not created"
    assert foreign_process.id not in stored, (
        "a relationship to another organisation's element was stored"
    )
    skipped = [
        line for line in result.output.splitlines()
        if line.startswith("Skipped ") and "different organisations" in line
    ]
    assert skipped and int(skipped[0].split()[1]) >= 1, result.output
