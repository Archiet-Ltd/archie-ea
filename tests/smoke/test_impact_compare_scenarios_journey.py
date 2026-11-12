"""An enterprise architect on Impact Analysis clicks Compare Scenarios and gets the
comparison dialog with the scenarios their organisation has saved to pick from."""
import os

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000
WALK_DIR = os.environ.get("SMOKE_WALK_DIR")
VIEWPORTS = [
    ({"width": 1440, "height": 900}, "light"),
    ({"width": 390, "height": 844}, "dark"),
]
SAVED = [
    {"id": "s1", "name": "Retire the legacy billing system", "change_type": "retire",
     "element": {"name": "Legacy Billing"}, "analysis_result": {"severity": "HIGH"}},
    {"id": "s2", "name": "Move billing to the cloud", "change_type": "migrate",
     "element": {"name": "Billing Cloud"}, "analysis_result": {"severity": "LOW"}},
]


def _seed_scenarios(app, email, saved):
    """Keep exactly these scenarios for the (test) organisation of this person, replacing any earlier ones."""
    from app import db
    from app.models.saved_scenario import SavedScenario
    from app.models.user import User

    with app.app_context():
        org_id = User.query.filter_by(email=email).one().organization_id
        SavedScenario.query.filter(SavedScenario.organization_id == org_id).delete(synchronize_session=False)
        for item in saved:
            db.session.add(SavedScenario(
                organization_id=org_id, name=item["name"], change_type=item["change_type"],
                element_name=item["element"]["name"], analysis_result=item["analysis_result"]))
        db.session.commit()
        db.session.remove()


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=["1440-light", "390-dark"])
def test_compare_scenarios_opens_the_dialog_with_saved_scenarios(browser, live_server, app, seeded, viewport, scheme):
    _seed_scenarios(app, seeded["emails"]["enterprise_architect"], SAVED)
    ctx = browser.new_context(viewport=viewport, color_scheme=scheme, ignore_https_errors=True)
    page = ctx.new_page()
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(live_server + "/strategic/impact-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)

    trigger = page.get_by_role("button", name="Compare Scenarios")
    expect(trigger).to_be_visible()
    trigger.focus()
    trigger.click()

    dialog = page.get_by_role("dialog", name="Compare Impact Scenarios")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text("Retire the legacy billing system")).to_be_visible()
    expect(dialog.get_by_text("Move billing to the cloud")).to_be_visible()
    dialog.get_by_text("Retire the legacy billing system").click()
    expect(dialog.get_by_text("Legacy Billing").first).to_be_visible()
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path="%s/compare-dialog-%d-%s.png" % (WALK_DIR, viewport["width"], scheme))

    dialog.get_by_role("button", name="Close scenario comparison dialog").click()
    expect(dialog).to_be_hidden()
    expect(trigger).to_be_focused()
    ctx.close()


SCORED = {
    "id": "s3", "name": "Consolidate the billing databases", "change_type": "modify",
    "element": {"name": "Billing Database"},
    "analysis_result": {
        "severity": "MEDIUM",
        "impacted_capabilities": [{"name": "Invoicing"}, {"name": "Collections"}],
        "impacted_processes": [{"name": "Month end close"}],
        "estimated_risk_cost": 250000,
        "dependencies": [{"name": "Ledger"}, {"name": "Tax engine"}, {"name": "Payments"}],
    },
}


# the chief technology officer of the same organisation, whom the cost rule lets read cost figures: a journey
# that reads a financial risk, or a ranking worked out from one, opens the dialog as them
COST_READER = "cto"


