"""The solutions architecture dashboard and the analyse reply behind it show money only to people the one cost rule allows.

Goal: "I open the solutions architecture dashboard and run an analysis. If my role may see spend I read the
estimated cost of each recommendation and the annual cost of each existing application; if it may not, I read
one sentence saying cost figures are withheld for my role, and no amount in the page or in the reply behind it."

Two organisations each carry their own solutions; the finance person and the solution architect of the first
read the pages, and nothing of the second shows anywhere.
"""
import os
import pathlib
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .cost_withheld_support import HELD_BACK, holds_none_of, open_page, shot, shown_text
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SIZES = [(1440, "light"), (390, "dark")]
STAND_IN = pathlib.Path(__file__).parent / "ai_stand_in"


@pytest.fixture(scope="module")
def live_server(request, ai_protocol_stub, app):
    """A server whose one AI call, the options analysis, is answered by a fixed test reply.

    The analyser no longer hides an AI failure, so a live server with no AI provider answers an error; this
    server answers with options whose text carries amounts, and the journey reads what a person is shown.
    """
    path = os.pathsep.join(filter(None, [str(STAND_IN), os.environ.get("PYTHONPATH", "")]))
    yield boot_live_server(request, ai_protocol_stub, app, extra_env={"PYTHONPATH": path})


@pytest.fixture(scope="module")
def organisations(live_server):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.solution_models import Solution

    orgs = []
    for base in (1, 2):
        org = create_fresh_org("finance", extra_roles=("solution_architect",))
        tag = org["suffix"]
        fig = dict(
            rec_cost=50000 + base * 1234,
            app_cost=80000 + base * 555,
        )
        org["figures"] = fig
        org["tag"] = tag
        with create_app("testing").app_context():
            app_row = ApplicationComponent(
                name="App %s" % tag, organization_id=org["org_id"],
                description="Replace the legacy ledger", lifecycle_status="active",
                total_cost_of_ownership=fig["app_cost"],
            )
            db.session.add(app_row)
            db.session.commit()
            sol = Solution(
                name="Solution %s" % tag, description="Analysed option %s" % tag,
                organization_id=org["org_id"],
                created_by_id=org["user_ids"]["solution_architect"],
                governance_status="draft",
            )
            db.session.add(sol)
            db.session.commit()
            org["app_id"] = app_row.id
            org["sol_id"] = sol.id
        orgs.append(org)
    return orgs


def _amounts(org):
    return [org["figures"][key] for key in ("rec_cost", "app_cost")]


def _analyse(page, live_server, path):
    return page.evaluate(
        """async (path) => {
            const meta = document.querySelector('meta[name="csrf-token"]');
            const headers = {'Accept': 'application/json', 'Content-Type': 'application/json'};
            if (meta) headers['X-CSRFToken'] = meta.content;
            const r = await fetch(path, {
                method: 'POST',
                headers: headers,
                credentials: 'same-origin',
                body: JSON.stringify({problem_description: 'Replace the legacy ledger'})
            });
            const text = await r.text();
            let body = text;
            try { body = JSON.parse(text); } catch (e) { /* not JSON */ }
            return [r.status, body];
        }""",
        live_server + path,
    )


ANALYZE_PATHS = (
    "/architecture/solutions-architecture/analyze",
    "/solutions/architect/api/analyze",
)


