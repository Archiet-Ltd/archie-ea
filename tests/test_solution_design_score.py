"""The design score: ten plain checks, computed once from the solution record.

Each check is proven to start open and to pass on the record a person would
write, and a solution of one organisation never yields a score to another.
"""
import pytest

from app.services.solution_design_score_service import compute, design_score


@pytest.fixture
def org(make_org):
    return make_org("design-score")


@pytest.fixture
def solution(db_session, org, tenant_ctx):
    from app.models.solution_models import Solution

    with tenant_ctx(org.id):
        s = Solution(name="Order Capture Replacement", organization_id=org.id)
        db_session.add(s)
        db_session.flush()
        yield s


def _passed(solution):
    return {c["key"]: c["passed"] for c in compute(solution).checks}


def _element(db_session, solution, etype, layer, name, link=True):
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_models import SolutionArchiMateElement

    el = ArchiMateElement(name=name, type=etype, layer=layer, organization_id=solution.organization_id)
    db_session.add(el)
    db_session.flush()
    if link:
        db_session.add(SolutionArchiMateElement(
            solution_id=solution.id, layer_type=layer.lower(), element_id=el.id,
            element_table="archimate_elements", element_name=name))
        db_session.flush()
    return el


def test_a_new_solution_scores_zero_of_ten(db_session, solution):
    score = compute(solution)
    assert score.total == 10 and len(score.checks) == 10
    assert score.passed == 0 and score.label == "0 of 10"
    assert all(c["fix_href"] for c in score.checks)


def test_problem_stated(db_session, solution):
    solution.description = "Orders"
    assert not _passed(solution)["problem"]
    solution.description = "Orders are retyped by hand at the depot."
    assert _passed(solution)["problem"]


def test_owner_and_technical_lead_both_named(db_session, solution):
    solution.solution_owner = "Ivo Reed"
    assert not _passed(solution)["owner"]
    solution.technical_lead = "Head of Engineering"
    assert _passed(solution)["owner"]


def test_gap_or_goal_linked(db_session, solution):
    _element(db_session, solution, "Driver", "Motivation", "Regulation")
    assert not _passed(solution)["goal"]
    _element(db_session, solution, "Goal", "Motivation", "Faster certificates")
    assert _passed(solution)["goal"]


def test_requirements_each_realised_by_a_design_element(db_session, solution):
    from app.models.archimate_core import ArchiMateRelationship

    req = _element(db_session, solution, "Requirement", "Motivation", "Readings are immutable")
    assert not _passed(solution)["requirements"]
    app = _element(db_session, solution, "ApplicationComponent", "Application", "Ledger")
    db_session.add(ArchiMateRelationship(source_id=app.id, target_id=req.id, type="realization",
                                         organization_id=solution.organization_id))
    db_session.flush()
    assert _passed(solution)["requirements"]


def test_applications_counted_from_design_elements(db_session, solution):
    assert not _passed(solution)["applications"]
    _element(db_session, solution, "ApplicationComponent", "Application", "Ledger")
    score = compute(solution)
    assert _passed(solution)["applications"] and score.counts["applications"] == 1


def test_data_named(db_session, solution):
    assert not _passed(solution)["data"]
    _element(db_session, solution, "DataObject", "Application", "Calibration result")
    assert _passed(solution)["data"]


def test_where_it_runs_named(db_session, solution):
    assert not _passed(solution)["runtime"]
    _element(db_session, solution, "Node", "Technology", "Compute pool")
    assert _passed(solution)["runtime"]


def test_options_compared_and_decision_recorded(db_session, solution):
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.solution_lifecycle_models import SolutionOption

    for name in ("Build a ledger", "Buy a suite"):
        db_session.add(SolutionOption(solution_id=solution.id, name=name,
                                      organization_id=solution.organization_id))
    db_session.flush()
    assert not _passed(solution)["decision"]
    db_session.add(ArchitectureDecision(title="One ledger", solution_id=solution.id,
                                        organization_id=solution.organization_id))
    db_session.flush()
    assert _passed(solution)["decision"]


def test_risks_from_the_register_each_with_an_owner(db_session, solution):
    from app.models.risk import Risk
    from app.models.risk_entity_link import RiskEntityLink

    risk = Risk(title="Testers lose readings", likelihood=3, impact=4,
                organization_id=solution.organization_id)
    db_session.add(risk)
    db_session.flush()
    db_session.add(RiskEntityLink(risk_id=risk.id, entity_type="solution", entity_id=solution.id,
                                  organization_id=solution.organization_id))
    db_session.flush()
    assert not _passed(solution)["risks"], "a risk with no owner must not pass"
    risk.owner = "Head of Engineering"
    db_session.flush()
    assert _passed(solution)["risks"]
    assert compute(solution).counts["risks"] == 1


def test_one_off_and_running_cost(db_session, solution):
    from app.models.solution_cost_model import SolutionCostLineItem, SolutionCostModel

    model = SolutionCostModel(solution_id=solution.id, name="Three-year cost")
    db_session.add(model)
    db_session.flush()
    db_session.add(SolutionCostLineItem(cost_model_id=model.id, category="software", cost_type="capex",
                                        name="Licences", unit_cost=1000, organization_id=solution.organization_id))
    db_session.flush()
    assert not _passed(solution)["cost"]
    db_session.add(SolutionCostLineItem(cost_model_id=model.id, category="services", cost_type="opex",
                                        name="Support", unit_cost=100, frequency="annual",
                                        organization_id=solution.organization_id))
    db_session.flush()
    assert _passed(solution)["cost"]


def test_the_old_unwritten_link_table_is_not_read(db_session, solution):
    """Elements linked the way every writer links them count (D1)."""
    _element(db_session, solution, "ApplicationComponent", "Application", "Ledger")
    _element(db_session, solution, "DataObject", "Application", "Reading")
    assert compute(solution).counts["elements"] == 2


def test_another_organisation_never_gets_the_score(db_session, solution, make_org, tenant_ctx):
    with tenant_ctx(solution.organization_id):
        assert design_score(solution.id) is not None
    other = make_org("design-score-other")
    with tenant_ctx(other.id):
        assert design_score(solution.id) is None