def _open_dialog(browser, live_server, app, seeded, viewport, scheme, saved, persona="enterprise_architect"):
    _seed_scenarios(app, seeded["emails"][persona], saved)
    ctx = browser.new_context(viewport=viewport, color_scheme=scheme, ignore_https_errors=True,
                              accept_downloads=True)
    page = ctx.new_page()
    _login(page, live_server, seeded["emails"][persona])
    page.goto(live_server + "/strategic/impact-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)
    trigger = page.get_by_role("button", name="Compare Scenarios")
    trigger.focus()
    trigger.click()
    dialog = page.get_by_role("dialog", name="Compare Impact Scenarios")
    expect(dialog).to_be_visible()
    return ctx, page, trigger, dialog


def _compare(dialog, *names):
    for name in names:
        dialog.get_by_text(name, exact=True).first.click()


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=["1440-light", "390-dark"])
def test_missing_figures_are_stated_not_shown_as_zero(browser, live_server, app, seeded, viewport, scheme, tmp_path):
    ctx, page, trigger, dialog = _open_dialog(browser, live_server, app, seeded, viewport, scheme, SAVED + [SCORED],
                                              persona=COST_READER)
    _compare(dialog, "Retire the legacy billing system", "Consolidate the billing databases")
    table = dialog.locator("table")
    expect(table).to_be_visible()

    def row(label):
        return table.locator("tr", has=page.get_by_text(label, exact=True))

    # the unscored scenario says so; the scored one shows its real figures
    for label in ("Affected Capabilities", "Affected Processes", "Direct Dependencies"):
        expect(row(label)).to_contain_text("Not scored")
    expect(row("Financial Risk")).to_contain_text("Not recorded")
    expect(row("Affected Capabilities")).to_contain_text("2")
    expect(row("Affected Processes")).to_contain_text("1")
    expect(row("Direct Dependencies")).to_contain_text("3")
    cells = [t.strip() for t in row("Financial Risk").get_by_role("cell").all_inner_texts()]
    assert cells[-2:] == ["Not recorded", "£250,000.00"] or cells[-2:] == ["£250,000.00", "Not recorded"], cells
    expect(table).not_to_contain_text("£0.00")
    # a figure that was never scored is never drawn as a bare zero: no cell of the table reads just 0
    bare_zero = [t for t in table.get_by_role("cell").all_inner_texts() if t.strip() == "0"]
    assert bare_zero == [], bare_zero
    # the recommendation does not rank a scenario whose figures were never scored
    expect(dialog.get_by_text("Recommended Scenario")).to_be_hidden()
    expect(dialog.get_by_text("cannot be ranked")).to_be_visible()

    with page.expect_download() as dl:
        dialog.get_by_role("button", name="Export").click()
    path = tmp_path / "compare.csv"
    dl.value.save_as(str(path))
    csv = path.read_text()
    assert "Not recorded" in csv and "Not scored" in csv
    assert ",0" not in csv.replace("250000", "")
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path="%s/missing-figures-%d-%s.png" % (WALK_DIR, viewport["width"], scheme))
    ctx.close()