@pytest.mark.parametrize("width,scheme", SIZES)
def test_a_solution_architect_reads_one_sentence_and_no_amount_on_solutions_architecture(
        browser, live_server, organisations, width, scheme):
    mine, theirs = organisations
    context, page = open_page(browser, live_server, mine["emails"]["solution_architect"], width, scheme)
    try:
        everything = _amounts(mine) + _amounts(theirs)

        # The dashboard: no cost figure anywhere (the withholding sentence appears inline
        # only when results exist, so it is not on the empty dashboard).
        page.goto(live_server + "/architecture/solutions-architecture",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        text = shown_text(page)
        assert HELD_BACK not in text, "the dashboard does not print the sentence unless costs are withheld"
        assert holds_none_of(page.content(), everything) == []
        shot(page, "architect-solarch-dashboard", width, scheme)

        # The analyse replies on both routes: the analyse set is not empty, every amount is null,
        # and one sentence says the figures are withheld.
        for path in ANALYZE_PATHS:
            status, body = _analyse(page, live_server, path)
            assert status == 200, (path, status, body)
            analysis = body.get("analysis") if "analysis" in body else body
            existing = analysis.get("existing_applications") or {}
            assert existing.get("found_count", 0) >= 1, (path, existing)
            assert len(analysis.get("recommendations", [])) >= 1, path
            assert existing.get("estimated_savings") is None, (path, existing)
            for rec in analysis.get("recommendations", []):
                assert rec.get("estimated_cost") is None, (path, rec)
                assert "£" not in (rec.get("description") or ""), (path, rec)
                for bullet in (rec.get("pros") or []) + (rec.get("cons") or []):
                    assert "£" not in bullet, (path, bullet)
                details = rec.get("details") or {}
                assert details.get("maintenance_cost_annual") is None, (path, details)
                for app in details.get("applications", []) or []:
                    assert app.get("annual_cost") is None, (path, app)
            for app in existing.get("applications", []):
                assert app.get("annual_cost") is None, (path, app)
            assert HELD_BACK in str(body), path
            assert holds_none_of(str(body), everything) == [], path
            # the amounts the AI wrote into its options never reach this person
            options = analysis["buy_build_analysis"]["options"]
            assert options and options[0]["pros"], (path, analysis["buy_build_analysis"])
            assert "40,000" not in str(body) and "250,000" not in str(body), path
        status, body = _analyse(page, live_server, ANALYZE_PATHS[0])
        assert body.get("costs_withheld_sentence") == HELD_BACK
    finally:
        context.close()


def test_a_finance_person_reads_the_solutions_architecture_figures(browser, live_server, organisations):
    mine, theirs = organisations
    context, page = open_page(browser, live_server, mine["emails"]["finance"], 1440, "light")
    try:
        page.goto(live_server + "/architecture/solutions-architecture",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert HELD_BACK not in shown_text(page)
        assert holds_none_of(page.content(), _amounts(theirs)) == []
        shot(page, "finance-solarch-dashboard", 1440, "light")

        # The analyse replies on both routes: the set is not empty, this organisation's costs are
        # in the reply, the other organisation's are not, and no withheld sentence appears.
        for path in ANALYZE_PATHS:
            status, body = _analyse(page, live_server, path)
            assert status == 200, (path, status, body)
            analysis = body.get("analysis") if "analysis" in body else body
            existing = analysis.get("existing_applications") or {}
            assert existing.get("found_count", 0) >= 1, (path, existing)
            assert len(analysis.get("recommendations", [])) >= 1, path
            assert "costs_withheld_sentence" not in str(body), path
            text = str(body)
            assert holds_none_of(text, _amounts(theirs)) == [], path
            assert holds_none_of(text, []) == [] and any(
                form in text for form in ("{:,.0f}".format(mine["figures"]["app_cost"]),
                                          str(int(mine["figures"]["app_cost"])))), path
    finally:
        context.close()


def test_the_refused_view_fits_a_phone(browser, live_server, organisations):
    mine, _ = organisations
    page = browser.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    try:
        from .test_archetype_journeys import _login
        _login(page, live_server, mine["emails"]["solution_architect"])
        assert page.goto(live_server + "/architecture/solutions-architecture", timeout=PAGE_TIMEOUT).status == 200
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        shot(page, "architect-solarch-phone", 390, "dark")
    finally:
        page.close()


@pytest.mark.parametrize("width,scheme", SIZES)
def test_a_solution_architect_runs_the_analysis_and_reads_the_sentence_once_without_a_cost_column(
        browser, live_server, organisations, width, scheme):
    """From the goal 'what are my options for this need': the architect types the need, clicks Run Analysis, and
    reads the options, the applications that already serve the need and the reason costs are missing, once."""
    mine, theirs = organisations
    context, page = open_page(browser, live_server, mine["emails"]["solution_architect"], width, scheme)
    try:
        page.goto(live_server + "/architecture/solutions-architecture",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_label("Problem / Requirement").fill("Replace the legacy ledger")
        page.get_by_role("button", name="Run Analysis").click()
        page.get_by_text("Top Recommendation").wait_for(timeout=PAGE_TIMEOUT)
        page.get_by_text("App %s" % mine["tag"]).first.wait_for(timeout=PAGE_TIMEOUT)
        text = shown_text(page)
        assert text.count(HELD_BACK) == 1, "the sentence is said %d times, not once" % text.count(HELD_BACK)
        assert page.get_by_role("columnheader", name="Annual Cost").count() == 0, "a cost column is shown"
        assert holds_none_of(page.content(), _amounts(mine) + _amounts(theirs)) == []
        assert "£" not in text
        assert page.get_by_text("App %s" % theirs["tag"]).count() == 0
        shot(page, "architect-solarch-results", width, scheme)
    finally:
        context.close()


def test_a_finance_person_runs_the_analysis_and_reads_the_cost_column(browser, live_server, organisations):
    mine, theirs = organisations
    context, page = open_page(browser, live_server, mine["emails"]["finance"], 1440, "light")
    try:
        page.goto(live_server + "/architecture/solutions-architecture",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_label("Problem / Requirement").fill("Replace the legacy ledger")
        page.get_by_role("button", name="Run Analysis").click()
        page.get_by_text("App %s" % mine["tag"]).first.wait_for(timeout=PAGE_TIMEOUT)
        assert page.get_by_role("columnheader", name="Annual Cost").count() == 1
        assert HELD_BACK not in shown_text(page)
        assert any(form in page.content() for form in ("{:,.0f}".format(mine["figures"]["app_cost"]),
                                                       str(int(mine["figures"]["app_cost"]))))
    finally:
        context.close()
