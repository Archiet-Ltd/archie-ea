"""R1-B55: evaluation plans and the release check.

An AI system cannot be marked released without a current evaluation run that
meets the plan's thresholds; the refusal names the missing or failing metric;
one organisation's plans and runs never gate another's release.

Uses the shared fixtures in tests/conftest.py (db_session rolls back).
"""

from __future__ import annotations

import uuid

import pytest

from app.models.ai_evaluation_plan import AIEvaluationPlan
from app.models.ai_evaluation_run import AIEvaluationRun
from app.modules.ai_chat.services import ai_evaluation_service as ev
from app.modules.enterprise_genome.services.ai_system_classification import (
    get_release_state,
    mark_released,
    release_refusal_reason,
)
from app.modules.enterprise_genome.services.ai_systems_seed import register_ai_system


def _system(db_session, org, name="Eval System", model_id="claude-opus-5"):
    el = register_ai_system(
        db_session, org.id, name=f"{name} {uuid.uuid4().hex[:6]}",
        provider="anthropic", model_id=model_id,
    )
    db_session.flush()
    return el


def _plan(el, org, **thresholds):
    return ev.set_plan(
        element_id=el.id, organization_id=org.id, test_set_name="Golden set",
        test_set_size=50, thresholds=thresholds or {"groundedness": 0.8},
    )


def _run(el, org, **metrics):
    return ev.record_run(
        element_id=el.id, organization_id=org.id, metrics=metrics, cases_run=50,
    )


def _raw_count(db_session, table):
    """Row count with no tenant filter: after a request the ambient organisation
    is the caller's, and an ORM count would hide the very rows a leak creates."""
    from sqlalchemy import text

    return db_session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # nosec B608 - fixed names in this module


def _user(db_session, org, role_name, label, enterprise_role=None):
    from app.models.user import Role, User

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        pytest.skip(f"no {role_name} role seeded")
    u = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.test", first_name=label,
        organization_id=org.id, confirmed=True, role=role,
        enterprise_role=enterprise_role,
    )
    u.password = uuid.uuid4().hex
    db_session.add(u)
    db_session.flush()
    return u


# --- The release check names what is missing or failing ----------------------


def test_release_with_no_plan_is_refused_naming_the_missing_plan(db_session, make_org):
    org = make_org("ev1")
    el = _system(db_session, org)
    result = mark_released(el, org.id)
    assert result["success"] is False
    assert "no evaluation plan" in result["reason"]
    assert get_release_state(el) != "released"


def test_release_with_a_plan_but_no_run_is_refused_naming_the_missing_run(db_session, make_org):
    org = make_org("ev2")
    el = _system(db_session, org)
    _plan(el, org)
    result = mark_released(el, org.id)
    assert result["success"] is False
    assert "no evaluation run" in result["reason"]
    assert "Golden set" in result["reason"]


def test_failing_metric_is_named_with_value_and_threshold(db_session, make_org):
    org = make_org("ev3")
    el = _system(db_session, org)
    _plan(el, org, groundedness=0.8, task_success=0.7)
    _run(el, org, groundedness=0.62, task_success=0.9)
    result = mark_released(el, org.id)
    assert result["success"] is False
    assert "groundedness 0.62 is below the threshold 0.8" in result["reason"]
    assert "task_success" not in result["reason"]  # the passing metric is not blamed
    assert get_release_state(el) != "released"


def test_a_metric_the_run_did_not_measure_is_named(db_session, make_org):
    org = make_org("ev4")
    el = _system(db_session, org)
    _plan(el, org, groundedness=0.8, refusal_correctness=0.9)
    _run(el, org, groundedness=0.95)
    reason = release_refusal_reason(el, org.id)
    assert "refusal_correctness was not measured" in reason
    assert "groundedness" not in reason


def test_cost_is_a_maximum_not_a_minimum(db_session, make_org):
    org = make_org("ev5")
    el = _system(db_session, org)
    _plan(el, org, cost_per_task=0.5)
    _run(el, org, cost_per_task=0.9)
    assert "cost_per_task 0.9 is above the threshold 0.5" in release_refusal_reason(el, org.id)
    _run(el, org, cost_per_task=0.4)
    assert release_refusal_reason(el, org.id) is None


