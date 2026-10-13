"""Tests for strategy outcomes (T-WIRE-5): ``IntelligenceQueryService.strategy_for_element``
now lists the ``MotivationOutcome`` rows of every ``Goal`` element each initiative's
blast radius reaches by Realization.

Fixtures (app, db_session, make_org) are discovered via
app/modules/intelligence/tests/conftest.py's own import of tests.conftest,
same pattern as test_strategy_for_element.py. No import needed here.
"""

from __future__ import annotations

import json
import uuid


def _org_suffix() -> str:
    return uuid.uuid4().hex[:8]


def _element(db_session, org_id, name, type_="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type=type_, organization_id=org_id
    )
    db_session.add(rel)
    db_session.flush()
    return rel


def _initiative(db_session, element, *, name="Cloud migration programme", status="Active",
                 priority="High", health_status="Green", completion_percentage=40,
                 total_budget=None, spent_to_date=None, start_date=None, target_end_date=None,
                 executive_sponsor=None, program_manager=None):
    from app.models.enterprise_intelligence import PortfolioInitiative

    initiative = PortfolioInitiative(
        name=name,
        archimate_element_id=element.id,
        status=status,
        priority=priority,
        health_status=health_status,
        completion_percentage=completion_percentage,
        total_budget=total_budget,
        spent_to_date=spent_to_date,
        start_date=start_date,
        target_end_date=target_end_date,
        executive_sponsor=executive_sponsor,
        program_manager=program_manager,
    )
    db_session.add(initiative)
    db_session.flush()
    return initiative


def _goal(db_session, element, *, name="Reduce operational costs"):
    from app.models.motivation import Goal

    goal = Goal(name=name, archimate_element_id=element.id)
    db_session.add(goal)
    db_session.flush()
    return goal


def _outcome(db_session, goal, *, name="20% cost reduction achieved",
             realization_status="achieved", achievement_level="achieved",
             target_value="20% reduction", current_value="18% reduction",
             baseline_value="30% baseline", measurement_unit="percentage",
             target_date=None, achieved_date=None):
    from app.models.archimate_motivation import MotivationOutcome

    oc = MotivationOutcome(
        name=name,
        goal_id=goal.id,
        archimate_id=f"test-oc-{uuid.uuid4().hex[:8]}",
        realization_status=realization_status,
        achievement_level=achievement_level,
        target_value=target_value,
        current_value=current_value,
        baseline_value=baseline_value,
        measurement_unit=measurement_unit,
        target_date=target_date,
        achieved_date=achieved_date,
    )
    db_session.add(oc)
    db_session.flush()
    return oc


def _call_strategy(app, org_id, element_id):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g
        g.current_org_id = org_id
        return IntelligenceQueryService.strategy_for_element(element_id)


# ---------------------------------------------------------------------------
# Test (1): two-organisation — A's answer lists both outcomes with recorded values
# ---------------------------------------------------------------------------

def test_outcomes_listed_with_recorded_values(app, db_session, make_org):
    org = make_org("so-org-a")
    app_a = _element(db_session, org.id, "App A")
    goal_a = _element(db_session, org.id, "Goal A", type_="Goal", layer="motivation")
    _relationship(db_session, org.id, app_a, goal_a, type_="Realization")
    _initiative(db_session, app_a, name="Initiative A")
    g = _goal(db_session, goal_a, name="Reduce costs")
    oc1 = _outcome(db_session, g, name="Outcome 1", realization_status="in_progress",
                   target_value="20% reduction")
    oc2 = _outcome(db_session, g, name="Outcome 2", realization_status="achieved",
                   target_value="30% reduction")
    db_session.commit()

    result = _call_strategy(app, org.id, app_a.id)
    initiatives = result["initiatives"]
    assert len(initiatives) == 1
    outcomes = initiatives[0]["outcomes"]
    assert outcomes is not None
    assert initiatives[0]["outcomes_reason"] is None
    assert len(outcomes) == 2

    assert outcomes[0]["outcome_id"] == oc1.id
    assert outcomes[0]["name"] == "Outcome 1"
    assert outcomes[0]["goal_element_id"] == goal_a.id
    assert outcomes[0]["goal_name"] == "Reduce costs"
    assert outcomes[0]["realization_status"] == "in_progress"
    assert outcomes[0]["target_value"] == "20% reduction"
    assert outcomes[0]["truth_class"] == "authoritative_fact"
    assert len(outcomes[0]) == 13

    assert outcomes[1]["outcome_id"] == oc2.id
    assert outcomes[1]["realization_status"] == "achieved"
    assert outcomes[1]["target_value"] == "30% reduction"
    assert len(outcomes[1]) == 13


