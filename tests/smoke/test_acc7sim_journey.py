"""A signed-in architect tries a change on a copy of the model, saves it as a scenario and compares two.

In the Lantern Quay Systems demonstration organisation, as its architect:
- opens /simulations and gets the simulation screen, not the waiting list;
- picks an application and "Decommission / Retirement", runs it, and reads the counts, running cost and
  risks before and after, while the real model (counted in the database before and after) is untouched
  and the analysis history gets no new row;
- follows a risk from the result and lands on that risk;
- saves the scenario by naming it in a dialog (no browser prompt; the button is announced with its full
  name), saves a second one, reloads, and compares the two side by side;
- on Impact Analysis, chooses "Minor Modification" and reads that wording in the report, a cost at risk,
  and an application, an owner and a health cell in every dependency row that is a word, not a dash.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("ACC7SIM_WALK_DIR")
DEMO_EMAIL = "demo@lantern-quay.example.com"
ELEMENT = "Calibration Ledger"
VIEWPORTS = [
    ({"width": 1440, "height": 900}, "light"),
    ({"width": 390, "height": 844}, "dark"),
]
VIEWPORT_IDS = ["1440-light", "390-dark"]


def _shot(page, name, viewport, scheme):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%d-%s.png" % (name, viewport["width"], scheme)), full_page=True)


@pytest.fixture(scope="module")
def demo(live_server, app):
    from app import db
    from app.commands.seed_demo_company import seed_demo_company

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            seed_demo_company()
            # a database seeded earlier keeps its first password: the journey signs in with this run's
            from app.models.user import User

            User.query.filter_by(email=DEMO_EMAIL).one().password = PASSWORD
            db.session.commit()
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    return True


def _model_counts(app):
    """What the demonstration organisation holds, counted straight from the database."""
    from sqlalchemy import text

    from app import db

    with app.app_context():
        org = db.session.execute(text("select id from organizations where slug = 'lantern-quay'")).scalar_one()

        def count(table):
            return db.session.execute(text("select count(*) from %s where organization_id = :o" % table), {"o": org}).scalar_one()

        counts = {name: count(name) for name in
                  ("archimate_elements", "archimate_relationships", "application_components", "risks")}
        # the analysis history belongs to the organisation through the person who ran it
        counts["analysis_history"] = db.session.execute(text(
            "select count(*) from impact_analysis_results where created_by_id in "
            "(select id from users where organization_id = :o)"), {"o": org}).scalar_one()
        costs = db.session.execute(text(
            "select coalesce(sum(total_cost_of_ownership), 0) from application_components where organization_id = :o"),
            {"o": org}).scalar_one()
        counts["cost"] = float(costs)
        db.session.remove()
        return counts


def _sign_in(browser, live_server, viewport, scheme):
    context = browser.new_context(viewport=viewport, color_scheme=scheme, ignore_https_errors=True)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    page.on("dialog", lambda dialog: pytest.fail("a browser dialog opened: " + dialog.message))
    page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", DEMO_EMAIL)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    return context, page


def _pick(page, name=ELEMENT, change="Decommission / Retirement"):
    page.get_by_label("Type to search...").fill(name)
    page.get_by_role("button", name=re.compile("^" + re.escape(name))).first.click()
    page.get_by_label("Change Type").select_option(label=change)


def _number(text):
    return int(re.sub(r"[^\d]", "", text))


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_simulations_opens_the_screen_and_runs_a_retirement_on_a_copy(browser, live_server, app, demo, viewport, scheme):
    context, page = _sign_in(browser, live_server, viewport, scheme)
    try:
        page.goto(live_server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("early-access-page").count() == 0, "the waiting list is showing to a signed-in person"
        expect(page.get_by_role("heading", level=1)).to_have_text("Simulations")
        _shot(page, "simulations-empty", viewport, scheme)

        before = _model_counts(app)
        _pick(page)
        page.get_by_role("button", name="Run simulation").click()

        panel = page.get_by_test_id("simulation-panel")
        expect(panel).to_be_visible(timeout=PAGE_TIMEOUT)
        rows = {row.locator("td").nth(0).inner_text(): row for row in panel.get_by_test_id("simulation-figure").all()}
        assert {"Elements in the model", "Links between elements", "Applications",
                "Yearly running cost of all applications", "Open risks touching the change"} <= set(rows)

        # the "now" column is the real model, counted again from the database
        def now(label):
            return rows[label].locator("td").nth(1).inner_text()

        def after(label):
            return rows[label].locator("td").nth(2).inner_text()

        assert _number(now("Elements in the model")) == before["archimate_elements"]
        assert _number(now("Links between elements")) == before["archimate_relationships"]
        assert _number(now("Applications")) == before["application_components"]
        # retiring an application takes one element and one application out, on the copy
        assert _number(after("Elements in the model")) == before["archimate_elements"] - 1
        assert _number(after("Applications")) == before["application_components"] - 1
        assert _number(after("Links between elements")) < before["archimate_relationships"]
        assert _number(after("Yearly running cost of all applications")) < _number(now("Yearly running cost of all applications"))
        for label, row in rows.items():
            text = row.inner_text()
            assert "undefined" not in text and "NaN" not in text and "[object" not in text, (label, text)
        expect(panel.get_by_test_id("real-model-line")).to_contain_text("Your real model has not changed")

        # the page fits the screen: nothing runs off the right edge
        wide = page.evaluate("""() => {
            const scrolls = (e) => { for (let n = e.parentElement; n; n = n.parentElement) {
                if (['auto', 'scroll'].includes(getComputedStyle(n).overflowX)) return true; } return false; };
            return Array.from(document.querySelectorAll('main *'))
                .filter(e => e.getBoundingClientRect().right > window.innerWidth + 1 && !scrolls(e))
                .slice(0, 5).map(e => e.tagName + '.' + String(e.className).slice(0, 60) + ' ' + Math.round(e.getBoundingClientRect().right));
        }""")
        assert not wide, "runs off the right edge at %d wide: %s" % (viewport["width"], wide)

        # the real model is untouched, and a simulation leaves nothing in the analysis history
        assert _model_counts(app) == before
        _shot(page, "simulations-result", viewport, scheme)
        if WALK:
            panel.screenshot(path=os.path.join(WALK, "simulations-copy-panel-%d-%s.png" % (viewport["width"], scheme)))

        # a risk in the result opens that risk
        link = panel.get_by_test_id("simulation-risks").get_by_role("link").first
        title = link.inner_text().strip()
        link.click()
        page.wait_for_url(re.compile(r"/risks/\d+"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(title).first).to_be_visible()
        _shot(page, "simulations-risk-landing", viewport, scheme)
    finally:
        context.close()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_a_scenario_is_named_in_a_dialog_saved_and_two_are_compared_after_a_reload(browser, live_server, demo, viewport, scheme):
    context, page = _sign_in(browser, live_server, viewport, scheme)
    try:
        page.goto(live_server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.evaluate("localStorage.removeItem('impactAnalysisScenarios')")
        for change, name in (("Decommission / Retirement", "Retire the ledger"), ("Full Replacement", "Replace the ledger")):
            page.goto(live_server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            _pick(page, change=change)
            page.get_by_role("button", name="Run simulation").click()
            expect(page.get_by_test_id("simulation-panel")).to_be_visible(timeout=PAGE_TIMEOUT)

            # announced by its full name, and naming the scenario opens a dialog, not a browser prompt
            save = page.get_by_role("button", name="Save Scenario for Comparison", exact=True)
            expect(save).to_be_visible()
            save.click()
            dialog = page.get_by_role("dialog", name="Save scenario")
            expect(dialog).to_be_visible()
            field = dialog.get_by_label("Scenario name")
            expect(field).to_be_focused()
            if name.startswith("Retire"):
                _shot(page, "simulations-save-dialog", viewport, scheme)
                field.fill("")
                dialog.get_by_role("button", name="Save scenario").click()
                expect(dialog.get_by_text("Give the scenario a name")).to_be_visible()
            field.fill(name)
            dialog.get_by_role("button", name="Save scenario").click()
            expect(dialog).to_be_hidden()

        # after a reload both are still there, side by side
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("button", name="Compare Scenarios").click()
        compare = page.get_by_role("dialog", name="Compare Impact Scenarios")
        expect(compare).to_be_visible()
        for name in ("Retire the ledger", "Replace the ledger"):
            expect(compare.get_by_text(name).first).to_be_visible()
            compare.get_by_text(name).first.click()
        expect(compare.get_by_text("Decommission / Retirement").first).to_be_visible()
        expect(compare.get_by_text("Full Replacement").first).to_be_visible()
        _shot(page, "simulations-compare", viewport, scheme)
    finally:
        context.close()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_the_impact_report_uses_the_chosen_words_and_explains_every_cell(browser, live_server, demo, viewport, scheme):
    context, page = _sign_in(browser, live_server, viewport, scheme)
    try:
        page.goto(live_server + "/strategic/impact-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _pick(page, change="Minor Modification")
        page.get_by_role("button", name="Analyze Impact").click()
        expect(page.get_by_test_id("tco-at-risk")).to_be_visible(timeout=PAGE_TIMEOUT)

        report = page.locator("body").inner_text()
        assert "Impact of Minor Modification on %s" % ELEMENT in report
        assert "The change (Minor Modification) will impact" in report
        assert "Impact of MODIFY" not in report and "MODIFY will impact" not in report

        # the cost at risk is a figure from the cost store
        tco = page.get_by_test_id("tco-at-risk").inner_text().strip()
        assert re.fullmatch(r"\$\d+k", tco), tco
        expect(page.get_by_test_id("tco-at-risk-reason")).to_contain_text("Yearly running cost")

        # every dependency row says something in its application, owner and health cells
        table_rows = page.locator("table.dense-table tbody tr").filter(has=page.locator("td:nth-child(6)"))
        assert table_rows.count() > 0
        for i in range(table_rows.count()):
            cells = [table_rows.nth(i).locator("td").nth(n).inner_text().strip() for n in (2, 3, 4)]
            assert all(cell and cell not in ("—", "-", "0", "N/A") for cell in cells), cells
        _shot(page, "impact-report", viewport, scheme)
    finally:
        context.close()


def test_an_element_with_nothing_attached_says_why_there_is_no_cost_and_no_risk(browser, live_server, seeded):
    from app import create_app, db
    from app.models import ArchiMateElement

    from .fresh_org import create_fresh_org, sign_in

    mine = create_fresh_org("enterprise_architect")
    with create_app("testing").app_context():
        db.session.add(ArchiMateElement(name="Lone Capability", type="Capability", layer="strategy",
                                        organization_id=mine["org_id"]))
        db.session.commit()
        db.session.remove()
    context = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, mine["emails"]["enterprise_architect"])
        page.goto(live_server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _pick(page, name="Lone Capability")
        page.get_by_role("button", name="Run simulation").click()
        panel = page.get_by_test_id("simulation-panel")
        expect(panel).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("tco-at-risk")).to_have_text("Not worked out")
        expect(page.get_by_test_id("tco-at-risk-reason")).to_contain_text("no running cost is at risk")
        expect(panel.get_by_test_id("simulation-risks")).to_contain_text("No open risk is recorded")
        expect(panel.get_by_test_id("real-model-line")).to_contain_text("Your real model has not changed")
        expect(page.get_by_text("No direct dependencies found.")).to_be_visible()
        _shot(page, "simulations-empty-case", {"width": 1440}, "light")
    finally:
        context.close()


def test_a_visitor_who_is_not_signed_in_still_gets_the_waiting_list(browser, live_server, demo):
    context = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
    page = context.new_page()
    try:
        page.goto(live_server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("early-access-page")).to_be_visible()
        assert page.get_by_test_id("simulation-panel").count() == 0
        assert ELEMENT not in page.locator("body").inner_text()
    finally:
        context.close()
