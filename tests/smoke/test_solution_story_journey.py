"""Journey, in a browser: the demonstration solution tells its story, and its risk heat map is the right way up.

The demonstration organisation is seeded by the product's own seed. The person who signs in is the demo Enterprise
Architect (persona enterprise_architect, platform role Viewer, not an administrator):

* opens "Field Calibration Platform" and finds its fifteen blueprint sections written, a completeness figure above
  zero that equals what the sections score, governance and compliance that follow from those scores;
* opens the solution's risk heat map and finds probability rising to the right, impact rising upward, each cell
  coloured from its own probability times impact, and every risk of the solution plotted in its cell, the same
  figures as the solution's risk list; opens a cell, follows a risk to its own page, edits a risk and sees it move.

A second organisation's person asking for the same heat map is refused, and a solution with no risks says why.
"""
import os
import pathlib
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
SOLUTION = "Field Calibration Platform"
SHOTS = pathlib.Path(os.environ.get("DEMO02_SHOTS", "/tmp/demo02-shots"))
BAND_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


@pytest.fixture(scope="module")
def demo_org(live_server):
    """Seed the demonstration organisation with the product's own seed."""
    from app import create_app
    from app.commands.seed_demo_company import seed_demo_company

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    app = create_app("testing")
    try:
        with app.app_context():
            seed_demo_company()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return DEMO_EMAIL


