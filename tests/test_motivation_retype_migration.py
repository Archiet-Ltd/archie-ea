"""The motivation retype migration gives softened relationships back, once, inside their organisation."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

PATH = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "20261115_motivation_retype.py"


def _load():
    spec = importlib.util.spec_from_file_location("motivation_retype_migration", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(db_session, step):
    module = _load()
    ctx = MigrationContext.configure(db_session.connection())
    with Operations.context(ctx):
        getattr(module, step)()
    db_session.expire_all()


def _element(db_session, org, kind, name):
    el = ArchiMateElement(name=name, type=kind, organization_id=org.id)
    db_session.add(el)
    db_session.flush()
    return el


def _softened(db_session, org, source, target, note="As modelled: Realization."):
    rel = ArchiMateRelationship(
        type="association",
        source_id=source.id,
        target_id=target.id,
        description=f"Agreed in workshop. {note}",
        organization_id=org.id,
    )
    db_session.add(rel)
    db_session.flush()
    return rel


@pytest.fixture
def seeded(db_session, make_org):
    orgs = [make_org("retype"), make_org("retype")]
    rows = {}
    for org in orgs:
        req = _element(db_session, org, "Requirement", f"Req {org.id}")
        goal = _element(db_session, org, "Goal", f"Goal {org.id}")
        app = _element(db_session, org, "ApplicationComponent", f"App {org.id}")
        rows[org.id] = {
            "motivation": _softened(db_session, org, req, goal),
            "application": _softened(db_session, org, app, goal),
            "influence": _softened(db_session, org, goal, req, "As modelled: Influence."),
            "other_note": _softened(db_session, org, req, goal, "As modelled: Serving."),
        }
    return orgs, rows


def test_softened_motivation_relationships_become_what_was_modelled_once(db_session, seeded):
    orgs, rows = seeded
    _run(db_session, "upgrade")
    _run(db_session, "upgrade")
    for org in orgs:
        mine = rows[org.id]
        assert mine["motivation"].type == "realization"
        assert mine["motivation"].organization_id == org.id
        assert "As modelled: Realization." in mine["motivation"].description
        assert mine["influence"].type == "influence"
        assert mine["application"].type == "association"
        assert mine["other_note"].type == "association"
    count = ArchiMateRelationship.query.filter(
        ArchiMateRelationship.type == "realization",
        ArchiMateRelationship.id.in_([rows[o.id]["motivation"].id for o in orgs]),
    ).count()
    assert count == 2


def test_a_row_whose_organisation_differs_from_its_elements_is_left_alone(db_session, seeded):
    orgs, rows = seeded
    stray = rows[orgs[0].id]["motivation"]
    db_session.execute(
        text("UPDATE archimate_relationships SET organization_id = :o WHERE id = :i"),
        {"o": orgs[1].id, "i": stray.id},
    )
    db_session.expire_all()
    _run(db_session, "upgrade")
    assert stray.type == "association"
    assert rows[orgs[1].id]["motivation"].type == "realization"


def test_downgrade_restores_the_association(db_session, seeded):
    orgs, rows = seeded
    _run(db_session, "upgrade")
    _run(db_session, "downgrade")
    for org in orgs:
        assert rows[org.id]["motivation"].type == "association"
        assert rows[org.id]["influence"].type == "association"


def _realization(db_session, org, source, target):
    rel = ArchiMateRelationship(
        type="realization",
        source_id=source.id,
        target_id=target.id,
        description="Agreed in workshop.",
        organization_id=org.id,
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def test_realization_stored_backwards_becomes_an_association_with_its_note_once(db_session, make_org):
    orgs = [make_org("backwards"), make_org("backwards")]
    rows = {}
    for org in orgs:
        goal = _element(db_session, org, "Goal", f"Goal {org.id}")
        outcome = _element(db_session, org, "Outcome", f"Outcome {org.id}")
        principle = _element(db_session, org, "Principle", f"Principle {org.id}")
        req = _element(db_session, org, "Requirement", f"Req {org.id}")
        rows[org.id] = {
            "goal_outcome": _realization(db_session, org, goal, outcome),
            "principle_req": _realization(db_session, org, principle, req),
            "forwards": _realization(db_session, org, outcome, goal),
        }
    _run(db_session, "upgrade")
    _run(db_session, "upgrade")
    for org in orgs:
        mine = rows[org.id]
        for key in ("goal_outcome", "principle_req"):
            assert mine[key].type == "association"
            assert mine[key].description == "Agreed in workshop. As modelled: Realization."
            assert mine[key].organization_id == org.id
        assert mine["forwards"].type == "realization"
