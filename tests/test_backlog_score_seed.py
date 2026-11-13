"""The demonstration seed records score inputs on each backlog requirement's ranked copy, for its own organisation only."""
from app.commands.seed_demo_company import _BACKLOG_SCORE_INPUTS, _seed_backlog_scores
from app.models.models import Requirement
from app.models.solution_architect_models import SolutionRequirement
from app.services import prioritisation_service

NAME = "Record the as-found reading for every instrument"


def _backlog_requirement(db_session, org):
    source = SolutionRequirement(name=NAME, description="d", status="Draft", organization_id=org.id)
    db_session.add(source)
    db_session.flush()
    prioritisation_service.project_backlog_requirements(org.id)
    db_session.flush()
    return source, db_session.get(Requirement, source.archimate_requirement_id)


def test_seeded_inputs_belong_to_one_organisation_and_leave_a_recorded_input_alone(db_session, make_org):
    ours, theirs = make_org("seed-scores-ours"), make_org("seed-scores-theirs")
    _, ours_ranked = _backlog_requirement(db_session, ours)
    _, theirs_ranked = _backlog_requirement(db_session, theirs)

    assert _seed_backlog_scores(ours.id) == 1
    bv, tc, rr, js = _BACKLOG_SCORE_INPUTS[NAME][:4]
    assert ours_ranked.wsjf_score == round((bv + tc + rr) / js, 2)
    assert ours_ranked.rice_score is not None
    assert theirs_ranked.wsjf_score is None and theirs_ranked.business_value is None

    # a person's own figure is not replaced by a second run
    ours_ranked.business_value = 1
    ours_ranked.time_criticality = ours_ranked.risk_reduction = ours_ranked.job_size = None
    assert _seed_backlog_scores(ours.id) == 0
    assert ours_ranked.business_value == 1 and ours_ranked.time_criticality is None
