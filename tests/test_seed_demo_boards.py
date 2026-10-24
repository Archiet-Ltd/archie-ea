"""The demonstration organisation carries SAFe kinds, ADM-board solutions, a
value stream and a requirements backlog, and no other organisation does."""

from __future__ import annotations

import os

import pytest

from app.commands.seed_demo_company import seed_demo_company


@pytest.fixture
def seeded(app, db_session, make_org):
    """Seed the demonstration organisation, plus a second real organisation."""
    os.environ["DEMO_USER_PASSWORD"] = "test-password"
    try:
        stats = seed_demo_company()
        second = seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    from app.models.organization import Organization

    demo_id = db_session.query(Organization.id).filter_by(slug="lantern-quay").scalar()
    other = make_org("boards-other")
    db_session.commit()
    return {"demo": demo_id, "other": other.id, "first": stats, "second": second}


def _kinds(org_id):
    from app.models.archimate_core import ArchiMateElement
    from app.services.element_profile_service import get_kind_property, read_profile

    rows = []
    for el in db_session_query(ArchiMateElement, org_id):
        profile = read_profile(el)
        if profile.get("kind"):
            rows.append((el, profile["kind"], get_kind_property(el, "kanban_state")))
    return rows


def db_session_query(model, org_id):
    from app import db

    return db.session.query(model).filter(
        model.organization_id == org_id, model.type == "WorkPackage").all()


def test_demo_organisation_has_epics_in_four_columns_and_other_kinds(seeded):
    rows = _kinds(seeded["demo"])
    by_kind = {}
    for _el, kind, _state in rows:
        by_kind[kind] = by_kind.get(kind, 0) + 1
    assert by_kind == {"Epic": 12, "Feature": 3, "Enabler": 1}

    columns = {}
    for _el, kind, state in rows:
        if kind == "Epic":
            columns[state] = columns.get(state, 0) + 1
    assert columns == {
        "Funnel": 3, "Reviewing": 3, "Analyzing": 3, "Portfolio Backlog": 3}


def test_epics_carry_wsjf_factors_that_differ(seeded):
    from app.services.element_profile_service import read_profile

    scores = set()
    for el, kind, _state in _kinds(seeded["demo"]):
        profile = read_profile(el)
        for part in ("business_value", "time_criticality", "risk_reduction", "job_size"):
            assert profile["properties"][part]["value"] >= 1
        scores.add(profile["wsjf_score"])
    assert len(scores) > 5


def test_other_organisation_has_no_kinds_solutions_streams_or_backlog(seeded):
    from app.models.solution_architect_models import SolutionRequirement
    from app.models.solution_models import Solution
    from app.models.unified_capability import ValueStream

    other = seeded["other"]
    assert _kinds(other) == []
    for model in (Solution, ValueStream, SolutionRequirement):
        assert model.query.filter_by(organization_id=other).count() == 0


def test_demo_organisation_has_three_solutions_each_with_deliverables(seeded):
    from app import db
    from app.models.solution_models import Solution, solution_deliverables

    sols = Solution.query.filter_by(organization_id=seeded["demo"]).all()
    assert len(sols) == 3
    assert len({s.adm_phase for s in sols}) == 3
    for sol in sols:
        n = db.session.execute(
            solution_deliverables.select().where(
                solution_deliverables.c.solution_id == sol.id)
        ).fetchall()
        assert len(n) >= 2, sol.name


def test_value_stream_has_four_stages_each_mapped_to_a_capability(seeded):
    from app import db
    from app.models.relationship_tables import value_stream_stage_capabilities
    from app.models.unified_capability import ValueStream, ValueStreamStage

    streams = ValueStream.query.filter_by(organization_id=seeded["demo"]).all()
    assert len(streams) == 1
    stages = (ValueStreamStage.query.filter_by(value_stream_id=streams[0].id)
              .order_by(ValueStreamStage.stage_order).all())
    assert len(stages) == 4
    for stage in stages:
        linked = db.session.execute(
            value_stream_stage_capabilities.select().where(
                value_stream_stage_capabilities.c.value_stream_stage_id == stage.id)
        ).fetchall()
        assert linked, stage.name


def test_requirements_backlog_has_eight_in_mixed_states(seeded):
    from app.models.solution_architect_models import SolutionRequirement

    rows = SolutionRequirement.query.filter_by(organization_id=seeded["demo"]).all()
    assert len(rows) == 8
    assert {r.status for r in rows} == {"Draft", "Refined", "Approved", "Implemented"}


def test_second_run_creates_nothing(seeded):
    second = seeded["second"]
    for key in ("safe_kinds_set", "solutions_created", "solution_deliverables_created",
                "value_streams_created", "value_stream_stages_created",
                "stage_capabilities_linked", "backlog_requirements_created"):
        assert second[key] == 0, key
    assert seeded["first"]["safe_kinds_set"] == 16
