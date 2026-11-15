"""Tests for chatfacts01: explain_element works on motivation elements and returns descriptions and properties."""

import uuid

import pytest

from app import db
from app.models.archimate_core import ArchiMateElement
from app.modules.ai_chat.tools.executor import ToolExecutor
from app.modules.ai_chat.tools.resolver import EntityResolver


def _architect_user(org_id):
    from app.models.user import User, Role
    user = User(
        email=f"architect-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test", last_name="Architect",
        organization_id=org_id, confirmed=True, enterprise_role="enterprise_architect",
    )
    user.role = Role.query.filter_by(name="Architect").one()
    user.password = "testpass"
    db.session.add(user)
    db.session.flush()
    return user


def test_explain_element_returns_description_and_properties(db_session, make_org, tenant_ctx):
    """explain_element returns description, custom_properties, and provenance."""
    org = make_org("explain")
    user = _architect_user(org.id)
    db.session.commit()

    with tenant_ctx(org.id):
        # Create motivation elements directly in ArchiMateElement table
        goal = ArchiMateElement(
            name="Reduce TCO 20%",
            type="Goal",
            layer="motivation",
            description="Reduce total cost of ownership by 20% in 12 months",
            organization_id=org.id,
        )
        constraint = ArchiMateElement(
            name="Budget Cap",
            type="Constraint",
            layer="motivation",
            description="Hard budget limit for the solution",
            custom_properties={"value": "500000", "unit": "USD", "severity": "5"},
            organization_id=org.id,
        )
        db.session.add_all([goal, constraint])
        db.session.commit()

        executor = ToolExecutor(user_id=user.id)

        # Test explain_element on Goal
        goal_result = executor._tool_explain_element({"element_name": "Reduce TCO 20%"})
        assert goal_result["success"] is True, f"Goal explain failed: {goal_result}"
        result = goal_result["result"]
        assert result["element_type"] == "Goal"
        assert result["element_layer"] == "motivation"
        assert "Reduce total cost of ownership" in result["description"]
        assert "provenance" in result
        assert "steps" in result["provenance"]

        # Test explain_element on Constraint
        constraint_result = executor._tool_explain_element({"element_name": "Budget Cap"})
        assert constraint_result["success"] is True, f"Constraint explain failed: {constraint_result}"
        result = constraint_result["result"]
        assert result["element_type"] == "Constraint"
        assert result["element_layer"] == "motivation"
        assert "Hard budget limit" in result["description"]
        assert result["custom_properties"].get("value") == "500000"
        assert result["custom_properties"].get("unit") == "USD"


def test_explain_element_on_value_element(db_session, make_org, tenant_ctx):
    """explain_element works on Value elements stored directly in ArchiMateElement."""
    org = make_org("value")
    user = _architect_user(org.id)
    db.session.commit()

    with tenant_ctx(org.id):
        # Create a Value element directly in ArchiMateElement with custom_properties
        value = ArchiMateElement(
            name="Entelim Startup Plan",
            type="Value",
            layer="motivation",
            description="$49 a month or $490 a year per organisation",
            custom_properties={"price": "49 USD per month, 490 USD per year, ten people"},
            organization_id=org.id,
        )
        db.session.add(value)
        db.session.commit()

        executor = ToolExecutor(user_id=user.id)

        result = executor._tool_explain_element({"element_name": "Entelim Startup Plan"})
        assert result["success"] is True, f"Value explain failed: {result}"
        data = result["result"]
        assert data["element_type"] == "Value"
        assert data["element_layer"] == "motivation"
        assert "$49 a month" in data["description"]
        assert data["custom_properties"].get("price") == "49 USD per month, 490 USD per year, ten people"


def test_explain_element_on_stakeholder_element(db_session, make_org, tenant_ctx):
    """explain_element works on Stakeholder elements stored directly in ArchiMateElement."""
    org = make_org("stakeholder")
    user = _architect_user(org.id)
    db.session.commit()

    with tenant_ctx(org.id):
        stakeholder = ArchiMateElement(
            name="CTO",
            type="Stakeholder",
            layer="motivation",
            description="Chief Technology Officer, key decision maker",
            custom_properties={"influence": "high", "interest": "high"},
            organization_id=org.id,
        )
        db.session.add(stakeholder)
        db.session.commit()

        executor = ToolExecutor(user_id=user.id)

        result = executor._tool_explain_element({"element_name": "CTO"})
        assert result["success"] is True, f"Stakeholder explain failed: {result}"
        data = result["result"]
        assert data["element_type"] == "Stakeholder"
        assert data["element_layer"] == "motivation"
        assert "Chief Technology Officer" in data["description"]
        assert data["custom_properties"].get("influence") == "high"