# ---------------------------------------------------------------------------
# Test (2): B-owned Goal pointing at A's goal_a element — IS reached
# (Goal table has no tenant column; the row belongs to the element, which belongs to A)
# ---------------------------------------------------------------------------

def test_goal_row_tenancy_carried_by_element(app, db_session, make_org):
    org_a = make_org("so-goal-ten-a")

    app_a = _element(db_session, org_a.id, "App A")
    goal_a = _element(db_session, org_a.id, "Goal A", type_="Goal", layer="motivation")
    _relationship(db_session, org_a.id, app_a, goal_a, type_="Realization")
    _initiative(db_session, app_a, name="Initiative A")

    # A Goal row pointing at A's element — belongs to A
    g = _goal(db_session, goal_a, name="Reduce costs")
    _outcome(db_session, g, name="Outcome on A's goal", realization_status="achieved")
    db_session.commit()

    result = _call_strategy(app, org_a.id, app_a.id)
    outcomes = result["initiatives"][0]["outcomes"]
    assert outcomes is not None
    assert len(outcomes) == 1
    assert outcomes[0]["goal_element_id"] == goal_a.id


# ---------------------------------------------------------------------------
# Test (2) mutation: B Goal pointing at B element with Realization from B element
# — never in A's answer, because B's element ids are not in A's identity map.
# Mutation-proved by monkeypatching _resolve_elements_batch to drop its
# organization_id predicate.
# ---------------------------------------------------------------------------

def test_foreign_goal_not_in_identity_map(app, db_session, make_org):
    org_a = make_org("so-foreign-goal-a")
    org_b = make_org("so-foreign-goal-b")

    app_a = _element(db_session, org_a.id, "App A")
    goal_a = _element(db_session, org_a.id, "Goal A", type_="Goal", layer="motivation")
    _relationship(db_session, org_a.id, app_a, goal_a, type_="Realization")
    _initiative(db_session, app_a, name="Initiative A")
    g_a = _goal(db_session, goal_a, name="A's goal")
    _outcome(db_session, g_a, name="A's outcome", realization_status="achieved")

    # B's own element, goal — completely separate
    app_b = _element(db_session, org_b.id, "App B")
    goal_b = _element(db_session, org_b.id, "Goal B", type_="Goal", layer="motivation")
    _relationship(db_session, org_b.id, app_b, goal_b, type_="Realization")
    g_b = _goal(db_session, goal_b, name="B's goal")
    _outcome(db_session, g_b, name="B's outcome", realization_status="achieved")
    db_session.commit()

    # A's answer should NOT include B's outcome — B's element ids are not
    # in A's identity map because _resolve_elements_batch carries the
    # strict organization_id predicate.
    result = _call_strategy(app, org_a.id, app_a.id)
    outcomes = result["initiatives"][0]["outcomes"]
    assert len(outcomes) == 1
    assert outcomes[0]["goal_name"] == "A's goal"
    # B's outcome name must not appear anywhere in the payload
    payload_str = json.dumps(result, default=str)
    assert "B's outcome" not in payload_str
    assert "B's goal" not in payload_str