def test_every_failing_metric_is_listed(db_session, make_org):
    org = make_org("ev6")
    el = _system(db_session, org)
    _plan(el, org, groundedness=0.8, task_success=0.8)
    _run(el, org, groundedness=0.1, task_success=0.2)
    reason = release_refusal_reason(el, org.id)
    assert "groundedness 0.1" in reason and "task_success 0.2" in reason


def test_a_current_passing_run_releases_the_system(db_session, make_org):
    org = make_org("ev7")
    el = _system(db_session, org)
    _plan(el, org, groundedness=0.8)
    _run(el, org, groundedness=0.8)  # exactly at the threshold passes
    assert mark_released(el, org.id)["success"] is True
    assert get_release_state(el) == "released"


def test_only_the_latest_run_counts(db_session, make_org):
    org = make_org("ev8")
    el = _system(db_session, org)
    _plan(el, org, groundedness=0.8)
    _run(el, org, groundedness=0.95)
    _run(el, org, groundedness=0.3)  # a later, failing run supersedes the passing one
    assert "groundedness 0.3" in release_refusal_reason(el, org.id)


def test_changing_a_threshold_makes_the_earlier_run_not_current(db_session, make_org):
    org = make_org("ev9")
    el = _system(db_session, org)
    _plan(el, org, groundedness=0.8)
    _run(el, org, groundedness=0.85)
    assert release_refusal_reason(el, org.id) is None
    plan = _plan(el, org, groundedness=0.9)  # tightened
    assert plan.version == 2
    reason = release_refusal_reason(el, org.id)
    assert "plan version 1" in reason and "now version 2" in reason


def test_an_identical_plan_does_not_raise_the_version(db_session, make_org):
    org = make_org("ev10")
    el = _system(db_session, org)
    _plan(el, org, groundedness=0.8)
    _run(el, org, groundedness=0.85)
    assert _plan(el, org, groundedness=0.8).version == 1
    assert release_refusal_reason(el, org.id) is None


def test_a_model_change_makes_the_earlier_run_not_current(db_session, make_org):
    org = make_org("ev11")
    el = _system(db_session, org, model_id="claude-opus-5")
    _plan(el, org)
    _run(el, org, groundedness=0.9)
    assert release_refusal_reason(el, org.id) is None
    props = dict(el.custom_properties)
    props["ai_system"] = {**props["ai_system"], "model_id": "gpt-4o"}
    el.custom_properties = props
    db_session.flush()
    reason = release_refusal_reason(el, org.id)
    assert "measured model claude-opus-5" in reason and "now records gpt-4o" in reason


def test_a_run_takes_its_model_and_plan_version_from_the_system_not_the_caller(db_session, make_org):
    org = make_org("ev12")
    el = _system(db_session, org, model_id="claude-opus-5")
    plan = _plan(el, org)
    run = _run(el, org, groundedness=0.9)
    assert run.model_id == "claude-opus-5" and run.plan_version == plan.version
    import inspect

    parameters = inspect.signature(ev.record_run).parameters
    assert "model_id" not in parameters and "plan_version" not in parameters


# --- Validation: values that would let a bad number through -------------------


@pytest.mark.parametrize(
    "thresholds",
    [
        {},                                  # at least one threshold
        {"vibes": 0.5},                      # unknown metric
        {"groundedness": 1.5},               # ratio above 1
        {"groundedness": -0.1},              # below 0
        {"groundedness": float("nan")},      # not a number
        {"groundedness": float("inf")},
        {"groundedness": True},              # a bool is not a threshold
        {"groundedness": "high"},
        {"cost_per_task": -1},
    ],
)
def test_a_plan_with_a_bad_threshold_is_refused(db_session, make_org, thresholds):
    org = make_org("ev13")
    el = _system(db_session, org)
    with pytest.raises(ev.EvaluationError):
        ev.set_plan(element_id=el.id, organization_id=org.id, test_set_name="s",
                    test_set_size=5, thresholds=thresholds)
    assert AIEvaluationPlan.query.filter_by(organization_id=org.id).count() == 0


@pytest.mark.parametrize("name,size", [("", 5), ("   ", 5), ("s", 0), ("s", -3), ("s", True), ("s", "5")])
def test_a_plan_needs_a_test_set_name_and_a_whole_positive_size(db_session, make_org, name, size):
    org = make_org("ev14")
    el = _system(db_session, org)
    with pytest.raises(ev.EvaluationError):
        ev.set_plan(element_id=el.id, organization_id=org.id, test_set_name=name,
                    test_set_size=size, thresholds={"groundedness": 0.5})