def test_explain_element_not_found_returns_clarification(db_session, make_org, tenant_ctx):
    """explain_element returns clarification when element not found."""
    org = make_org("notfound")
    user = _architect_user(org.id)
    db.session.commit()

    with tenant_ctx(org.id):
        executor = ToolExecutor(user_id=user.id)

        result = executor._tool_explain_element({"element_name": "NonExistent Element"})
        assert result["success"] is False
        assert result["needs_clarification"] is True
        assert "ArchiMate element" in result["entity"]
        assert "No ArchiMate element found" in result["error"]


def test_chat_answer_quotes_value_price(db_session, make_org, tenant_ctx, monkeypatch):
    """Chat answer to 'What does our startup plan cost?' quotes the Value's price property."""
    # This test requires the full chat stack with LLM stub
    # For now, verify the tool returns the price property
    org = make_org("price")
    user = _architect_user(org.id)
    db.session.commit()

    with tenant_ctx(org.id):
        value = ArchiMateElement(
            name="Entelim Startup Plan",
            type="Value",
            layer="motivation",
            description="$49 a month or $490 a year per organisation",
            custom_properties={"price": "49 USD per month, 490 USD per year, ten people"},
            organization_id=org.id,
        )
        db.session.add(value)
        db.session.commit()

        executor = ToolExecutor(user_id=user.id)
        result = executor._tool_explain_element({"element_name": "Entelim Startup Plan"})
        assert result["success"] is True
        data = result["result"]
        price = data["custom_properties"].get("price")
        assert price == "49 USD per month, 490 USD per year, ten people"
        # The description also mentions the price
        assert "$49 a month" in data["description"]


def test_tool_name_not_in_answer_when_not_found(db_session, make_org, tenant_ctx):
    """When explain_element finds nothing, the error message doesn't contain the tool name."""
    org = make_org("toolname")
    user = _architect_user(org.id)
    db.session.commit()

    with tenant_ctx(org.id):
        executor = ToolExecutor(user_id=user.id)

        result = executor._tool_explain_element({"element_name": "NonExistent Element"})
        assert result["success"] is False
        # The error should be in plain words, not contain "explain_element"
        error_text = str(result.get("error", ""))
        assert "explain_element" not in error_text.lower()
        assert "_tool_explain_element" not in error_text.lower()


def _problem(org, user):
    from app.models.solution_architect_models import SolutionAnalysisSession, SolutionProblemDefinition

    session = SolutionAnalysisSession(name=f"session-{uuid.uuid4().hex[:6]}", created_by_id=user.id, organization_id=org.id)
    db.session.add(session)
    db.session.flush()
    problem = SolutionProblemDefinition(session_id=session.id, problem_description="p", organization_id=org.id)
    db.session.add(problem)
    db.session.flush()
    return problem


def _solution(org, user):
    from app.models.solution_models import Solution

    solution = Solution(name=f"solution-{uuid.uuid4().hex[:6]}", organization_id=org.id, created_by_id=user.id)
    db.session.add(solution)
    db.session.flush()
    return solution


def _mirror(org, kind, name):
    element = ArchiMateElement(name=name, type=kind, layer="motivation", organization_id=org.id,
                               description=f"{name} as the model records it")
    db.session.add(element)
    db.session.flush()
    return element


def _record(table, org, user, name, element):
    """One Solution* motivation record called *name*, mirrored by *element*."""
    from app.models.solution_architect_models import (
        ConstraintType, DriverType, SolutionConstraint, SolutionDriver, SolutionGoal, SolutionRequirement,
    )
    from app.models.solution_lifecycle_models import SolutionRisk

    common = dict(organization_id=org.id, archimate_element_id=element.id)
    if table == "driver":
        row = SolutionDriver(problem_id=_problem(org, user).id, name=name, description="Pressure from the market",
                             driver_type=list(DriverType)[0], impact_level=4, **common)
    elif table == "goal":
        row = SolutionGoal(problem_id=_problem(org, user).id, name=name, description="Cut run cost",
                           measurement_criteria="Annual run cost", priority=2, **common)
    elif table == "constraint":
        row = SolutionConstraint(problem_id=_problem(org, user).id, name=name, description="Fixed ceiling",
                                 constraint_type=list(ConstraintType)[0], value="500000", unit="USD", **common)
    elif table == "requirement":
        row = SolutionRequirement(solution_id=_solution(org, user).id, name=name, description="Must log every change",
                                  priority=1, acceptance_criteria="Audit trail present", **common)
    else:
        row = SolutionRisk(solution_id=_solution(org, user).id, risk_name=name, risk_description="Supplier may fail",
                           impact="high", probability="low", mitigation="Second source", **common)
    db.session.add(row)
    db.session.flush()
    return row