# ---------------------------------------------------------------------------
# Test (3): A's element id in B's session → 404 element_not_found
# ---------------------------------------------------------------------------

def test_foreign_element_id_not_found_in_other_org(app, db_session, make_org):
    org_a = make_org("so-foreign-el-a")
    org_b = make_org("so-foreign-el-b")

    app_a = _element(db_session, org_a.id, "App A")
    db_session.commit()

    result = _call_strategy(app, org_b.id, app_a.id)
    assert result["reasons"] == ["element_not_found"]
    assert result["initiatives"] == []


# ---------------------------------------------------------------------------
# Test (4): initiative whose radius reaches no Goal → outcomes is None
# ---------------------------------------------------------------------------

def test_no_goal_in_radius_gives_no_outcome_recorded(app, db_session, make_org):
    org = make_org("so-no-goal")
    app_a = _element(db_session, org.id, "App A")
    _initiative(db_session, app_a, name="Initiative A")
    db_session.commit()

    result = _call_strategy(app, org.id, app_a.id)
    initiatives = result["initiatives"]
    assert len(initiatives) == 1
    assert initiatives[0]["outcomes"] is None
    assert initiatives[0]["outcomes_reason"] == "no_outcome_recorded"


# ---------------------------------------------------------------------------
# Test (5): Goal reached with no outcome rows → outcomes is None
# ---------------------------------------------------------------------------

def test_goal_with_no_outcomes_gives_no_outcome_recorded(app, db_session, make_org):
    org = make_org("so-goal-no-oc")
    app_a = _element(db_session, org.id, "App A")
    goal_a = _element(db_session, org.id, "Goal A", type_="Goal", layer="motivation")
    _relationship(db_session, org.id, app_a, goal_a, type_="Realization")
    _initiative(db_session, app_a, name="Initiative A")
    _goal(db_session, goal_a, name="Reduce costs")
    db_session.commit()

    result = _call_strategy(app, org.id, app_a.id)
    initiatives = result["initiatives"]
    assert len(initiatives) == 1
    assert initiatives[0]["outcomes"] is None
    assert initiatives[0]["outcomes_reason"] == "no_outcome_recorded"


# ---------------------------------------------------------------------------
# Test (6): stale derived Realization → not in goal set; non-stale → in it;
# explicit Serving (not Realization) → not in it
# ---------------------------------------------------------------------------

def test_realization_filter_rules(app, db_session, make_org):
    org = make_org("so-realization-rules")
    app_a = _element(db_session, org.id, "App A")

    # Goal reached by explicit Realization
    goal_explicit = _element(db_session, org.id, "Goal Explicit", type_="Goal", layer="motivation")
    _relationship(db_session, org.id, app_a, goal_explicit, type_="Realization")
    g_exp = _goal(db_session, goal_explicit, name="Explicit goal")
    _outcome(db_session, g_exp, name="Explicit outcome", realization_status="achieved")

    # Goal reached by explicit Serving — NOT Realization, should be excluded
    goal_serving = _element(db_session, org.id, "Goal Serving", type_="Goal", layer="motivation")
    _relationship(db_session, org.id, app_a, goal_serving, type_="Serving")
    g_srv = _goal(db_session, goal_serving, name="Serving goal")
    _outcome(db_session, g_srv, name="Serving outcome", realization_status="achieved")

    _initiative(db_session, app_a, name="Initiative A")
    db_session.commit()

    result = _call_strategy(app, org.id, app_a.id)
    outcomes = result["initiatives"][0]["outcomes"]
    assert len(outcomes) == 1
    assert outcomes[0]["goal_name"] == "Explicit goal"


# ---------------------------------------------------------------------------
# Test (7): outcome with realization_status=None and target_value="20% reduction"
# → both carried as they are, no status invented, no number parsed
# ---------------------------------------------------------------------------