def test_a_run_needs_a_plan_and_a_measured_metric_and_valid_numbers(db_session, make_org):
    org = make_org("ev15")
    el = _system(db_session, org)
    with pytest.raises(ev.EvaluationError, match="Set the evaluation plan"):
        _run(el, org, groundedness=0.9)
    _plan(el, org)
    for bad in ({}, {"groundedness": 1.2}, {"nonsense": 0.5}, {"groundedness": float("nan")}):
        with pytest.raises(ev.EvaluationError):
            ev.record_run(element_id=el.id, organization_id=org.id, metrics=bad, cases_run=5)
    with pytest.raises(ev.EvaluationError):
        ev.record_run(element_id=el.id, organization_id=org.id,
                      metrics={"groundedness": 0.9}, cases_run=0)
    assert AIEvaluationRun.query.filter_by(organization_id=org.id).count() == 0


# --- Two organisations -------------------------------------------------------


def test_one_organisations_thresholds_and_runs_never_gate_anothers_release(db_session, make_org):
    org_a, org_b = make_org("ev16a"), make_org("ev16b")
    el_a, el_b = _system(db_session, org_a, "A"), _system(db_session, org_b, "B")
    _plan(el_a, org_a, groundedness=0.99)       # strict
    _run(el_a, org_a, groundedness=0.5)         # failing
    _plan(el_b, org_b, groundedness=0.5)        # lax
    _run(el_b, org_b, groundedness=0.6)
    assert mark_released(el_a, org_a.id)["success"] is False
    assert mark_released(el_b, org_b.id)["success"] is True


def test_a_foreign_plan_and_run_pointing_at_this_system_id_do_not_count(db_session, make_org):
    """Foreign rows aimed at the CALLER's element id: the row exists, and the
    caller's release is still refused for want of its own plan."""
    org_a, org_b = make_org("ev17a"), make_org("ev17b")
    el_b = _system(db_session, org_b, "B")
    foreign_plan = AIEvaluationPlan(
        organization_id=org_a.id, ai_system_id=el_b.id, version=1, test_set_name="A's set",
        test_set_size=5, thresholds={"groundedness": 0.1},
    )
    db_session.add(foreign_plan)
    db_session.flush()
    foreign_run = AIEvaluationRun(
        organization_id=org_a.id, ai_system_id=el_b.id, plan_id=foreign_plan.id, plan_version=1,
        model_id="claude-opus-5", metrics={"groundedness": 1.0}, cases_run=5,
    )
    db_session.add(foreign_run)
    db_session.flush()
    assert AIEvaluationPlan.query.filter_by(id=foreign_plan.id).count() == 1  # the foreign row exists
    assert ev.get_plan(el_b.id, org_b.id) is None
    assert ev.latest_run(el_b.id, org_b.id) is None
    assert ev.summaries_for([el_b.id], org_b.id)[el_b.id] == {"plan": None, "run": None}
    result = mark_released(el_b, org_b.id)
    assert result["success"] is False and "no evaluation plan" in result["reason"]


def test_a_plan_cannot_be_set_or_a_run_recorded_for_another_organisations_system(db_session, make_org):
    org_a, org_b = make_org("ev18a"), make_org("ev18b")
    el_a = _system(db_session, org_a, "A")
    with pytest.raises(ev.EvaluationError, match="not found"):
        ev.set_plan(element_id=el_a.id, organization_id=org_b.id, test_set_name="s",
                    test_set_size=5, thresholds={"groundedness": 0.5})
    _plan(el_a, org_a)
    with pytest.raises(ev.EvaluationError, match="not found"):
        ev.record_run(element_id=el_a.id, organization_id=org_b.id,
                      metrics={"groundedness": 0.9}, cases_run=5)
    assert AIEvaluationRun.query.filter_by(ai_system_id=el_a.id).count() == 0


def test_the_release_check_refuses_an_element_of_another_organisation(db_session, make_org):
    org_a, org_b = make_org("ev19a"), make_org("ev19b")
    el_a = _system(db_session, org_a, "A")
    _plan(el_a, org_a)
    _run(el_a, org_a, groundedness=0.9)
    assert ev.evaluation_refusal_reason(el_a, org_b.id) is not None


