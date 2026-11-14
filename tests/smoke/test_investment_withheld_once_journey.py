"""A person the cost rule withholds from reads the one withheld sentence once per investment page, and a
derived score's step opens that capability's own importance.

Goal: "I open the investment matrix or the investment priorities, and where my role may not see cost figures
I read 'Cost figures are withheld for your role.' once on the page — not once per row — and no money anywhere.
Where a capability's score was worked out for want of a recorded importance, its 'Record its importance' step
opens that capability's own edit form, with the capability already chosen, and saving the importance replaces
the derived score on both pages."

The organisation is seeded with linked data (a funded capability and an unfunded, unrated one) and a second
organisation whose capability must never reach the first one's pages.
"""
import os
import pathlib
import tempfile
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.fresh_org import create_fresh_org, sign_in

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PAGES = ("/strategic/investment-matrix", "/architecture/investment-priorities")
HELD_BACK = "Cost figures are withheld for your role."
SHOTS = pathlib.Path(os.environ.get("SMOKE_SHOT_DIR") or tempfile.mkdtemp(prefix="investment-withheld-"))


def _seed_org(org_id, tag):
    """One funded, rated capability and one capability with nothing recorded; returns their names."""
    from app import db
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability
    from app.models.unified_work_package import UnifiedWorkPackage

    funded_name = "Settle claims %s" % tag
    plain_name = "Plan events %s" % tag
    funded = UnifiedCapability(
        name=funded_name, organization_id=org_id, strategic_importance="high", level=1)
    db.session.add(funded)
    db.session.flush()
    # The priorities page opens only once some application is mapped to a capability.
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping

    app_row = ApplicationComponent(name="Claims system %s" % tag, organization_id=org_id)
    db.session.add(app_row)
    db.session.flush()
    db.session.add(UnifiedApplicationCapabilityMapping(
        unified_capability_id=funded.id, application_component_id=app_row.id))
    db.session.add(UnifiedWorkPackage(
        name="Work for %s" % funded_name, organization_id=org_id,
        capability_id=funded.id, capability_ids=[funded.id], estimated_cost=12345.0))
    # Created the way the capabilities page creates one, so that page lists it too and its
    # importance can be recorded there.
    db.session.add(BusinessCapability(name=plain_name, organization_id=org_id))
    # A capability nobody rated whose application is rated high: its score is worked out, not recorded.
    derived_name = "Run payroll %s" % tag
    derived_row = BusinessCapability(name=derived_name, organization_id=org_id)
    db.session.add(derived_row)
    db.session.flush()
    derived = UnifiedCapability.query.filter_by(
        source_table="business_capability", source_id=str(derived_row.id), organization_id=org_id).one()
    rated_app = ApplicationComponent(
        name="Payroll system %s" % tag, organization_id=org_id, business_criticality="high")
    db.session.add(rated_app)
    db.session.flush()
    db.session.add(UnifiedApplicationCapabilityMapping(
        unified_capability_id=derived.id, application_component_id=rated_app.id))
    db.session.commit()
    return {"funded": funded_name, "plain": plain_name, "derived": derived_name, "derived_bc": derived_row.id}


@pytest.fixture(scope="module")
def two_orgs(live_server):
    from app import create_app

    tag = uuid.uuid4().hex[:6]
    mine = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    app = create_app("testing")
    with app.app_context():
        names = _seed_org(mine["org_id"], "m" + tag)
        foreign = _seed_org(other["org_id"], "f" + tag)
    mine["admin_email"] = "launch.admin.%s@example.com" % mine["suffix"]
    return {"mine": mine, "names": names, "foreign": foreign, "foreign_org_id": other["org_id"]}


def _row(page, name):
    return page.get_by_test_id("capability-row").filter(has_text=name)


def _shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))), full_page=False)