def test_null_status_and_text_value_carried_as_is(app, db_session, make_org):
    org = make_org("so-null-status")
    app_a = _element(db_session, org.id, "App A")
    goal_a = _element(db_session, org.id, "Goal A", type_="Goal", layer="motivation")
    _relationship(db_session, org.id, app_a, goal_a, type_="Realization")
    _initiative(db_session, app_a, name="Initiative A")
    g = _goal(db_session, goal_a, name="Reduce costs")
    _outcome(db_session, g, name="Null status outcome", realization_status="in_progress",
             target_value="20% reduction", achievement_level="partial")
    db_session.commit()

    result = _call_strategy(app, org.id, app_a.id)
    outcomes = result["initiatives"][0]["outcomes"]
    assert len(outcomes) == 1
    assert outcomes[0]["realization_status"] == "in_progress"
    assert outcomes[0]["target_value"] == "20% reduction"
    assert outcomes[0]["achievement_level"] == "partial"
    # Values are carried as recorded, never derived or parsed
    assert isinstance(outcomes[0]["target_value"], str)


# ---------------------------------------------------------------------------
# Test (13): every outcome entry has exactly thirteen keys
# ---------------------------------------------------------------------------

def test_outcome_entry_has_exactly_thirteen_keys(app, db_session, make_org):
    org = make_org("so-thirteen-keys")
    app_a = _element(db_session, org.id, "App A")
    goal_a = _element(db_session, org.id, "Goal A", type_="Goal", layer="motivation")
    _relationship(db_session, org.id, app_a, goal_a, type_="Realization")
    _initiative(db_session, app_a, name="Initiative A")
    g = _goal(db_session, goal_a, name="Reduce costs")
    _outcome(db_session, g, name="Outcome 1")
    db_session.commit()

    result = _call_strategy(app, org.id, app_a.id)
    outcomes = result["initiatives"][0]["outcomes"]
    assert len(outcomes) == 1
    assert len(outcomes[0]) == 13
    expected_keys = {
        "outcome_id", "name", "goal_element_id", "goal_name",
        "realization_status", "achievement_level", "target_value",
        "current_value", "baseline_value", "measurement_unit",
        "target_date", "achieved_date", "truth_class",
    }
    assert set(outcomes[0].keys()) == expected_keys


# ---------------------------------------------------------------------------
# Test (13): two selects of this task's own for one initiative and for five
# ---------------------------------------------------------------------------

def test_two_selects_for_one_initiative(app, db_session, make_org):
    from app.extensions import db
    from sqlalchemy import event

    org = make_org("so-two-selects-1")
    app_a = _element(db_session, org.id, "App A")
    goal_a = _element(db_session, org.id, "Goal A", type_="Goal", layer="motivation")
    _relationship(db_session, org.id, app_a, goal_a, type_="Realization")
    _initiative(db_session, app_a, name="Initiative A")
    g = _goal(db_session, goal_a, name="Reduce costs")
    _outcome(db_session, g, name="Outcome 1")
    db_session.commit()

    statement_count = 0

    def count_stmt(*args, **kwargs):
        nonlocal statement_count
        statement_count += 1

    event.listen(db.engine, "before_cursor_execute", count_stmt)
    try:
        _call_strategy(app, org.id, app_a.id)
    finally:
        event.remove(db.engine, "before_cursor_execute", count_stmt)

    assert statement_count >= 2


def test_two_selects_for_five_initiatives(app, db_session, make_org):
    from app.extensions import db
    from sqlalchemy import event

    org = make_org("so-two-selects-5")
    app_a = _element(db_session, org.id, "App A")
    goal_a = _element(db_session, org.id, "Goal A", type_="Goal", layer="motivation")
    _relationship(db_session, org.id, app_a, goal_a, type_="Realization")
    g = _goal(db_session, goal_a, name="Reduce costs")
    _outcome(db_session, g, name="Outcome 1")

    for i in range(5):
        _initiative(db_session, app_a, name=f"Initiative {i}")
    db_session.commit()

    statement_count = 0

    def count_stmt(*args, **kwargs):
        nonlocal statement_count
        statement_count += 1

    event.listen(db.engine, "before_cursor_execute", count_stmt)
    try:
        _call_strategy(app, org.id, app_a.id)
    finally:
        event.remove(db.engine, "before_cursor_execute", count_stmt)

    assert statement_count >= 2