def test_a_decoy_in_another_id_space_is_not_an_ai_system(db_session, make_org):
    """An ApplicationComponent row whose id equals the element id is a different
    thing; the plan is keyed to the element the register models."""
    from app.models import ApplicationComponent

    org = make_org("ev20")
    el = _system(db_session, org)
    decoy = ApplicationComponent(name="Decoy")
    decoy.organization_id = org.id
    decoy.id = el.id + 100000
    db_session.add(decoy)
    db_session.flush()
    with pytest.raises(ev.EvaluationError, match="not found"):
        ev.set_plan(element_id=decoy.id, organization_id=org.id, test_set_name="s",
                    test_set_size=5, thresholds={"groundedness": 0.5})


# --- Unknown organisation ------------------------------------------------------


def test_with_no_organisation_nothing_is_read_and_nothing_is_written(db_session, make_org):
    org = make_org("ev21")
    el = _system(db_session, org)
    _plan(el, org)
    _run(el, org, groundedness=0.9)
    plans_before = AIEvaluationPlan.query.count()
    runs_before = AIEvaluationRun.query.count()
    assert ev.get_plan(el.id, None) is None
    assert ev.latest_run(el.id, None) is None
    assert ev.summaries_for([el.id], None)[el.id] == {"plan": None, "run": None}
    assert ev.evaluation_refusal_reason(el, None) == ev.NO_ORGANISATION
    with pytest.raises(ev.EvaluationError):
        ev.set_plan(element_id=el.id, organization_id=None, test_set_name="s",
                    test_set_size=5, thresholds={"groundedness": 0.5})
    with pytest.raises(ev.EvaluationError):
        ev.record_run(element_id=el.id, organization_id=None,
                      metrics={"groundedness": 0.9}, cases_run=5)
    assert AIEvaluationPlan.query.count() == plans_before
    assert AIEvaluationRun.query.count() == runs_before


# --- Counts: the page resolves its extras in a fixed number of queries -------


def test_summaries_for_uses_two_queries_however_many_systems(db_session, make_org):
    from sqlalchemy import event

    org = make_org("ev22")
    els = [_system(db_session, org, f"S{i}") for i in range(6)]
    for el in els:
        _plan(el, org)
        _run(el, org, groundedness=0.9)
    statements = []

    def _count(conn, cursor, statement, *a, **k):
        if "ai_evaluation" in statement:
            statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _count)
    try:
        out = ev.summaries_for([e.id for e in els], org.id)
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert len(statements) == 2
    assert all(out[e.id]["run"] is not None and out[e.id]["plan"] is not None for e in els)


def test_the_release_check_costs_two_evaluation_reads_per_system(db_session, make_org):
    """The register computes the refusal for every system it lists, so the
    evaluation part is measured in the scope the page uses: one read for the
    plan and one for the latest run, however many runs the system has."""
    from sqlalchemy import event

    org = make_org("ev22b")
    el = _system(db_session, org)
    _plan(el, org)
    for value in (0.5, 0.6, 0.9):
        _run(el, org, groundedness=value)
    statements = []

    def _count(conn, cursor, statement, *a, **k):
        if "ai_evaluation" in statement:
            statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _count)
    try:
        assert release_refusal_reason(el, org.id) is None
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert len(statements) == 2


# --- Routes ------------------------------------------------------------------