# table -> (the element type its mirror has, a field the typed record must show)
TABLES = {
    "driver": ("Driver", "impact_level", 4),
    "goal": ("Goal", "measurement_criteria", "Annual run cost"),
    "constraint": ("Constraint", "value", "500000"),
    "requirement": ("Requirement", "acceptance_criteria", "Audit trail present"),
    "risk": ("Assessment", "mitigation", "Second source"),
}


@pytest.mark.parametrize("table", sorted(TABLES))
def test_a_motivation_record_is_found_by_its_own_name_and_explained(db_session, make_org, tenant_ctx, table):
    """The record's name differs from its mirror's, so only the motivation table can find it.
    A second organisation holds a record of the same name; it is never returned."""
    kind, field, expected = TABLES[table]
    mine, theirs = make_org(f"mine-{table}"), make_org(f"theirs-{table}")
    me, other = _architect_user(mine.id), _architect_user(theirs.id)
    record_name = f"Programme {table} {uuid.uuid4().hex[:6]}"
    with tenant_ctx(theirs.id):
        theirs_element = _mirror(theirs, kind, f"Their element {table}")
        _record(table, theirs, other, record_name, theirs_element)
        db.session.commit()
    with tenant_ctx(mine.id):
        my_element = _mirror(mine, kind, f"My element {table}")
        _record(table, mine, me, record_name, my_element)
        db.session.commit()

        found = EntityResolver().resolve_archimate_element(record_name)
        assert found["resolved"] is True, found
        assert found["id"] == my_element.id
        assert found["id"] != theirs_element.id

        explained = ToolExecutor(user_id=me.id)._tool_explain_element({"element_name": record_name})
        assert explained["success"] is True, explained
        typed = explained["result"]["typed_record"]
        assert typed[field] == expected
        assert explained["result"]["element_id"] == my_element.id
        assert explained["result"]["description"] == f"{my_element.name} as the model records it"


def test_a_partial_name_of_a_motivation_record_lists_only_my_organisation(db_session, make_org, tenant_ctx):
    mine, theirs = make_org("partial-mine"), make_org("partial-theirs")
    me, other = _architect_user(mine.id), _architect_user(theirs.id)
    with tenant_ctx(theirs.id):
        _record("goal", theirs, other, "Retire the old ledger A", _mirror(theirs, "Goal", "Their goal element"))
        db.session.commit()
    with tenant_ctx(mine.id):
        _record("goal", mine, me, "Retire the old ledger B", _mirror(mine, "Goal", "My first goal element"))
        _record("goal", mine, me, "Retire the old ledger C", _mirror(mine, "Goal", "My second goal element"))
        db.session.commit()
        found = EntityResolver().resolve_archimate_element("old ledger")
        assert found["resolved"] is False
        assert sorted(c["name"] for c in found["candidates"]) == ["My first goal element", "My second goal element"]


def test_the_description_comes_first_and_the_property_keeps_its_source(db_session, make_org, tenant_ctx):
    """A price card withdrawn in the description, a price still held as a property: the answer
    carries both, description first, and the property says where it came from."""
    org = make_org("withdrawn")
    user = _architect_user(org.id)
    with tenant_ctx(org.id):
        db.session.add(ArchiMateElement(
            name="Team price card", type="Value", layer="motivation", organization_id=org.id,
            description="This price card was withdrawn in March",
            custom_properties={"price": "24 / 49 USD per month"}, source_kind="import", source_label="prices.csv",
        ))
        db.session.commit()
        out = ToolExecutor(user_id=user.id)._tool_explain_element({"element_name": "Team price card"})
        assert out["success"] is True
        message = out["message"]
        assert "withdrawn" in message and "price: 24 / 49 USD per month" in message
        assert message.index("withdrawn") < message.index("price: 24")
        assert "(Imported from prices.csv)" in message or "(imported from prices.csv)" in message.lower()
        assert list(out["result"]).index("description") < list(out["result"]).index("custom_properties")
