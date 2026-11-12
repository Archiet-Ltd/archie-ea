"""A signed-in architect reaches the simulation screen, and a scenario they save is kept for the organisation.

In the Lantern Quay Systems demonstration organisation, with the first three waves of the product released:
- as the architect, opens /simulations and gets the simulation screen, not the waiting list; the service
  status page then lists Scenarios as available and that link lands on the same screen, and a visitor to
  the same page finds it in the footer's product column, not under Coming soon, and is sent to sign in
  when opening /simulations, then lands on the screen after signing in;
- clicks Scenarios in the sidebar under Changes and lands on the screen;
- names a scenario in the save dialog (the button is announced by its full name), saves it, and a colleague
  of the same organisation, signing in on another browser, finds it under Compare Scenarios with the
  words the architect picked; a person in another organisation finds the list empty and told why.
"""
import json
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server
from .fresh_org import create_fresh_org, sign_in
from .test_simulation_journey import DEMO_EMAIL, ELEMENT, VIEWPORT_IDS, VIEWPORTS, _cost_reader, _member, _pick, demo  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

COLLEAGUE_EMAIL = "second.architect@lantern-quay.example.com"
SCENARIO = "Retire the ledger for the shared review"


@pytest.fixture(scope="module")
def server(request, app):
    return boot_live_server(request, None, app, extra_env={"RELEASE_WAVE": "3"})


def _shot(page, name, viewport, scheme):
    walk = os.environ.get("SMOKE_WALK_DIR")
    if walk:
        os.makedirs(walk, exist_ok=True)
        page.screenshot(path=os.path.join(walk, "%s-%d-%s.png" % (name, viewport["width"], scheme)), full_page=True)


def _context(browser, viewport, scheme):
    context = browser.new_context(viewport=viewport, color_scheme=scheme, ignore_https_errors=True)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context