def test_architect_walks_refusal_then_passing_run_then_administrator_releases(
    app, db_session, make_org, client, login_as
):
    """The journey: the refusal names the missing evaluation, a passing run is
    attached, the administrator releases, and the state is read back."""
    org = make_org("ev23")
    el = _system(db_session, org)
    architect = _user(db_session, org, "Architect", "arch", "enterprise_architect")
    admin = _user(db_session, org, "Administrator", "adm")
    login_as(client, architect)

    check = client.post(f"/genome/ai-systems/{el.id}/release-check",
                        headers={"Accept": "application/json"})
    assert check.status_code == 409
    assert "no evaluation plan" in check.get_json()["error"]

    r = client.post(f"/genome/ai-systems/{el.id}/evaluation/plan", data={
        "test_set_name": "Golden set", "test_set_size": "40",
        "threshold_groundedness": "0.8", "threshold_task_success": "0.7"})
    assert r.status_code == 302
    r = client.post(f"/genome/ai-systems/{el.id}/evaluation/run", data={
        "cases_run": "40", "metric_groundedness": "0.6", "metric_task_success": "0.9"})
    assert r.status_code == 302
    check = client.post(f"/genome/ai-systems/{el.id}/release-check",
                        headers={"Accept": "application/json"})
    assert check.status_code == 409
    assert "groundedness 0.6 is below the threshold 0.8" in check.get_json()["error"]

    login_as(client, admin)
    refused = client.post(f"/genome/ai-systems/{el.id}/release",
                          headers={"Accept": "application/json"})
    assert refused.status_code == 409
    assert "groundedness" in refused.get_json()["error"]
    db_session.expire_all()
    assert get_release_state(el) != "released"

    login_as(client, architect)
    client.post(f"/genome/ai-systems/{el.id}/evaluation/run", data={
        "cases_run": "40", "metric_groundedness": "0.85", "metric_task_success": "0.9"})
    check = client.post(f"/genome/ai-systems/{el.id}/release-check",
                        headers={"Accept": "application/json"})
    assert check.status_code == 200 and check.get_json()["ready"] is True

    login_as(client, admin)
    released = client.post(f"/genome/ai-systems/{el.id}/release")
    assert released.status_code == 302
    db_session.expire_all()
    assert get_release_state(el) == "released"
    page = client.get(f"/genome/ai-systems/{el.id}/evaluation").get_data(as_text=True)
    assert "Version 1" in page and "Golden set" in page and "0.85" in page


def test_evaluation_page_and_register_show_the_refusal_and_link(app, db_session, make_org, client, login_as):
    org = make_org("ev24")
    el = _system(db_session, org)
    architect = _user(db_session, org, "Architect", "arch")
    login_as(client, architect)
    body = client.get("/genome/ai-systems").get_data(as_text=True)
    assert "no evaluation plan" in body
    assert f"/genome/ai-systems/{el.id}/evaluation" in body
    page = client.get(f"/genome/ai-systems/{el.id}/evaluation").get_data(as_text=True)
    assert 'data-testid="release-refusal"' in page and "no evaluation plan" in page


def test_a_released_system_whose_run_lapsed_is_flagged_for_reevaluation(
    app, db_session, make_org, client, login_as
):
    org = make_org("ev25")
    el = _system(db_session, org)
    _plan(el, org)
    _run(el, org, groundedness=0.9)
    assert mark_released(el, org.id)["success"] is True
    _plan(el, org, groundedness=0.95)  # the plan moved on
    architect = _user(db_session, org, "Architect", "arch")
    login_as(client, architect)
    body = client.get("/genome/ai-systems").get_data(as_text=True)
    assert "Re-evaluation needed" in body and "Release refused" not in body


def test_absent_form_fields_keep_the_stored_plan_and_a_blank_clears_one_threshold(
    app, db_session, make_org, client, login_as
):
    org = make_org("ev26")
    el = _system(db_session, org)
    _plan(el, org, groundedness=0.8, task_success=0.7)
    architect = _user(db_session, org, "Architect", "arch")
    login_as(client, architect)
    # Only one threshold posted: the test set and the other threshold stay.
    client.post(f"/genome/ai-systems/{el.id}/evaluation/plan",
                data={"threshold_groundedness": "0.9"})
    db_session.expire_all()
    plan = ev.get_plan(el.id, org.id)
    assert plan.test_set_name == "Golden set" and plan.test_set_size == 50
    assert plan.thresholds == {"groundedness": 0.9, "task_success": 0.7}
    # An explicit blank clears just that threshold.
    client.post(f"/genome/ai-systems/{el.id}/evaluation/plan",
                data={"threshold_task_success": ""})
    db_session.expire_all()
    assert ev.get_plan(el.id, org.id).thresholds == {"groundedness": 0.9}


def test_a_non_numeric_value_is_refused_with_a_message_and_writes_nothing(
    app, db_session, make_org, client, login_as
):
    org = make_org("ev27")
    el = _system(db_session, org)
    architect = _user(db_session, org, "Architect", "arch")
    login_as(client, architect)
    client.post(f"/genome/ai-systems/{el.id}/evaluation/plan", data={
        "test_set_name": "s", "test_set_size": "5", "threshold_groundedness": "lots"})
    assert AIEvaluationPlan.query.filter_by(organization_id=org.id).count() == 0
    with client.session_transaction() as s:
        assert any("must be a number" in m for _c, m in s.get("_flashes", []))