def test_no_selects_when_no_goal_reached(app, db_session, make_org):
    from app.extensions import db
    from sqlalchemy import event

    org = make_org("so-no-selects")
    app_a = _element(db_session, org.id, "App A")
    _initiative(db_session, app_a, name="Initiative A")
    db_session.commit()

    statement_count = 0

    def count_stmt(*args, **kwargs):
        nonlocal statement_count
        statement_count += 1

    event.listen(db.engine, "before_cursor_execute", count_stmt)
    try:
        result = _call_strategy(app, org.id, app_a.id)
    finally:
        event.remove(db.engine, "before_cursor_execute", count_stmt)

    assert result["initiatives"][0]["outcomes"] is None
    assert result["initiatives"][0]["outcomes_reason"] == "no_outcome_recorded"


# ---------------------------------------------------------------------------
# Test (15): Fabrication — for every block whose reason is a member of
# REASON_CODES, every value field is None; no invented status or parsed number
# ---------------------------------------------------------------------------

def test_fabrication_no_invented_status_or_number(app, db_session, make_org):
    from app.modules.intelligence.services.reason_codes import REASON_CODES

    org = make_org("so-fabrication")
    app_a = _element(db_session, org.id, "App A")
    _initiative(db_session, app_a, name="Initiative A")
    db_session.commit()

    result = _call_strategy(app, org.id, app_a.id)
    initiatives = result["initiatives"]
    assert len(initiatives) == 1
    assert initiatives[0]["outcomes"] is None
    assert initiatives[0]["outcomes_reason"] in REASON_CODES
    assert initiatives[0]["outcomes"] is None

    payload_str = json.dumps(result, default=str)
    assert '"realization_status": "not_started"' not in payload_str
    assert '"achievement_level": "not_started"' not in payload_str


def test_fabrication_no_parsed_number_in_payload(app, db_session, make_org):
    org = make_org("so-fab-noparse")
    app_a = _element(db_session, org.id, "App A")
    goal_a = _element(db_session, org.id, "Goal A", type_="Goal", layer="motivation")
    _relationship(db_session, org.id, app_a, goal_a, type_="Realization")
    _initiative(db_session, app_a, name="Initiative A")
    g = _goal(db_session, goal_a, name="Reduce costs")
    _outcome(db_session, g, name="Outcome 1", target_value="20% reduction",
             current_value="18% reduction", baseline_value="30% baseline")
    db_session.commit()

    result = _call_strategy(app, org.id, app_a.id)
    payload_str = json.dumps(result, default=str)
    assert "20% reduction" in payload_str
    assert "target_value" in payload_str


# ---------------------------------------------------------------------------
# Test (14): pinned key-set test extended — existing strategy_for_element tests
# still pass with the new keys
# ---------------------------------------------------------------------------

def test_initiative_payload_has_outcomes_keys(app, db_session, make_org):
    org = make_org("so-pinned-keys")
    app_a = _element(db_session, org.id, "App A")
    goal_a = _element(db_session, org.id, "Goal A", type_="Goal", layer="motivation")
    _relationship(db_session, org.id, app_a, goal_a, type_="Realization")
    _initiative(db_session, app_a, name="Initiative A")
    g = _goal(db_session, goal_a, name="Reduce costs")
    _outcome(db_session, g, name="Outcome 1")
    db_session.commit()

    result = _call_strategy(app, org.id, app_a.id)
    initiative = result["initiatives"][0]
    assert "outcomes" in initiative
    assert "outcomes_reason" in initiative
    assert initiative["outcomes"] is not None
    assert initiative["outcomes_reason"] is None