def test_a_withheld_person_reads_the_sentence_once_per_page_and_no_money(browser, live_server, two_orgs):
    names, foreign = two_orgs["names"], two_orgs["foreign"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    try:
        sign_in(page, live_server, two_orgs["mine"]["emails"]["enterprise_architect"])
        for path in PAGES:
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(_row(page, names["plain"])).to_have_count(1)
            # Another organisation's capability never reaches this one's pages.
            assert foreign["plain"] not in page.content()
            assert foreign["funded"] not in page.content()
            # The one sentence, once on the page, in the page-level notice.
            assert page.content().count(HELD_BACK) == 1, path
            expect(page.get_by_test_id("investment-costs-withheld")).to_have_text(HELD_BACK)
            # No money anywhere, drawn or hidden.
            assert "£" not in page.content(), path
            # Rows show neither money nor the sentence; the investment cell says Withheld.
            row = _row(page, names["funded"])
            cell = row.get_by_test_id("cell-investment")
            expect(cell).to_contain_text("Withheld")
            assert HELD_BACK not in cell.inner_text()
            total = page.get_by_test_id("total-investment").inner_text()
            assert "Withheld" in total and "£" not in total, total
            _shot(page, "withheld-" + ("matrix" if "strategic" in path else "priorities"), 1440, "light")
    finally:
        context.close()


def test_a_derived_scores_step_opens_that_capabilitys_own_importance(browser, live_server, two_orgs):
    names = two_orgs["names"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    try:
        sign_in(page, live_server, two_orgs["mine"]["emails"]["enterprise_architect"])
        page.goto(live_server + PAGES[0], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = _row(page, names["plain"])
        expect(row).to_have_count(1)
        # Nothing is recorded to score it from, so the row offers the one step.
        assert row.get_by_test_id("strategic-step").count() == 1
        step = row.get_by_test_id("strategic-step")
        href = step.get_attribute("href")
        assert href and "edit=" in href, href
        step.click()
        page.wait_for_load_state("domcontentloaded")
        # The capability's own edit form is open, with the capability already chosen: its name
        # in the name field and its importance control beside it.
        dialog = page.locator("#cap-dialog")
        expect(dialog).to_be_visible()
        expect(page.locator("#cap-name")).to_have_value(names["plain"])
        expect(page.locator("#cap-importance")).to_be_visible()
        # Record the importance through that form.
        page.locator("#cap-importance").select_option("high")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.locator("#cap-save").click()
        expect(dialog).to_be_hidden(timeout=PAGE_TIMEOUT)
        # Both pages now show the recorded value for that capability, and no derived tag, no step.
        for path in PAGES:
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            row = _row(page, names["plain"])
            expect(row).to_have_count(1)
            strategic = row.get_by_test_id("cell-strategic").inner_text()
            assert strategic.startswith("20/25"), strategic
            assert "derived" not in strategic.lower(), strategic
            assert "high importance" in strategic, strategic
            assert row.get_by_test_id("strategic-derived").count() == 0
            assert row.get_by_test_id("strategic-step").count() == 0
        _shot(page, "recorded-importance", 1440, "light")
    finally:
        context.close()


def test_a_role_that_may_see_costs_reads_the_money_and_no_sentence(browser, live_server, two_orgs):
    names = two_orgs["names"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    try:
        sign_in(page, live_server, two_orgs["mine"]["admin_email"])
        for path in PAGES:
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(_row(page, names["funded"])).to_have_count(1)
            # The sentence does not appear; the money does.
            assert HELD_BACK not in page.content(), path
            assert page.get_by_test_id("investment-costs-withheld").count() == 0, path
            assert "£" in page.content(), path
            row = _row(page, names["funded"])
            cell = row.get_by_test_id("cell-investment").inner_text()
            assert "£" in cell and "12.3k" in cell, cell
            total = page.get_by_test_id("total-investment").inner_text()
            assert "£" in total and "Withheld" not in total, total
            _shot(page, "admin-" + ("matrix" if "strategic" in path else "priorities"), 1440, "light")
    finally:
        context.close()


def test_the_withheld_sentence_once_on_a_phone_in_the_dark(browser, live_server, two_orgs):
    names = two_orgs["names"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844},
                                  color_scheme="dark")
    page = context.new_page()
    try:
        sign_in(page, live_server, two_orgs["mine"]["emails"]["enterprise_architect"])
        page.goto(live_server + PAGES[1], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(_row(page, names["plain"])).to_have_count(1)
        assert page.evaluate("document.documentElement.scrollWidth") <= 391
        assert page.content().count(HELD_BACK) == 1
        # The phone card says Withheld, not the sentence, and carries no money.
        phone_cell = _row(page, names["funded"]).get_by_test_id("phone-investment")
        expect(phone_cell).to_contain_text("Withheld")
        assert HELD_BACK not in phone_cell.inner_text()
        assert "£" not in page.content()
        _shot(page, "withheld-priorities", 390, "dark")
    finally:
        context.close()


def _importance_in_database(org_id, name):
    """What the capability records and its unified record hold for importance, read from the database."""
    from app import create_app, db
    from app.models.business_capabilities import BusinessCapability
    from app.models.unified_capability import UnifiedCapability

    app = create_app("testing")
    with app.app_context():
        recorded = db.session.execute(db.select(
            BusinessCapability.strategic_importance, BusinessCapability.category
        ).where(BusinessCapability.organization_id == org_id, BusinessCapability.name == name)).one()
        unified = db.session.execute(db.select(UnifiedCapability.strategic_importance).where(
            UnifiedCapability.organization_id == org_id, UnifiedCapability.name == name)).scalar_one()
        return recorded[0], recorded[1], unified


def test_a_derived_score_offers_the_step_and_recording_importance_replaces_it(
        browser, live_server, two_orgs):
    names, foreign = two_orgs["names"], two_orgs["foreign"]
    org_id, other_id = two_orgs["mine"]["org_id"], two_orgs["foreign_org_id"]
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    try:
        sign_in(page, live_server, two_orgs["mine"]["emails"]["enterprise_architect"])
        for path in PAGES:
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            row = _row(page, names["derived"])
            expect(row).to_have_count(1)
            # Worked out from the application's criticality, tagged so, and the step is offered.
            expect(row.get_by_test_id("strategic-derived")).to_have_count(1)
            expect(row.get_by_test_id("strategic-sentence")).to_contain_text("criticality")
            expect(row.get_by_test_id("strategic-step")).to_have_text("Record its importance")
        page.goto(live_server + PAGES[1], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = _row(page, names["derived"])
        row.get_by_test_id("strategic-step").click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("#cap-dialog")).to_be_visible()
        expect(page.locator("#cap-name")).to_have_value(names["derived"])
        # The category stays as it was: not chosen.
        expect(page.locator("#cap-category")).to_have_value("")
        page.locator("#cap-importance").select_option("medium")
        with page.expect_navigation(timeout=PAGE_TIMEOUT):
            page.locator("#cap-save").click()
        expect(page.locator("#cap-dialog")).to_be_hidden(timeout=PAGE_TIMEOUT)
        for path in PAGES:
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            row = _row(page, names["derived"])
            expect(row).to_have_count(1)
            strategic = row.get_by_test_id("cell-strategic").inner_text()
            assert "medium importance" in strategic, strategic
            assert strategic.startswith("15/25"), strategic
            assert row.get_by_test_id("strategic-derived").count() == 0
            assert row.get_by_test_id("strategic-step").count() == 0
        _shot(page, "derived-recorded", 1440, "light")
    finally:
        context.close()
    # Importance changed, the category was left alone, and the other organisation's twin did not move.
    assert _importance_in_database(org_id, names["derived"]) == ("medium", None, "medium")
    assert _importance_in_database(other_id, foreign["derived"]) == (None, None, None)


def test_a_person_the_save_refuses_is_offered_no_step_on_either_page(browser, live_server):
    from app import create_app

    plain = create_fresh_org("enterprise_architect", platform_role="Viewer")
    tag = uuid.uuid4().hex[:6]
    with create_app("testing").app_context():
        names = _seed_org(plain["org_id"], "u" + tag)
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 390, "height": 844},
                                  color_scheme="dark")
    page = context.new_page()
    try:
        sign_in(page, live_server, plain["emails"]["enterprise_architect"])
        for path in PAGES:
            page.goto(live_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            expect(_row(page, names["derived"])).to_have_count(1)
            expect(_row(page, names["derived"]).get_by_test_id("strategic-derived")).to_have_count(1)
            assert page.get_by_test_id("strategic-step").count() == 0, path
            assert page.content().count(HELD_BACK) == 1, path
        _shot(page, "no-step-plain-user", 390, "dark")
    finally:
        context.close()