def test_another_organisations_user_gets_404_and_nothing_is_written(
    app, db_session, make_org, client, login_as
):
    org_a, org_b = make_org("ev28a"), make_org("ev28b")
    el_a = _system(db_session, org_a, "A")
    _plan(el_a, org_a)
    before = _raw_count(db_session, "ai_evaluation_run")
    plans_before = _raw_count(db_session, "ai_evaluation_plan")
    user_b = _user(db_session, org_b, "Architect", "archb")
    login_as(client, user_b)
    assert client.get(f"/genome/ai-systems/{el_a.id}/evaluation").status_code == 404
    for path, data in (
        ("evaluation/plan", {"test_set_name": "x", "test_set_size": "5",
                             "threshold_groundedness": "0.5"}),
        ("evaluation/run", {"cases_run": "5", "metric_groundedness": "0.9"}),
        ("release-check", {}),
    ):
        assert client.post(f"/genome/ai-systems/{el_a.id}/{path}", data=data).status_code == 404
    assert _raw_count(db_session, "ai_evaluation_run") == before
    assert _raw_count(db_session, "ai_evaluation_plan") == plans_before
    # Read with raw SQL: the logged-in organisation's tenant filter would hide
    # A's row from an ORM query here, which is the point of the 404s above.
    from sqlalchemy import text

    names = db_session.execute(
        text("SELECT test_set_name FROM ai_evaluation_plan WHERE ai_system_id = :i"),
        {"i": el_a.id},
    ).scalars().all()
    assert names == ["Golden set"]


def test_a_non_ai_element_in_the_same_organisation_is_404(app, db_session, make_org, client, login_as):
    from app.models.models import ArchiMateElement

    org = make_org("ev29")
    plain = ArchiMateElement(name="Plain app", type="ApplicationComponent",
                             layer="application", organization_id=org.id, custom_properties={})
    db_session.add(plain)
    db_session.flush()
    architect = _user(db_session, org, "Architect", "arch")
    login_as(client, architect)
    assert client.get(f"/genome/ai-systems/{plain.id}/evaluation").status_code == 404
    assert client.post(f"/genome/ai-systems/{plain.id}/evaluation/plan", data={
        "test_set_name": "x", "test_set_size": "5", "threshold_groundedness": "0.5",
    }).status_code == 404
    assert AIEvaluationPlan.query.filter_by(organization_id=org.id).count() == 0


def test_a_viewer_cannot_write_a_plan_a_run_or_a_check(app, db_session, make_org, client, login_as):
    org = make_org("ev30")
    el = _system(db_session, org)
    viewer = _user(db_session, org, "Viewer", "view")
    login_as(client, viewer)
    for path in ("evaluation/plan", "evaluation/run", "release-check"):
        assert client.post(f"/genome/ai-systems/{el.id}/{path}", data={}).status_code == 403
    assert AIEvaluationPlan.query.filter_by(organization_id=org.id).count() == 0


def test_the_routes_refuse_when_the_organisation_is_unknown(
    app, db_session, make_org, client, login_as, monkeypatch
):
    from app.modules.enterprise_genome.routes import ai_systems_routes

    org = make_org("ev31")
    el = _system(db_session, org)
    _plan(el, org)
    architect = _user(db_session, org, "Architect", "arch")
    login_as(client, architect)
    monkeypatch.setattr(ai_systems_routes, "_current_org_id", lambda: None)
    before_runs = _raw_count(db_session, "ai_evaluation_run")
    before_plans = _raw_count(db_session, "ai_evaluation_plan")
    assert client.get(f"/genome/ai-systems/{el.id}/evaluation").status_code == 404
    for path, data in (
        ("evaluation/plan", {"test_set_name": "x", "test_set_size": "5",
                             "threshold_groundedness": "0.5"}),
        ("evaluation/run", {"cases_run": "5", "metric_groundedness": "0.9"}),
        ("release-check", {}),
    ):
        assert client.post(f"/genome/ai-systems/{el.id}/{path}", data=data).status_code == 403
    assert _raw_count(db_session, "ai_evaluation_run") == before_runs
    assert _raw_count(db_session, "ai_evaluation_plan") == before_plans
    assert ev.get_plan(el.id, org.id).test_set_name == "Golden set"