def _sign_in_as(context, server, email):
    page = context.new_page()
    page.on("dialog", lambda dialog: pytest.fail("a browser dialog opened: " + dialog.message))
    page.goto(server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    return page


def _screen_count(page, label):
    """The figure in the screen's tile under *label* (Capabilities, Processes)."""
    return page.get_by_text(label, exact=True).locator("xpath=preceding-sibling::span[1]").first.inner_text().strip()


def _compare_count(dialog, label):
    """The figure in the comparison table's row *label* for the one scenario picked."""
    return dialog.get_by_role("row").filter(has_text=label).get_by_role("cell").nth(1).inner_text().strip().split("\n")[0]


def _inside_viewport(page, locator, viewport):
    box = locator.bounding_box()
    assert box is not None, "not rendered"
    assert box["x"] >= 0 and box["x"] + box["width"] <= viewport["width"] + 1, box
    assert page.evaluate("document.documentElement.scrollWidth") <= viewport["width"] + 1


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_the_status_page_lists_scenarios_as_available_and_the_link_opens_the_screen(browser, server, demo, viewport, scheme):
    context = _context(browser, viewport, scheme)
    try:
        page = _sign_in_as(context, server, DEMO_EMAIL)
        page.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("early-access-page").count() == 0, "the waiting list is showing to a signed-in person"
        assert page.get_by_label("Email").count() == 0 and page.get_by_test_id("early-access-submit").count() == 0
        expect(page.get_by_role("heading", level=1)).to_have_text("Scenarios")
        _shot(page, "simulations-screen", viewport, scheme)

        page.goto(server + "/status", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        footer = page.locator("footer")
        soon = footer.locator('[data-footer-column="coming-soon"]')
        assert soon.get_by_text("Scenarios").count() == 0, "Scenarios is still listed as coming soon to a signed-in person"
        link = footer.locator('[data-footer-column="product"]').get_by_role("link", name=re.compile("^Scenarios"))
        expect(link).to_have_attribute("href", "/simulations")
        link.scroll_into_view_if_needed()
        _inside_viewport(page, link, viewport)
        _shot(page, "status-scenarios", viewport, scheme)
        link.click()
        page.wait_for_url(re.compile(r"/simulations$"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", level=1)).to_have_text("Scenarios")
        expect(page.get_by_role("button", name="Run scenario")).to_be_visible()
    finally:
        context.close()

    visitor = _context(browser, viewport, scheme)
    try:
        page = visitor.new_page()
        page.goto(server + "/status", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        soon = page.locator('footer [data-footer-column="coming-soon"]')
        assert soon.get_by_text("Scenarios").count() == 0, "a released Scenarios is under Coming soon for a visitor"
        product = page.locator('footer [data-footer-column="product"]')
        expect(product.get_by_role("link", name=re.compile("^Scenarios"))).to_be_visible()
        page.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("early-access-page").count() == 0, "a visitor is offered a waiting list for a released screen"
        expect(page).to_have_url(re.compile(r"/account/login\?next=(%2F|/)simulations$"))
        page.fill("#email", DEMO_EMAIL)
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(re.compile(r"/simulations$"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", level=1)).to_have_text("Scenarios")
        expect(page.get_by_role("button", name="Run scenario")).to_be_visible()
        page.goto(server + "/early-access", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", level=1)).to_be_visible()
        assert "Simulation scenarios" not in page.locator("main").inner_text()
    finally:
        visitor.close()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_a_signed_in_person_opens_scenarios_from_the_sidebar(browser, server, demo, viewport, scheme):
    context = _context(browser, viewport, scheme)
    try:
        page = _sign_in_as(context, server, DEMO_EMAIL)
        page.goto(server + "/dashboard/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        if viewport["width"] < 768:
            drawer = page.get_by_role("dialog", name="Navigation")
            if not drawer.is_visible():
                page.get_by_test_id("sidebar-trigger").click()
            expect(drawer).to_be_visible(timeout=PAGE_TIMEOUT)
        changes = page.locator('[data-nav-key="changes"]').first
        expect(changes).to_be_visible(timeout=PAGE_TIMEOUT)
        if changes.get_attribute("aria-expanded") == "false":
            changes.click()
        link = page.locator('[data-nav-key="scenarios"]').first
        expect(link).to_be_visible()
        expect(link).to_have_text(re.compile("Scenarios"))
        _shot(page, "sidebar-scenarios", viewport, scheme)
        link.click()
        page.wait_for_url(re.compile(r"/simulations$"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", level=1)).to_have_text("Scenarios")
        expect(page.get_by_role("button", name="Run scenario")).to_be_visible()
    finally:
        context.close()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_a_saved_scenario_is_listed_for_a_colleague_and_not_for_another_organisation(browser, server, app, demo, viewport, scheme):
    from app import db
    from app.models.saved_scenario import SavedScenario
    from app.models.user import User

    with app.app_context():
        from app.models.user import Role

        org_id = db.session.execute(db.text("select id from organizations where slug = 'lantern-quay'")).scalar_one()
        colleague = User.query.filter_by(email=COLLEAGUE_EMAIL).first()
        if colleague is None:
            colleague = User(email=COLLEAGUE_EMAIL, first_name="Second", last_name="Architect", organization_id=org_id,
                             confirmed=True, enterprise_role="cto")
            colleague.role = Role.query.filter_by(name="Architect").one()
            db.session.add(colleague)
        # both people may read running costs, so the dialog's figure can be held to the run card's
        colleague.enterprise_role = "cto"
        colleague.password = PASSWORD
        db.session.commit()
        db.session.remove()

    architect_ctx = _context(browser, viewport, scheme)
    try:
        page = _sign_in_as(architect_ctx, server, _cost_reader(app))
        page.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _pick(page, change="Decommission / Retirement")
        page.get_by_role("button", name="Run scenario").click()
        expect(page.get_by_test_id("simulation-panel")).to_be_visible(timeout=PAGE_TIMEOUT)
        cost_at_risk = page.get_by_test_id("tco-at-risk").inner_text().strip()
        on_screen = {"capabilities": _screen_count(page, "Capabilities"), "processes": _screen_count(page, "Processes")}
        assert re.fullmatch(r"[1-9]\d*", on_screen["capabilities"]), on_screen
        # a Processes figure of 0 says why, from the count of everything the change reached
        assert on_screen["processes"] == "0", on_screen
        reason = page.get_by_test_id("processes-reason")
        expect(reason).to_have_text(re.compile(r"None of the [1-9]\d* elements? reached is a business process\."))
        reason_on_screen = reason.inner_text().strip()
        _shot(page, "simulation-processes-reason", viewport, scheme)
        page.get_by_role("button", name="Save Scenario for Comparison", exact=True).click()
        dialog = page.get_by_role("dialog", name="Save scenario")
        dialog.get_by_label("Scenario name").fill(SCENARIO)
        dialog.get_by_role("button", name="Save scenario").click()
        expect(dialog).to_be_hidden()

        with app.app_context():
            rows = SavedScenario.query.filter_by(name=SCENARIO).all()
            org_id = db.session.execute(db.text("select id from organizations where slug = 'lantern-quay'")).scalar_one()
            assert [(r.organization_id, r.element_name, r.change_label) for r in rows] == \
                [(org_id, ELEMENT, "Decommission / Retirement")]
            db.session.remove()

        # a colleague signing in on another browser finds it, with the words the architect picked
        colleague_ctx = _context(browser, viewport, scheme)
        try:
            colleague = _sign_in_as(colleague_ctx, server, COLLEAGUE_EMAIL)
            colleague.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            colleague.get_by_role("button", name="Compare Scenarios").click()
            compare = colleague.get_by_role("dialog", name="Compare Impact Scenarios")
            expect(compare).to_be_visible()
            expect(compare.get_by_text(SCENARIO)).to_be_visible()
            compare.get_by_text(SCENARIO).click()
            expect(compare.get_by_text("Decommission / Retirement").first).to_be_visible()
            expect(compare.get_by_text(ELEMENT).first).to_be_visible()
            # the figure reads the same in the dialog as on the architect's screen, in the same currency
            expect(compare.get_by_role("cell", name=cost_at_risk, exact=True)).to_be_visible()
            # the same element, the same counts: what the architect saw is what the colleague compares
            assert _compare_count(compare, "Affected Capabilities") == on_screen["capabilities"]
            assert _compare_count(compare, "Affected Processes") == on_screen["processes"]
            expect(compare.get_by_test_id("compare-processes-reason")).to_have_text(reason_on_screen)
            _shot(colleague, "colleague-compare", viewport, scheme)
        finally:
            colleague_ctx.close()

        # a person in another organisation finds none of it, and is told why
        other = create_fresh_org("enterprise_architect")
        other_ctx = _context(browser, viewport, scheme)
        try:
            outsider = other_ctx.new_page()
            sign_in(outsider, server, other["emails"]["enterprise_architect"])
            outsider.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            outsider.get_by_role("button", name="Compare Scenarios").click()
            compare = outsider.get_by_role("dialog", name="Compare Impact Scenarios")
            expect(compare.get_by_text("No Saved Scenarios")).to_be_visible()
            assert compare.get_by_text(SCENARIO).count() == 0
            _shot(outsider, "other-organisation-compare", viewport, scheme)
        finally:
            other_ctx.close()
    finally:
        architect_ctx.close()
        with app.app_context():
            SavedScenario.query.filter_by(name=SCENARIO).delete()
            db.session.commit()
            db.session.remove()


def _element_id(app):
    from app import db

    with app.app_context():
        found = db.session.execute(db.text(
            "select id from archimate_elements where name = :n and organization_id = "
            "(select id from organizations where slug = 'lantern-quay') order by id limit 1"), {"n": ELEMENT}).scalar_one()
        db.session.remove()
    return found


def _compare(page, server):
    page.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Compare Scenarios").click()
    return page.get_by_role("dialog", name="Compare Impact Scenarios")


LEGACY = "Saved in this browser last quarter"


def _legacy_storage(element_id):
    return """localStorage.setItem('impactAnalysisScenarios', JSON.stringify([{id: 'scenario-1', name: %r,
        element: {id: %d, name: %r}, change_type: 'RETIRE', change_label: 'Decommission / Retirement',
        analysis_result: {severity: 'LOW', estimated_risk_cost: 1}, created_at: '2026-01-01T00:00:00Z'}]));""" % (
        LEGACY, element_id, ELEMENT)


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_a_data_architect_is_not_offered_save_and_still_compares_what_an_architect_saved(browser, server, app, demo, viewport, scheme):
    from app import db
    from app.models.saved_scenario import SavedScenario

    email = "data.architect@lantern-quay.example.com"
    _member(app, email, "data_architect")
    context = _context(browser, viewport, scheme)
    try:
        # an architect saves one scenario through the screen
        architect = _sign_in_as(context, server, DEMO_EMAIL)
        architect.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _pick(architect, change="Decommission / Retirement")
        architect.get_by_role("button", name="Run scenario").click()
        expect(architect.get_by_test_id("simulation-panel")).to_be_visible(timeout=PAGE_TIMEOUT)
        architect.get_by_role("button", name="Save Scenario for Comparison", exact=True).click()
        dialog = architect.get_by_role("dialog", name="Save scenario")
        dialog.get_by_label("Scenario name").fill(SCENARIO + " seen by data")
        dialog.get_by_role("button", name="Save scenario").click()
        expect(dialog).to_be_hidden()

        other_ctx = _context(browser, viewport, scheme)
        try:
            person = _sign_in_as(other_ctx, server, email)
            person.goto(server + "/simulations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            _pick(person, change="Decommission / Retirement")
            person.get_by_role("button", name="Run scenario").click()
            expect(person.get_by_test_id("simulation-panel")).to_be_visible(timeout=PAGE_TIMEOUT)
            # no control that would only refuse them, and the page says who can save
            assert person.get_by_role("button", name="Save Scenario for Comparison").count() == 0
            note = person.get_by_test_id("save-scenario-not-allowed")
            expect(note).to_contain_text(
                "Only solution architects, enterprise architects, ARB members, portfolio managers, "
                "organisation administrators and platform administrators can save a scenario")
            _inside_viewport(person, note, viewport)
            _shot(person, "data-architect-no-save", viewport, scheme)
            # what the architect saved is theirs to compare, with real counts
            compare = person.get_by_role("button", name="Compare Scenarios")
            compare.click()
            dialog = person.get_by_role("dialog", name="Compare Impact Scenarios")
            expect(dialog.get_by_text(SCENARIO + " seen by data")).to_be_visible()
            dialog.get_by_text(SCENARIO + " seen by data").click()
            expect(dialog.get_by_text(ELEMENT).first).to_be_visible()
            expect(dialog.get_by_role("row").filter(has_text="Affected Capabilities").get_by_role("cell").nth(1)).to_have_text(re.compile(r"^\d+$"))
        finally:
            other_ctx.close()
    finally:
        context.close()
        with app.app_context():
            SavedScenario.query.filter(SavedScenario.name.like(SCENARIO + "%")).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_the_empty_compare_dialog_offers_saving_only_to_a_person_who_may_save(browser, server, app, demo, viewport, scheme):
    from app import db
    from app.models.saved_scenario import SavedScenario
    from app.commands import seed_demo_company as seed_demo
    from app.models.user import User

    email = "data.architect@lantern-quay.example.com"
    _member(app, email, "data_architect")
    with app.app_context():
        org_id = User.query.filter_by(email=DEMO_EMAIL).one().organization_id
        SavedScenario.query.filter(SavedScenario.organization_id == org_id, SavedScenario.name.like(SCENARIO + "%")).delete(synchronize_session=False)
        # the demonstration keeps two scenarios to compare: they are taken out by name for this run and put back after
        SavedScenario.query.filter(SavedScenario.organization_id == org_id, SavedScenario.name.in_(
            [name for name, _system, _change in seed_demo._SCENARIOS])).delete(synchronize_session=False)
        has_others = SavedScenario.query.filter(SavedScenario.organization_id == org_id).count()
        db.session.commit()
        db.session.remove()
    try:
        assert has_others == 0, "the demonstration organisation must hold no saved scenario for its dialog to be empty"
        for who, may_save in ((DEMO_EMAIL, True), (email, False)):
            context = _context(browser, viewport, scheme)
            try:
                page = _sign_in_as(context, server, who)
                dialog = _compare(page, server)
                step = dialog.get_by_test_id("empty-next-step")
                expect(step).to_be_visible()
                if may_save:
                    expect(step).to_have_text("Run an analysis and save it to compare scenarios")
                else:
                    expect(step).to_contain_text("Ask ")
                    expect(step).to_contain_text("solution architects")
                    assert "Run an analysis and save it to compare" not in step.inner_text()
                _inside_viewport(page, step, viewport)
                _shot(page, "empty-compare-%s" % ("architect" if may_save else "data-architect"), viewport, scheme)
            finally:
                context.close()
    finally:
        with app.app_context():
            seed_demo._seed_scenarios(org_id, User.query.filter_by(email=DEMO_EMAIL).one().id)
            db.session.commit()
            db.session.remove()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_scenarios_saved_earlier_in_a_browser_move_across_for_an_architect_and_are_named_for_anyone_else(browser, server, app, demo, viewport, scheme):
    from app import db
    from app.models.saved_scenario import SavedScenario

    element_id = _element_id(app)
    email = "data.architect@lantern-quay.example.com"
    _member(app, email, "data_architect")

    # someone who may not save is told, and the scenario stays where it is
    context = _context(browser, viewport, scheme)
    context.add_init_script(_legacy_storage(element_id))
    try:
        page = _sign_in_as(context, server, email)
        compare = _compare(page, server)
        expect(compare.get_by_test_id("carried-notice")).to_contain_text("1 scenario was saved in this browser before scenarios were shared")
        assert compare.get_by_text(LEGACY).count() == 0
        assert page.evaluate("localStorage.getItem('impactAnalysisScenarios')") is not None
        _shot(page, "legacy-notice", viewport, scheme)
    finally:
        context.close()

    # an architect's visit moves it into the organisation, and a colleague then lists it
    context = _context(browser, viewport, scheme)
    context.add_init_script(_legacy_storage(element_id))
    try:
        page = _sign_in_as(context, server, DEMO_EMAIL)
        compare = _compare(page, server)
        expect(compare.get_by_test_id("carried-notice")).to_contain_text("1 scenario saved earlier in this browser was moved to your organisation")
        expect(compare.get_by_text(LEGACY)).to_be_visible()
        assert page.evaluate("localStorage.getItem('impactAnalysisScenarios')") is None
        with app.app_context():
            rows = SavedScenario.query.filter_by(name=LEGACY).all()
            assert len(rows) == 1 and rows[0].element_name == ELEMENT
            # the figures kept are the server's own, not the 'LOW' and 1 the old browser held
            assert rows[0].analysis_result["estimated_risk_cost"] != 1
            db.session.remove()
        _shot(page, "legacy-moved", viewport, scheme)
    finally:
        context.close()
        with app.app_context():
            SavedScenario.query.filter_by(name=LEGACY).delete()
            db.session.commit()
            db.session.remove()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_carried_scenarios_leave_browser_storage_one_by_one_and_one_that_fails_can_be_dismissed(browser, server, app, demo, viewport, scheme):
    from app import db
    from app.models.saved_scenario import SavedScenario

    element_id = _element_id(app)
    names = (LEGACY + " first", LEGACY + " second")
    storage = """localStorage.setItem('impactAnalysisScenarios', JSON.stringify([
        {id: 's1', name: %r, element: {id: %d, name: %r}, change_type: 'RETIRE', analysis_result: {}},
        {id: 's2', name: %r, element: {id: %d, name: %r}, change_type: 'RETIRE', analysis_result: {}},
        {id: 's3', name: 'Points at nothing', element: {id: 2000000000, name: 'Gone'}, change_type: 'RETIRE', analysis_result: {}}]));""" % (
        names[0], element_id, ELEMENT, names[1], element_id, ELEMENT)
    context = _context(browser, viewport, scheme)
    context.add_init_script("if (!sessionStorage.getItem('seeded')) { sessionStorage.setItem('seeded', '1'); %s }" % storage)
    try:
        page = _sign_in_as(context, server, DEMO_EMAIL)
        left_when_second_was_sent = []

        def watch(route):
            if route.request.method == "POST":
                left_when_second_was_sent.append(page.evaluate(
                    "JSON.parse(localStorage.getItem('impactAnalysisScenarios') || '[]').length"))
            route.continue_()

        page.route("**/strategic/api/scenarios", watch)
        compare = _compare(page, server)
        notice = compare.get_by_test_id("carried-notice")
        expect(notice).to_contain_text("2 scenarios saved earlier in this browser were moved to your organisation")
        expect(notice).to_contain_text("1 could not be moved")
        # each one left storage as soon as it was saved: 3, then 2, then 1 were still held when the next was sent
        assert left_when_second_was_sent[:3] == [3, 2, 1], left_when_second_was_sent
        kept = page.evaluate("JSON.parse(localStorage.getItem('impactAnalysisScenarios')).map(s => s.name)")
        assert kept == ["Points at nothing"]
        _shot(page, "carried-failed", viewport, scheme)
        # the one whose element is not found stays until dismissed (each opening of the dialog would try it again);
        # dismissing it empties storage and the notice goes
        expect(notice.get_by_test_id("dismiss-unmoved")).to_have_attribute("data-slot", "button")
        notice.get_by_role("button", name="Dismiss").click()
        expect(notice).to_be_hidden()
        assert page.evaluate("localStorage.getItem('impactAnalysisScenarios')") is None
        with app.app_context():
            assert SavedScenario.query.filter(SavedScenario.name.in_(names)).count() == 2
            db.session.remove()
    finally:
        context.close()
        with app.app_context():
            SavedScenario.query.filter(SavedScenario.name.in_(names)).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_while_carried_scenarios_are_still_moving_the_notice_already_says_how_many_have_moved(browser, server, app, demo, viewport, scheme):
    from app import db
    from app.models.saved_scenario import SavedScenario

    element_id = _element_id(app)
    names = (LEGACY + " moved early", LEGACY + " moved next")
    storage = """localStorage.setItem('impactAnalysisScenarios', JSON.stringify([
        {id: 's1', name: %r, element: {id: %d, name: %r}, change_type: 'RETIRE', analysis_result: {}},
        {id: 's2', name: %r, element: {id: %d, name: %r}, change_type: 'RETIRE', analysis_result: {}},
        {id: 's3', name: 'Points at nothing', element: {id: 2000000000, name: 'Gone'}, change_type: 'RETIRE', analysis_result: {}}]));""" % (
        names[0], element_id, ELEMENT, names[1], element_id, ELEMENT)
    context = _context(browser, viewport, scheme)
    context.add_init_script("if (!sessionStorage.getItem('seeded')) { sessionStorage.setItem('seeded', '1'); %s }" % storage)
    try:
        page = _sign_in_as(context, server, DEMO_EMAIL)
        held = []

        def hold_the_last(route):
            # the server is slow to answer for the last one: the person is still waiting for it
            if route.request.method == "POST" and json.loads(route.request.post_data)["element"]["id"] == 2000000000:
                held.append(route)
            else:
                route.continue_()

        page.route("**/strategic/api/scenarios", hold_the_last)
        compare = _compare(page, server)
        notice = compare.get_by_test_id("carried-notice")
        # before the last answer arrives the person already reads how many have moved
        expect(notice).to_contain_text("2 scenarios saved earlier in this browser were moved to your organisation")
        assert len(held) == 1
        assert page.evaluate("JSON.parse(localStorage.getItem('impactAnalysisScenarios')).map(s => s.name)") == ["Points at nothing"]
        _shot(page, "carried-moving", viewport, scheme)
        held[0].continue_()
        expect(notice).to_contain_text("1 could not be moved (its element may no longer exist)")
        expect(notice).to_contain_text("2 scenarios saved earlier in this browser were moved to your organisation")
        expect(compare.get_by_text(names[0])).to_be_visible()
        expect(compare.get_by_text(names[1])).to_be_visible()
    finally:
        context.close()
        with app.app_context():
            SavedScenario.query.filter(SavedScenario.name.in_(names)).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_a_scenario_the_server_could_not_check_stays_in_the_browser_and_is_moved_on_trying_again(browser, server, app, demo, viewport, scheme):
    from app import db
    from app.models.saved_scenario import SavedScenario

    name = LEGACY + " held back"
    storage = """localStorage.setItem('impactAnalysisScenarios', JSON.stringify([
        {id: 's1', name: %r, element: {id: %d, name: %r}, change_type: 'RETIRE', analysis_result: {}},
        {id: 's2', name: 'Points at nothing', element: {id: 2000000000, name: 'Gone'}, change_type: 'RETIRE', analysis_result: {}}]));""" % (
        name, _element_id(app), ELEMENT)
    context = _context(browser, viewport, scheme)
    context.add_init_script("if (!sessionStorage.getItem('seeded')) { sessionStorage.setItem('seeded', '1'); %s }" % storage)
    try:
        page = _sign_in_as(context, server, DEMO_EMAIL)

        def fail_for_the_real_element(route):
            if route.request.method == "POST" and json.loads(route.request.post_data)["element"]["id"] != 2000000000:
                route.fulfill(status=503, content_type="application/json", body='{"error": "unavailable"}')
            else:
                route.continue_()

        page.route("**/strategic/api/scenarios", fail_for_the_real_element)
        compare = _compare(page, server)
        notice = compare.get_by_test_id("carried-notice")
        expect(notice).to_contain_text("1 scenario could not be checked right now and is still in this browser")
        expect(notice).to_contain_text("1 could not be moved (its element may no longer exist)")
        assert page.evaluate("JSON.parse(localStorage.getItem('impactAnalysisScenarios')).map(s => s.name).sort()") == sorted([name, "Points at nothing"])
        _shot(page, "carried-unchecked", viewport, scheme)

        # Dismiss clears only the one whose element is not found; the one not yet checked stays
        notice.get_by_role("button", name="Dismiss").click()
        assert page.evaluate("JSON.parse(localStorage.getItem('impactAnalysisScenarios')).map(s => s.name)") == [name]
        expect(notice).to_contain_text("could not be checked right now")
        expect(notice.get_by_role("button", name="Dismiss")).to_be_hidden()

        # the server answers again: Try again moves it and it is listed for the organisation
        page.unroute("**/strategic/api/scenarios")
        notice.get_by_role("button", name="Try again").click()
        expect(notice).to_contain_text("1 scenario saved earlier in this browser was moved to your organisation")
        assert page.evaluate("localStorage.getItem('impactAnalysisScenarios')") is None
        expect(compare.get_by_text(name)).to_be_visible()
        with app.app_context():
            assert SavedScenario.query.filter_by(name=name).count() == 1
            db.session.remove()
    finally:
        context.close()
        with app.app_context():
            SavedScenario.query.filter_by(name=name).delete()
            db.session.commit()
            db.session.remove()


@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=VIEWPORT_IDS)
def test_a_carried_scenario_the_server_refuses_is_dismissed_and_does_not_come_back(browser, server, app, demo, viewport, scheme):
    from app.models.saved_scenario import SavedScenario

    long_name = "L" * 201
    storage = """localStorage.setItem('impactAnalysisScenarios', JSON.stringify([
        {id: 's1', name: %r, element: {id: %d, name: %r}, change_type: 'RETIRE', analysis_result: {}}]));""" % (
        long_name, _element_id(app), ELEMENT)
    context = _context(browser, viewport, scheme)
    context.add_init_script("if (!sessionStorage.getItem('seeded')) { sessionStorage.setItem('seeded', '1'); %s }" % storage)
    try:
        page = _sign_in_as(context, server, DEMO_EMAIL)
        compare = _compare(page, server)
        notice = compare.get_by_test_id("carried-notice")
        expect(notice).to_contain_text("1 was refused by the server and cannot be moved as it is")
        expect(notice).not_to_contain_text("could not be checked right now")
        expect(notice.get_by_role("button", name="Try again")).to_be_hidden()
        _shot(page, "carried-refused", viewport, scheme)
        notice.get_by_role("button", name="Dismiss").click()
        assert page.evaluate("localStorage.getItem('impactAnalysisScenarios')") is None
        expect(notice).to_be_hidden()
        # opening the dialog again does not bring it back
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("button", name="Compare Scenarios").click()
        expect(page.get_by_role("dialog", name="Compare Impact Scenarios").get_by_test_id("carried-notice")).to_be_hidden()
        with app.app_context():
            assert SavedScenario.query.filter_by(name=long_name).count() == 0
    finally:
        context.close()


def test_a_full_scenario_list_says_it_shows_only_the_most_recent(browser, server, app, demo):
    from app import db
    from app.models.saved_scenario import SavedScenario

    prefix = "Capped list scenario "
    with app.app_context():
        org_id = db.session.execute(db.text("select id from organizations where slug = 'lantern-quay'")).scalar_one()
        db.session.add_all([SavedScenario(name="%s%03d" % (prefix, i), change_type="RETIRE", organization_id=org_id,
                                          analysis_result={"severity": "LOW"}) for i in range(200)])
        db.session.commit()
        db.session.remove()
    context = _context(browser, VIEWPORTS[0][0], VIEWPORTS[0][1])
    try:
        page = _sign_in_as(context, server, DEMO_EMAIL)
        compare = _compare(page, server)
        capped = compare.get_by_test_id("scenarios-capped")
        expect(capped).to_be_visible()
        expect(capped).to_have_text("Showing your 200 most recent scenarios. Older ones are not listed.")
        # the sentence sits above the list it describes
        assert capped.bounding_box()["y"] < compare.get_by_text(prefix + "199").first.bounding_box()["y"]
        _shot(page, "scenarios-capped", VIEWPORTS[0][0], VIEWPORTS[0][1])
    finally:
        context.close()
        with app.app_context():
            SavedScenario.query.filter(SavedScenario.name.like(prefix + "%")).delete(synchronize_session=False)
            db.session.commit()
            db.session.remove()
        # with the cap gone, a short list carries no such sentence
    context = _context(browser, VIEWPORTS[0][0], VIEWPORTS[0][1])
    try:
        page = _sign_in_as(context, server, DEMO_EMAIL)
        compare = _compare(page, server)
        expect(compare.get_by_test_id("scenarios-capped")).to_be_hidden()
    finally:
        context.close()
