"""Tests for the forecast, self-ratings and live-initiative parts of
``strategy_for_element`` (L2).

Each initiative carries its forecast cost and expected return as recorded,
beside the budget and the spend, and the three 1-100 self-ratings as ratings
with their scale, never combined or ranked. The live initiative record the
Portfolio screens use has no element link, so it is not read and the answer
says so on every branch. The three figures are redacted at the route for a
caller without budget authority; the ratings are not financial and stay.

Fixtures (app, db_session, make_org, client, login_as) come from
app/modules/intelligence/tests/conftest.py.
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from sqlalchemy import event

LIVE = {"rows": None, "reason": "initiative_not_element_linked", "source": "enterprise_initiatives"}
RATING_KEYS = {"business_value_score", "risk_score", "strategic_alignment_score", "scale", "truth_class"}
BASE_KEYS = {
    "initiative_id", "name", "status", "priority", "health_status", "completion_percentage",
    "start_date", "target_end_date", "executive_sponsor", "program_manager", "budget_variance_pct",
    "budget_reason", "success_metrics", "affected_rows", "affected_summary",
}
NEW_KEYS = {"forecast_cost", "expected_roi_percentage", "forecast_reason", "self_ratings"}


def _element(db_session, org_id, name, layer="business"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type="Goal", layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type="Serving", organization_id=org_id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _initiative(db_session, element, **kwargs):
    from app.models.enterprise_intelligence import PortfolioInitiative

    fields = {"name": "Cloud migration programme", "status": "Active", "total_budget": 300000,
              "spent_to_date": 100000}
    fields.update(kwargs)
    row = PortfolioInitiative(archimate_element_id=element.id, **fields)
    db_session.add(row)
    db_session.flush()
    return row


def _make_user(db_session, org, *, enterprise_role=None):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"forecast-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=role,
        is_org_admin=True,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _answer(app, org_id, element_id):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_id
        return IntelligenceQueryService.strategy_for_element(element_id)


class _Counter:
    def __init__(self):
        self.statements = []

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def touching(self, table):
        pattern = re.compile(rf"\bFROM {table}\b")
        return sum(1 for s in self.statements if pattern.search(s))


@pytest.fixture
def counter(app):
    from app import db

    c = _Counter()
    event.listen(db.engine, "before_cursor_execute", c)
    try:
        yield c
    finally:
        event.remove(db.engine, "before_cursor_execute", c)


def _world(db_session, make_org, slug):
    org = make_org(slug)
    goal = _element(db_session, org.id, "Goal A")
    other = _element(db_session, org.id, "Other")
    _relationship(db_session, org.id, goal, other)
    row = _initiative(
        db_session, goal, forecast_cost=250000, expected_roi_percentage=12.5,
        business_value_score=65, risk_score=75, strategic_alignment_score=80,
    )
    db_session.commit()
    return org, goal, row


# (1) recorded figures and ratings, side by side with the budget
def test_forecast_roi_and_ratings_are_carried_as_recorded(app, db_session, make_org):
    org, goal, row = _world(db_session, make_org, "sf-1")

    payload = _answer(app, org.id, goal.id)["initiatives"][0]

    assert payload["forecast_cost"] == 250000.0
    assert payload["expected_roi_percentage"] == 12.5
    assert payload["forecast_reason"] is None
    assert payload["self_ratings"] == {
        "business_value_score": 65, "risk_score": 75, "strategic_alignment_score": 80,
        "scale": "1-100", "truth_class": "authoritative_fact",
    }
    # Beside, not combined: the budget figures are unchanged and nothing is derived.
    assert round(payload["budget_variance_pct"], 2) == round((100000 - 300000) / 300000 * 100, 2)


# (2) tenancy
def test_initiative_seeded_on_another_organisations_element_is_absent(app, db_session, make_org):
    org, goal, row = _world(db_session, make_org, "sf-2a")
    org_b = make_org("sf-2b")
    b_goal = _element(db_session, org_b.id, "B goal")
    foreign = _initiative(db_session, b_goal, name="B initiative", forecast_cost=1, expected_roi_percentage=1)
    db_session.commit()

    ids = {i["initiative_id"] for i in _answer(app, org.id, goal.id)["initiatives"]}

    assert ids == {row.id}
    assert foreign.id not in ids


def test_element_of_another_organisation_answers_not_found_with_the_live_block(app, db_session, make_org):
    org, goal, row = _world(db_session, make_org, "sf-2c")
    org_b = make_org("sf-2d")
    db_session.commit()

    result = _answer(app, org_b.id, goal.id)

    assert result["initiatives"] == []
    assert result["reasons"] == ["element_not_found"]
    assert result["live_initiatives"] == LIVE


# (3) (4) (5) not recorded
def test_no_forecast_says_no_budget_recorded(app, db_session, make_org):
    org = make_org("sf-3")
    goal = _element(db_session, org.id, "Goal A")
    _initiative(db_session, goal, forecast_cost=None)
    db_session.commit()

    payload = _answer(app, org.id, goal.id)["initiatives"][0]

    assert payload["forecast_cost"] is None
    assert payload["forecast_reason"] == "no_budget_recorded"


def test_unrecorded_ratings_are_null_and_the_scale_stays(app, db_session, make_org):
    org = make_org("sf-4")
    goal = _element(db_session, org.id, "Goal A")
    _initiative(db_session, goal)
    db_session.commit()

    ratings = _answer(app, org.id, goal.id)["initiatives"][0]["self_ratings"]

    assert ratings["business_value_score"] is None
    assert ratings["risk_score"] is None
    assert ratings["strategic_alignment_score"] is None
    assert ratings["scale"] == "1-100"


def test_a_recorded_zero_return_stays_zero(app, db_session, make_org):
    org = make_org("sf-5")
    goal = _element(db_session, org.id, "Goal A")
    _initiative(db_session, goal, expected_roi_percentage=0, forecast_cost=0)
    db_session.commit()

    payload = _answer(app, org.id, goal.id)["initiatives"][0]

    assert payload["expected_roi_percentage"] == 0.0
    assert payload["forecast_cost"] == 0.0
    assert payload["forecast_reason"] is None


# (6) the live record is never read
def test_live_block_is_constant_on_every_branch_and_the_record_is_never_read(
    app, db_session, make_org, counter
):
    from app.models.vendor.vendor_organization import EnterpriseInitiative
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org, goal, row = _world(db_session, make_org, "sf-6")
    empty = _element(db_session, org.id, "Empty")
    db_session.add(EnterpriseInitiative(organization_id=org.id, name=row.name))
    db_session.commit()

    counter.statements.clear()
    computed = _answer(app, org.id, goal.id)
    no_initiative = _answer(app, org.id, empty.id)
    not_found = _answer(app, org.id, 999999999)
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None
        no_context = IntelligenceQueryService.strategy_for_element(goal.id)

    for answer in (computed, no_initiative, not_found, no_context):
        assert answer["live_initiatives"] == LIVE
    assert counter.touching("enterprise_initiatives") == 0


# (7) (8) shape and batching
def test_payload_and_self_rating_shapes(app, db_session, make_org):
    org, goal, row = _world(db_session, make_org, "sf-7")

    payload = _answer(app, org.id, goal.id)["initiatives"][0]

    assert set(payload.keys()) == BASE_KEYS | NEW_KEYS
    assert set(payload["self_ratings"].keys()) == RATING_KEYS


def _own_selects(app, db_session, make_org, counter, slug, initiatives):
    org = make_org(slug)
    goal = _element(db_session, org.id, "Goal A")
    for index in range(initiatives):
        _initiative(db_session, goal, name=f"Initiative {index}", forecast_cost=index + 1)
    db_session.commit()

    counter.statements.clear()
    result = _answer(app, org.id, goal.id)
    assert len(result["initiatives"]) == initiatives
    return counter.touching("portfolio_initiatives")


def test_no_extra_select_for_one_initiative_or_for_five(app, db_session, make_org, counter):
    assert _own_selects(app, db_session, make_org, counter, "sf-8a", 1) == _own_selects(
        app, db_session, make_org, counter, "sf-8b", 5
    ) == 1


# (9) nothing invented, combined or ranked
def test_no_invented_figure_rank_or_average(app, db_session, make_org):
    org = make_org("sf-9")
    goal = _element(db_session, org.id, "Goal A")
    _initiative(db_session, goal, forecast_cost=None, expected_roi_percentage=None)
    db_session.commit()

    result = _answer(app, org.id, goal.id)
    text = json.dumps(result)

    assert '"forecast_cost": 0' not in text
    for word in ('"rank"', '"average"', '"overall"'):
        assert word not in text
    payload = result["initiatives"][0]
    assert payload["expected_roi_percentage"] is None
    assert not any(key.startswith("forecast_variance") for key in payload)


def test_live_block_has_no_value_beside_its_reason(app, db_session, make_org):
    from app.modules.intelligence.services.reason_codes import REASON_CODES

    org, goal, row = _world(db_session, make_org, "sf-9b")

    block = _answer(app, org.id, goal.id)["live_initiatives"]

    assert block["reason"] in REASON_CODES
    assert block["rows"] is None


# (10) route and redaction
def test_route_redacts_the_three_figures_for_a_restricted_role_only(
    app, db_session, make_org, client, login_as
):
    org, goal, row = _world(db_session, make_org, "sf-10")
    restricted = _make_user(db_session, org, enterprise_role="solution_architect")
    budget_holder = _make_user(db_session, org, enterprise_role="cto")
    db_session.commit()

    login_as(client, restricted)
    resp = client.get(f"/api/v1/intelligence/strategy/{goal.id}")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    payload = data["initiatives"][0]
    assert payload["budget_variance_pct"] is None
    assert payload["forecast_cost"] is None
    assert payload["expected_roi_percentage"] is None
    assert payload["budget_reason"] == "financial_data_restricted"
    assert payload["self_ratings"]["business_value_score"] == 65
    assert data["live_initiatives"] == LIVE

    login_as(client, budget_holder)
    payload = client.get(f"/api/v1/intelligence/strategy/{goal.id}").get_json()["data"]["initiatives"][0]
    assert payload["forecast_cost"] == 250000.0
    assert payload["expected_roi_percentage"] == 12.5
    assert payload["budget_variance_pct"] is not None
    assert payload["budget_reason"] is None
