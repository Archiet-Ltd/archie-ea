"""A solution's saved session, its record and the solution list give money only to a role the cost rule allows.

Written from two goals: a solution architect without spend authority opens an analysis session they ran, the
solution record made from it and the solution list, and reads no budget, estimate or amount in the text, and
the reason once on each page; the chief technology officer opens the same pages and reads every figure the
analysis saved. A second organisation holds a solution with other money, so a leak across organisations would
also show.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

HELD = "Cost figures are withheld for your role."
OWN = (489600, 734400, 612000, 623459, 734561, 250000, 900000)
OTHER = (777001, 777002)


def _forms(*amounts):
    return [form for amount in amounts for form in ("{:,.0f}".format(amount), str(int(amount)))]


def _user(db_session, org, role):
    from app.models.user import Role, User

    user = User(email="sol-%s@example.com" % uuid.uuid4().hex[:8], first_name="S", last_name="P",
                organization_id=org.id, confirmed=True, enterprise_role=role)
    user.password = "Sup3rSecret!23"
    user.role = Role.query.filter_by(name="Architect").first()
    db_session.add(user)
    db_session.flush()
    return user


def _estate(db_session, org, owner, tag, own=True):
    """A session with a problem and three costed recommendations, and the solution accepted from it."""
    from app.models.solution_architect_models import (
        RecommendationOptionType,
        SolutionAnalysisSession,
        SolutionProblemDefinition,
        SolutionRecommendation,
    )
    from app.models.solution_models import Solution

    amounts = OWN if own else OTHER + OTHER
    session = SolutionAnalysisSession(name="Ledger replacement %s" % tag, created_by_id=owner.id, organization_id=org.id)
    db_session.add(session)
    db_session.flush()
    db_session.add(SolutionProblemDefinition(
        session_id=session.id, organization_id=org.id, problem_description="Replace the ledger",
        budget_min=amounts[5 % len(amounts)], budget_max=amounts[6 % len(amounts)], timeline_months=9))
    for rank, option in enumerate((RecommendationOptionType.BUY, RecommendationOptionType.BUILD,
                                   RecommendationOptionType.REUSE), start=1):
        db_session.add(SolutionRecommendation(
            session_id=session.id, organization_id=org.id, option_type=option, rank=rank, score=80 - rank, confidence=0.8,
            estimated_cost_min=Decimal(amounts[(rank - 1) % len(amounts)]),
            estimated_cost_max=Decimal(amounts[rank % len(amounts)]), timeline_months=9,
            justification="est. £%s" % "{:,.0f}".format(amounts[2 % len(amounts)])))
    solution = Solution(name="Ledger %s" % tag, organization_id=org.id, created_by_id=owner.id,
                        estimated_cost=amounts[3 % len(amounts)], actual_cost=amounts[4 % len(amounts)],
                        description="Replace the ledger, est. £%s a year" % "{:,.0f}".format(amounts[2 % len(amounts)]),
                        analysis_session_id=session.id, status="planned", governance_status="draft")
    db_session.add(solution)
    db_session.flush()
    return session, solution


@pytest.fixture
def world(db_session, make_org):
    tag = uuid.uuid4().hex[:6]
    mine, theirs = make_org("sp-a"), make_org("sp-b")
    architect = _user(db_session, mine, "solution_architect")
    cto = _user(db_session, mine, "cto")
    session, solution = _estate(db_session, mine, architect, tag)
    cto_session, cto_solution = _estate(db_session, mine, cto, "c" + tag)
    other_architect = _user(db_session, theirs, "solution_architect")
    _estate(db_session, theirs, other_architect, "o" + tag, own=False)
    return {"architect": architect, "cto": cto, "session": session, "solution": solution,
            "cto_session": cto_session, "cto_solution": cto_solution, "tag": tag}


def _text(response):
    import html

    return html.unescape(response.get_data(as_text=True))


def _no_amount(text, where):
    for form in _forms(*OWN, *OTHER):
        assert form not in text, "%s printed %s to a person the cost rule withholds from" % (where, form)
    assert not re.search(r"£\s?\d", text), "%s printed a pound amount" % where


def test_the_withheld_architect_reads_the_session_without_an_amount(client, login_as, world):
    login_as(client, world["architect"])
    for path in ("/solutions/architect/sessions/%d" % world["session"].id,
                 "/solutions/architect/workspace/%d" % world["session"].id):
        response = client.get(path)
        assert response.status_code == 200, path
        text = _text(response)
        assert world["session"].name in text and "months" in text, "the session is shown at %s" % path
        main = text[text.index("<main"):] if "<main" in text else text
        assert main.count(HELD) == 1, "%s says the sentence %d times" % (path, main.count(HELD))
        _no_amount(text, path)


def test_the_chief_technology_officer_reads_the_session_with_its_figures(client, login_as, world):
    login_as(client, world["cto"])
    text = _text(client.get("/solutions/architect/sessions/%d" % world["cto_session"].id))
    assert "489,600" in text and "734,400" in text and "250,000" in text
    assert HELD not in text


def test_the_withheld_architect_reads_the_solution_record_without_an_amount(client, login_as, world):
    login_as(client, world["architect"])
    response = client.get("/solutions/%d" % world["solution"].id)
    assert response.status_code == 200
    text = _text(response)
    assert world["solution"].name in text
    assert text.count(HELD) == 1, "the record says the sentence %d times" % text.count(HELD)
    _no_amount(text, "the solution record")
    assert "Replace the ledger" in text, "the solution's own words stay"
    edit = _text(client.get("/solutions/%d?edit=1" % world["solution"].id))
    _no_amount(edit, "the solution editor")


def test_the_chief_technology_officer_reads_the_solution_record_with_its_figures(client, login_as, world):
    login_as(client, world["cto"])
    text = _text(client.get("/solutions/%d" % world["cto_solution"].id))
    assert "623,459" in text or "623459" in text or "623K" in text or "USD 623" in text, "the estimate is shown"
    assert HELD not in text


def test_the_solution_list_feed_holds_no_amount_for_the_architect_and_the_figures_for_the_cto(client, login_as, world):
    login_as(client, world["architect"])
    rows = client.get("/architecture/api/solutions").get_json()
    mine = [row for row in rows if row["name"] == world["solution"].name]
    assert len(mine) == 1
    assert all(row["estimated_cost"] is None and row["actual_cost"] is None for row in rows)
    assert not any("Ledger o" in (row["name"] or "") for row in rows), "the other organisation's solution is not listed"
    _no_amount(str(rows), "the solution list feed")

    login_as(client, world["cto"])
    rows = client.get("/architecture/api/solutions").get_json()
    mine = [row for row in rows if row["name"] == world["cto_solution"].name]
    assert mine and mine[0]["estimated_cost"] == 623459.0 and mine[0]["actual_cost"] == 734561.0