def _facts(email, solution_name):
    """What the database says: the signed-in person's roles, the solution id,
    its section scores and the risks the solution's own list returns."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.solution_models import Solution
        from app.models.user import User
        from app.services import risk_service

        user = User.query.filter_by(email=email).one()
        solution = Solution.query.filter_by(
            name=solution_name, organization_id=user.organization_id).one()
        canonical = risk_service.risks_linked_to("solution", solution.id)
        out = {
            "enterprise_role": user.enterprise_role,
            "platform_role": user.role.name if user.role else None,
            "solution_id": solution.id,
            "scores": dict(solution.section_scores or {}),
            "risks": sorted((r.likelihood, r.impact) for r in canonical),
        }
        db.session.remove()
        return out


def _open(browser, base, email, width, dark=False):
    context = browser.new_context(viewport={"width": width, "height": 900},
                                  color_scheme="dark" if dark else "light")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    sign_in(page, base, email)
    return context, page, errors


def _shot(page, name, width, theme="light"):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, theme, width))), full_page=True)


@pytest.mark.parametrize("width", [1440, 390])
def test_the_architect_opens_the_solution_and_reads_its_story(browser, live_server, demo_org, width):
    from app.modules.solutions_strategic.v2.services.blueprint_completeness_service import (
        BLUEPRINT_SECTION_IDS,
    )

    facts = _facts(demo_org, SOLUTION)
    assert facts["enterprise_role"] == "enterprise_architect"  # the persona, not an administrator
    assert facts["platform_role"] not in ("Administrator", "Platform Admin")
    scores = facts["scores"]
    overall = [scores.get(key, {}).get("overall", 0) for key in BLUEPRINT_SECTION_IDS]
    written = sum(1 for value in overall if value >= 80)
    mean = round(sum(overall) / len(overall))
    assert written > 0 and mean > 0, "the seed wrote nothing: %s" % overall
    context, page, errors = _open(browser, live_server, demo_org, width)
    try:
        page.goto("%s/solutions/%d" % (live_server, facts["solution_id"]),
                  wait_until="networkidle", timeout=PAGE_TIMEOUT)
        sections = page.locator("[data-bp-section]")
        ids = [sections.nth(i).get_attribute("id") for i in range(sections.count())]
        for key in BLUEPRINT_SECTION_IDS:
            assert key in ids, "%s is not on the page" % key

        # The executive summary is the written one, not the placeholder.
        summary = page.locator("textarea[aria-label='Executive summary']").input_value()
        assert "Field Calibration Platform" in summary and len(summary.split()) >= 100, summary[:80]

        # The architecture decisions are written, with the options rejected.
        decisions = page.locator("textarea[aria-label='Decision narrative']").input_value()
        assert "calibration ledger" in decisions.lower() and "rejected" in decisions.lower(), decisions[:80]

        if width >= 1024:
            body = page.inner_text("body")
            shown = re.search(r"(\d+) of (\d+) sections complete", body)
            assert shown, "the completeness caption is missing"
            assert int(shown.group(2)) == 15
            # The caption counts exactly the sections the blueprint scoring rates complete.
            assert int(shown.group(1)) == written, (shown.group(0), written)
            ring = re.search(r"Completeness\s+(\d+)%", body, re.I)
            assert ring and int(ring.group(1)) == mean, (ring and ring.group(1), mean)
            # Compliance is the same figure, and governance follows from every section being ready.
            compliance = re.search(r"Compliance\s+(\d+)%", body, re.I)
            assert compliance and int(compliance.group(1)) == mean
            assert "Proposed to the ARB" in body
        else:
            # Below the desktop width the rail is folded away: the section strip carries the figures.
            chips = page.locator("div.sticky button[title*='%']")
            shown = [chips.nth(i).text_content().split()[-1] for i in range(chips.count())]
            assert shown[:15] == ["%d%%" % v for v in overall], (shown, overall)
        _shot(page, "solution-overview", width)
        assert not errors, errors
    finally:
        context.close()


def _grid(page):
    cells = {}
    for el in page.locator("[data-testid^='cell-'][data-probability]").all():
        cells[(int(el.get_attribute("data-probability")), int(el.get_attribute("data-impact")))] = {
            "band": el.get_attribute("data-band"),
            "count": int(el.get_attribute("data-count")),
            "box": el.bounding_box(),
        }
    return cells


@pytest.mark.parametrize("width", [1440, 390])
def test_the_heat_map_is_the_right_way_up_and_plots_every_risk(browser, live_server, demo_org, width):
    facts = _facts(demo_org, SOLUTION)
    assert facts["enterprise_role"] == "enterprise_architect"
    context, page, errors = _open(browser, live_server, demo_org, width)
    try:
        # The solution's own risk list (the interface its risk tab reads).
        status, listing = api(page, "GET", "/solutions/%d/risks" % facts["solution_id"])
        assert status == 200
        listed = listing["data"]
        assert len(listed) == 6, listed

        page.goto("%s/solutions/%d/risks/heatmap" % (live_server, facts["solution_id"]),
                  wait_until="networkidle", timeout=PAGE_TIMEOUT)
        cells = _grid(page)
        assert len(cells) == 25
        # Probability rises to the right, impact rises upward.
        for impact in range(1, 6):
            for probability in range(1, 5):
                assert cells[(probability, impact)]["box"]["x"] < cells[(probability + 1, impact)]["box"]["x"]
        for probability in range(1, 6):
            for impact in range(1, 5):
                assert cells[(probability, impact)]["box"]["y"] > cells[(probability, impact + 1)]["box"]["y"]
        # Each cell is coloured from its own probability times impact.
        want = {(p, i): ("critical" if p * i >= 15 else "high" if p * i >= 9
                         else "medium" if p * i >= 5 else "low")
                for p in range(1, 6) for i in range(1, 6)}
        assert {k: v["band"] for k, v in cells.items()} == want
        assert cells[(5, 5)]["band"] == "critical"          # top right
        assert cells[(5, 1)]["band"] == "medium"             # likely but small: not critical
        assert cells[(1, 5)]["band"] == "medium"
        assert cells[(1, 1)]["band"] == "low"
        # Colour is drawn from the band, and the bands differ from one another.
        colours = {}
        for key in ((1, 1), (5, 1), (3, 4), (5, 5)):
            colours[key] = page.locator(
                "[data-testid='cell-%d-%d']" % key).evaluate("e => getComputedStyle(e).backgroundColor")
        assert len(set(colours.values())) == 4, colours

        # Every risk of the solution is plotted, the same figures as its list.
        plotted = sum(c["count"] for c in cells.values())
        assert plotted == len(listed)
        assert page.locator("[data-testid='plotted-total']").inner_text().startswith("%d risk" % len(listed))
        expected = {}
        for risk in facts["risks"]:
            expected[risk] = expected.get(risk, 0) + 1
        assert {k: v["count"] for k, v in cells.items() if v["count"]} == expected
        assert cells[(5, 5)]["count"] == 1 and cells[(4, 4)]["count"] == 2

        # Open the top-right cell: its risk is listed, and its link lands on that risk.
        page.locator("[data-testid='cell-5-5']").click()
        row = page.locator("#cell-5-5 [data-testid^='risk-row-']")
        expect(row).to_have_count(1)
        title = row.locator("a").inner_text()
        assert title == "Engineers keep using paper job sheets after go-live"
        _shot(page, "heat-map-cell", width)
        row.locator("a").click()
        page.wait_for_load_state("networkidle")
        assert title in page.inner_text("body")
        assert "/risks/" in page.url
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_the_heat_map_screens(browser, live_server, demo_org, width):
    facts = _facts(demo_org, SOLUTION)
    for dark in (False, True):
        context, page, errors = _open(browser, live_server, demo_org, width, dark=dark)
        try:
            if dark:
                page.add_init_script("localStorage.setItem('theme', 'dark')")
            page.goto("%s/solutions/%d/risks/heatmap" % (live_server, facts["solution_id"]),
                      wait_until="networkidle", timeout=PAGE_TIMEOUT)
            if dark:
                page.evaluate("document.documentElement.classList.add('dark')")
            # In either theme the four bands read as four different colours.
            colours = {
                page.locator("[data-testid='cell-%d-%d']" % key).evaluate(
                    "e => getComputedStyle(e).backgroundColor")
                for key in ((1, 1), (5, 1), (3, 4), (5, 5))
            }
            assert len(colours) == 4, colours
            _shot(page, "heat-map", width, "dark" if dark else "light")
            assert not errors, errors
        finally:
            context.close()


def test_a_risk_edited_on_the_heat_map_moves_and_persists(browser, live_server, demo_org):
    facts = _facts(demo_org, SOLUTION)
    context, page, errors = _open(browser, live_server, demo_org, 1440)
    title = "Accreditation audit delays certificate signing"

    def move(from_cell, to_probability):
        page.locator("[data-testid='cell-%s']" % from_cell).click()
        page.get_by_role("button", name="Edit " + title).click()
        form = page.locator("[data-testid='risk-edit-form']")
        expect(form).to_be_visible()
        form.locator("#edit-probability").select_option(str(to_probability))
        with page.expect_navigation():
            page.locator("[data-testid='risk-save']").click()
        page.wait_for_load_state("networkidle")

    try:
        url = "%s/solutions/%d/risks/heatmap" % (live_server, facts["solution_id"])
        page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert page.locator("[data-testid='cell-2-4']").get_attribute("data-count") == "1"
        move("2-4", 5)
        # After the reload the risk is in its new cell, and the same on a fresh visit.
        assert page.locator("[data-testid='cell-5-4']").get_attribute("data-count") == "1"
        assert page.locator("[data-testid='cell-2-4']").get_attribute("data-count") == "0"
        page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert page.locator("[data-testid='cell-5-4']").get_attribute("data-count") == "1"
        assert page.locator("[data-testid='cell-5-4']").get_attribute("data-band") == "critical"
        # Put it back so the other journeys read the seeded grid.
        move("5-4", 2)
        assert page.locator("[data-testid='cell-2-4']").get_attribute("data-count") == "1"
        assert not errors, errors
    finally:
        context.close()


def test_another_organisation_cannot_open_the_heat_map_and_an_empty_solution_says_why(
        browser, live_server, demo_org):
    facts = _facts(demo_org, SOLUTION)
    other = create_fresh_org("enterprise_architect")
    email = other["emails"]["enterprise_architect"]
    context, page, _errors = _open(browser, live_server, email, 1440)
    try:
        response = page.goto("%s/solutions/%d/risks/heatmap" % (live_server, facts["solution_id"]),
                             wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 404
        assert SOLUTION not in page.inner_text("body")
    finally:
        context.close()


def test_a_solution_without_risks_explains_the_empty_grid(browser, live_server, demo_org):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.solution_models import Solution
        from app.models.user import User

        user = User.query.filter_by(email=DEMO_EMAIL).one()
        sol = Solution(name="Empty risk story", organization_id=user.organization_id,
                       created_by_id=user.id)
        db.session.add(sol)
        db.session.commit()
        solution_id = sol.id
        db.session.remove()
    context, page, errors = _open(browser, live_server, demo_org, 1440)
    try:
        page.goto("%s/solutions/%d/risks/heatmap" % (live_server, solution_id),
                  wait_until="networkidle", timeout=PAGE_TIMEOUT)
        body = page.inner_text("body")
        assert "No risks recorded for this solution" in body
        assert "placed on this grid by their probability and impact" in body
        link = page.get_by_role("link", name="Add a risk on the solution")
        link.click()
        page.wait_for_load_state("networkidle")
        assert "Empty risk story" in page.inner_text("body")
        assert not errors, errors
    finally:
        context.close()
        with app.app_context():
            from app.models.solution_models import Solution

            db.session.query(Solution).filter(Solution.id == solution_id).delete()
            db.session.commit()
            db.session.remove()