@pytest.mark.smoke
@pytest.mark.journey
def test_comparison_table_scrolls_inside_the_dialog_on_a_phone(browser, live_server, app, seeded):
    viewport = {"width": 390, "height": 844}
    ctx, page, trigger, dialog = _open_dialog(browser, live_server, app, seeded, viewport, "dark", SAVED + [SCORED])
    _compare(dialog, "Retire the legacy billing system", "Move billing to the cloud",
             "Consolidate the billing databases")
    table = dialog.locator("table")
    expect(table).to_be_visible()
    page.wait_for_timeout(400)
    footer_close = dialog.get_by_role("button", name="Close", exact=True)
    expect(footer_close).to_be_visible()
    box = dialog.bounding_box()
    f = footer_close.bounding_box()
    # the footer is inside the dialog and in the viewport, and the table does not reach below it
    assert f["y"] + f["height"] <= viewport["height"], f
    assert box["y"] + box["height"] <= viewport["height"] + 1, box
    scroller = table.locator("xpath=ancestor::div[contains(@class,'overflow-x-auto')][1]")
    frame = scroller.bounding_box()
    assert frame["x"] >= box["x"] - 1 and frame["x"] + frame["width"] <= box["x"] + box["width"] + 1, (frame, box)
    # the dialog body scrolls, so the footer stays on screen under it
    body = table.locator("xpath=ancestor::div[contains(@class,'overflow-y-auto')][1]")
    body_box = body.bounding_box()
    assert body_box["y"] + body_box["height"] <= f["y"] + 1, (body_box, f)
    assert body.evaluate("e => e.scrollHeight > e.clientHeight")
    # the table scrolls sideways inside its own frame instead of spilling out of the dialog
    assert scroller.evaluate("e => e.scrollWidth > e.clientWidth")
    # a metric label is read in full, not squeezed to a few letters
    heads = table.locator("th").evaluate_all(
        "els => els.map(e => { const r = e.getBoundingClientRect(); return [r.left, r.right]; })")
    for earlier, later in zip(heads, heads[1:]):
        assert later[0] >= earlier[1] - 1, ("a column sits over the one before it", heads)
    label = table.get_by_text("Affected Capabilities", exact=True).bounding_box()
    assert label["width"] >= 130 and label["height"] <= 56, label
    ctx.close()


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("how", ["backdrop", "escape"])
@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=["1440-light", "390-dark"])
def test_dialog_closes_from_outside_and_escape_and_returns_focus(browser, live_server, app, seeded, viewport, scheme, how):
    ctx, page, trigger, dialog = _open_dialog(browser, live_server, app, seeded, viewport, scheme, SAVED)
    if how == "backdrop":
        page.mouse.click(2, 2)
    else:
        page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(trigger).to_be_focused()
    ctx.close()


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=["1440-light", "390-dark"])
def test_no_saved_scenarios_says_why_and_what_to_do(browser, live_server, app, seeded, viewport, scheme):
    ctx, page, trigger, dialog = _open_dialog(browser, live_server, app, seeded, viewport, scheme, [])
    expect(dialog.get_by_text("No Saved Scenarios")).to_be_visible()
    expect(dialog.get_by_text("Run an analysis and save it to compare scenarios")).to_be_visible()
    expect(dialog.get_by_role("button", name="Export")).to_be_disabled()
    ctx.close()


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=["1440-light", "390-dark"])
def test_the_dialog_says_it_is_loading_until_the_saved_scenarios_arrive(browser, live_server, app, seeded, viewport, scheme):
    _seed_scenarios(app, seeded["emails"]["enterprise_architect"], SAVED)
    ctx = browser.new_context(viewport=viewport, color_scheme=scheme, ignore_https_errors=True)
    page = ctx.new_page()
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(live_server + "/strategic/impact-analysis", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)

    held = []

    def hold(route):
        held.append(route)

    page.route("**/strategic/api/scenarios", hold)
    page.get_by_role("button", name="Compare Scenarios").click()
    dialog = page.get_by_role("dialog", name="Compare Impact Scenarios")
    expect(dialog).to_be_visible()
    # while the list is on its way the person is told so, and is not shown the empty-list message
    expect(dialog.get_by_text("Loading the saved scenarios")).to_be_visible()
    expect(dialog.get_by_text("No Saved Scenarios")).to_be_hidden()
    page.wait_for_timeout(300)
    for route in held:
        route.continue_()
    expect(dialog.get_by_text("Retire the legacy billing system")).to_be_visible()
    expect(dialog.get_by_text("Loading the saved scenarios")).to_be_hidden()
    ctx.close()


def _scored(scenario_id, name, severity, cost):
    return {
        "id": scenario_id, "name": name, "change_type": "modify", "element": {"name": name + " element"},
        "analysis_result": {
            "severity": severity, "impacted_capabilities": [{"name": "Invoicing"}],
            "impacted_processes": [{"name": "Month end close"}], "estimated_risk_cost": cost,
            "dependencies": [{"name": "Ledger"}],
        },
    }


@pytest.mark.smoke
@pytest.mark.journey
@pytest.mark.parametrize("viewport,scheme", VIEWPORTS, ids=["1440-light", "390-dark"])
def test_two_equal_scenarios_are_named_as_a_tie_and_a_lower_one_is_named_alone(browser, live_server, app, seeded, viewport, scheme):
    tied = [_scored("t1", "Retire the ledger", "HIGH", 25200), _scored("t2", "Replace the ledger", "HIGH", 25200)]
    lower = _scored("t3", "Keep the ledger", "LOW", 1000)
    ctx, page, trigger, dialog = _open_dialog(browser, live_server, app, seeded, viewport, scheme, tied + [lower],
                                              persona=COST_READER)
    _compare(dialog, "Retire the ledger", "Replace the ledger")
    sentence = dialog.get_by_test_id("scenario-recommendation")
    expect(sentence).to_have_text(
        "Retire the ledger and Replace the ledger tie on the lowest combined risk score based on severity and financial impact.")
    assert "has the lowest" not in sentence.inner_text()
    heading = dialog.get_by_test_id("scenario-recommendation-heading")
    expect(heading).to_have_text("Scenarios tie")
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path="%s/compare-tie-%d-%s.png" % (WALK_DIR, viewport["width"], scheme))
    _compare(dialog, "Keep the ledger")
    expect(heading).to_have_text("Recommended Scenario")
    expect(sentence).to_have_text("Keep the ledger has the lowest combined risk score based on severity and financial impact.")
    ctx.close()
