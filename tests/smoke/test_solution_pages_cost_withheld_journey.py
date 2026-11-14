"""A solution's record and its analysis session show money only to people the one cost rule allows.

Goal: "I open the solution made from an analysis and the analysis behind it. If my role may see spend I read the
estimate, the budget and the cost of each option; if it may not, I read what the solution is for, the options and
their timelines, one sentence saying cost figures are withheld for my role, and no amount, not even one written in
the text."

Two organisations each carry a solution with its own amounts; the solution architect and the finance person of the
first read the pages, and nothing of the second shows anywhere.
"""
import re
import uuid
from decimal import Decimal

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .cost_withheld_support import HELD_BACK, open_page, shot, shown_text
from .fresh_org import create_fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SIZES = [(1440, "light"), (390, "dark")]
AMOUNT = re.compile(r"[£$€]\s?\d")


def _figures(base):
    return dict(option_low=410000 + base * 1711, option_high=520000 + base * 2113, estimate=623000 + base * 1019,
                spent=198000 + base * 877, budget_low=300000 + base * 311, budget_high=900000 + base * 419)


def _seed(org, owner_role, figures, name):
    from app import create_app, db
    from app.models.solution_architect_models import (
        RecommendationOptionType,
        SolutionAnalysisSession,
        SolutionProblemDefinition,
        SolutionRecommendation,
    )
    from app.models.solution_models import Solution

    with create_app("testing").app_context():
        owner = org["user_ids"][owner_role]
        session = SolutionAnalysisSession(name=name, created_by_id=owner, organization_id=org["org_id"])
        db.session.add(session)
        db.session.flush()
        db.session.add(SolutionProblemDefinition(
            session_id=session.id, organization_id=org["org_id"], problem_description="Replace the ledger",
            budget_min=figures["budget_low"], budget_max=figures["budget_high"], timeline_months=9))
        for rank, option in enumerate((RecommendationOptionType.BUY, RecommendationOptionType.BUILD), start=1):
            db.session.add(SolutionRecommendation(
                session_id=session.id, organization_id=org["org_id"], option_type=option, rank=rank, score=80 - rank,
                confidence=0.8, estimated_cost_min=Decimal(figures["option_low"]),
                estimated_cost_max=Decimal(figures["option_high"]), timeline_months=9,
                justification="Roughly £{:,} to deliver".format(figures["option_high"])))
        solution = Solution(
            name=name, organization_id=org["org_id"], created_by_id=owner, estimated_cost=figures["estimate"],
            actual_cost=figures["spent"], analysis_session_id=session.id, status="planned", governance_status="draft",
            description="Replace the ledger, about £{:,} a year".format(figures["estimate"]))
        db.session.add(solution)
        db.session.commit()
        return {"session": session.id, "solution": solution.id, "name": name}


@pytest.fixture(scope="module")
def organisations(live_server):
    result = []
    for base in (1, 2):
        org = create_fresh_org("finance", extra_roles=("solution_architect",))
        org["figures"] = _figures(base)
        tag = uuid.uuid4().hex[:6]
        org["mine"] = _seed(org, "solution_architect", org["figures"], "Ledger replacement a%s" % tag)
        org["finance"] = _seed(org, "finance", org["figures"], "Ledger replacement f%s" % tag)
        result.append(org)
    return result


def _forms(figures):
    return [form for value in figures.values() for form in ("{:,}".format(value), str(value))]


@pytest.mark.parametrize("width,scheme", SIZES)
def test_a_solution_architect_opens_the_solution_and_its_session_and_reads_no_amount(
        browser, live_server, organisations, width, scheme):
    mine, theirs = organisations
    context, page = open_page(browser, live_server, mine["emails"]["solution_architect"], width, scheme)
    try:
        everything = _forms(mine["figures"]) + _forms(theirs["figures"])
        page.goto(live_server + "/solutions/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        with page.expect_navigation():
            page.get_by_role("link", name=mine["mine"]["name"]).first.click()
        expect(page.get_by_role("heading", name=mine["mine"]["name"]).first).to_be_visible()
        text = shown_text(page)
        assert text.count(HELD_BACK) == 1, "the record says the sentence %d times" % text.count(HELD_BACK)
        assert "Replace the ledger" in text, "the solution's own words stay"
        assert not AMOUNT.search(text), AMOUNT.search(text)
        assert [form for form in everything if form in page.content()] == []
        shot(page, "architect-solution-record", width, scheme)

        page.goto(live_server + "/solutions/architect/sessions/%d" % mine["mine"]["session"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Show problem definition tab").click()
        expect(page.get_by_text("Timeline", exact=True).first).to_be_visible()
        assert "Budget" not in shown_text(page), "a budget card is shown"
        page.get_by_role("button", name="Show recommendations tab").click()
        page.get_by_text("BUY").first.wait_for(timeout=PAGE_TIMEOUT)
        text = shown_text(page)
        assert text.count(HELD_BACK) == 1, "the session says the sentence %d times" % text.count(HELD_BACK)
        assert "9 months" in text, "the options keep their timelines"
        assert not AMOUNT.search(text), AMOUNT.search(text)
        assert [form for form in everything if form in page.content()] == []
        shot(page, "architect-solution-session", width, scheme)
    finally:
        context.close()


@pytest.mark.parametrize("width,scheme", SIZES)
def test_a_finance_person_opens_the_solution_and_its_session_and_reads_the_figures(
        browser, live_server, organisations, width, scheme):
    mine, theirs = organisations
    context, page = open_page(browser, live_server, mine["emails"]["finance"], width, scheme)
    try:
        figures = mine["figures"]
        page.goto(live_server + "/solutions/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        with page.expect_navigation():
            page.get_by_role("link", name=mine["finance"]["name"]).first.click()
        text = shown_text(page)
        assert HELD_BACK not in text
        assert "{:,}".format(figures["estimate"]) in text or "%dK" % round(figures["estimate"] / 1000) in text, \
            "the estimate is shown"
        shot(page, "finance-solution-record", width, scheme)

        page.goto(live_server + "/solutions/architect/sessions/%d" % mine["finance"]["session"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Show problem definition tab").click()
        expect(page.get_by_text("{:,}".format(figures["budget_low"])).first).to_be_visible()
        page.get_by_role("button", name="Show recommendations tab").click()
        page.get_by_text("BUY").first.wait_for(timeout=PAGE_TIMEOUT)
        text = shown_text(page)
        assert HELD_BACK not in text
        assert "{:,}".format(figures["option_low"]) in text
        assert not [form for form in _forms(theirs["figures"]) if form in page.content()]
        shot(page, "finance-solution-session", width, scheme)
    finally:
        context.close()
