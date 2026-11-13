"""The solutions architecture dashboard and the analyse reply behind it show money only to people the one cost rule allows.

Goal: "I open the solutions architecture dashboard and run an analysis. If my role may see spend I read the
estimated cost of each recommendation and the annual cost of each existing application; if it may not, I read
one sentence saying cost figures are withheld for my role, and no amount in the page or in the reply behind it."

Two organisations each carry their own solutions; the finance person and the solution architect of the first
read the pages, and nothing of the second shows anywhere.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .cost_withheld_support import HELD_BACK, holds_none_of, open_page, shot, shown_text
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SIZES = [(1440, "light"), (390, "dark")]


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
                lifecycle_status="operational", total_cost_of_ownership=fig["app_cost"],
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


def _analyse(page, live_server, problem="Replace the legacy ledger"):
    return page.evaluate(
        """async (path) => {
            const meta = document.querySelector('meta[name="csrf-token"]');
            const headers = {'Accept': 'application/json'};
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
        live_server + "/architecture/solutions-architecture/analyze",
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

        # The analyse reply: estimated costs are null.
        status, body = _analyse(page, live_server)
        assert status == 200, (status, body)
        for rec in body.get("recommendations", []):
            assert rec.get("estimated_cost") is None, rec
        for app in (body.get("existing_applications") or {}).get("applications", []):
            assert app.get("annual_cost") is None, app
        assert body.get("costs_withheld_sentence") == HELD_BACK
        assert holds_none_of(str(body), everything) == []
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

        # The analyse reply carries costs for this organisation only.
        status, body = _analyse(page, live_server)
        assert status == 200, (status, body)
        assert holds_none_of(str(body), _amounts(theirs)) == []
        assert "costs_withheld_sentence" not in body
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
