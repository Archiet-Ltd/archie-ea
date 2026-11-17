"""Journey: an architect uploads a document into a solution and its extracted elements settle with no one
accepting them. The architect sees each one's verdict and reasons, follows the link to the review, finds no
Accept or Reject control, and can undo an applied one from the Approvals record, after which the model is as
it was. A second organisation's proposal is neither listed nor settled.

Run at 1440 wide in the light scheme and at 390 wide in the dark scheme. Set BLUEPRINT_SETTLE_JOURNEY_SHOTS to a
folder to keep screenshots.
"""

import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.test_connect_github_to_cited_answer_journey import remove_organisations

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("BLUEPRINT_SETTLE_JOURNEY_SHOTS")
SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"


def shoot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name), full_page=True)


@pytest.fixture(scope="module")
def world(app):
    from app.models.solution_models import Solution
    from app.services.solution_blueprint_service import create_solution_blueprint_proposal
    from app import db
    from app.models.archimate_core import ArchiMateElement

    suffix = uuid.uuid4().hex[:6]
    one = create_fresh_org("solution_architect", org_admin=True)
    two = create_fresh_org("solution_architect", org_admin=True)
    out = {"one": one, "two": two, "suffix": suffix,
           "new": "Brand new desk %s" % suffix, "repeat": "Existing hub %s" % suffix,
           "dropped": "Dropped idea %s" % suffix, "rival": "Rival idea %s" % suffix}
    with app.app_context():
        for key, org, names in (("one", one, ("new", "repeat", "dropped")), ("two", two, ("rival",))):
            solution = Solution(name="Journey solution %s" % org["suffix"], organization_id=org["org_id"],
                                created_by_id=org["user_ids"]["solution_architect"])
            db.session.add(solution)
            db.session.flush()
            out["solution_" + key] = solution.id
            if key == "one":
                db.session.add(ArchiMateElement(name=out["repeat"], type="ApplicationComponent", layer="application",
                                                organization_id=org["org_id"]))
            for name in names:
                proposal = create_solution_blueprint_proposal(
                    solution_id=solution.id, organization_id=org["org_id"], archimate_type="ApplicationComponent",
                    name=out[name], source="llm", acm_domain="BUS")
                if name == "dropped":
                    proposal.status = "rejected"  # a person took it out of the design before it was checked
            db.session.commit()
    yield out
    with app.app_context():
        # Rows that join to a solution through no organisation column of their own; removed by key.
        from sqlalchemy import text

        keys = {"ids": [out["solution_one"], out["solution_two"]]}
        for table in ("solution_archimate_elements", "codegen_generations"):
            db.session.execute(text("DELETE FROM %s WHERE solution_id = ANY(:ids)" % table), keys)
        db.session.commit()
    remove_organisations(app, [one["org_id"], two["org_id"]])


def settle(app, org_id):
    from app.jobs.tenant_safe_job import tenant_scope
    from app.modules.ai_chat.services import change_review

    with app.app_context():
        with tenant_scope(org_id):
            return change_review.settle_proposals(org_id)


def new_page(browser, world, width=1440, height=900, scheme="light"):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, world["base"], world["one"]["emails"]["solution_architect"])
    return context, page


def outcomes(page):
    """{proposal name: the outcome word its card shows}."""
    found = {}
    for card in page.locator("[data-testid^='blueprint-proposal-verdict-']").locator("xpath=..").all():
        name = card.get_by_test_id("blueprint-proposal-name").inner_text().strip()
        found[name] = card.locator("[data-testid^='blueprint-proposal-verdict-'] p").first.inner_text().strip()
    return found


def rows(page):
    return page.locator("[data-testid^='blueprint-proposal-verdict-']")


def test_1_the_extracted_elements_settle_with_no_accept_control_and_each_shows_its_verdict(browser, live_server, world, app):
    world["base"] = str(live_server)
    context, page = new_page(browser, world)
    page.goto("%s/solutions/%d/codegen" % (world["base"], world["solution_one"]), wait_until="domcontentloaded")
    expect(page.get_by_text("3 elements extracted")).to_be_visible()
    # Nobody has to accept anything: there is no Accept, Accept All, Reject or Dismiss control for a proposal.
    for name in ("Accept", "Accept All", "Reject", "Dismiss", "Dismiss All"):
        expect(page.get_by_role("button", name=name, exact=True)).to_have_count(0)
    shoot(page, "blueprint-in-review-1440.png")

    # The server settles them with no one asking (the job worker); settling again changes nothing.
    settle(app, world["one"]["org_id"])

    # The panel asks again while the review decides, so the verdicts arrive without a reload.
    for _ in range(30):
        if len(outcomes(page)) == 3 and "In review" not in outcomes(page).values():
            break
        page.wait_for_timeout(2000)
        page.reload(wait_until="domcontentloaded")
    got = outcomes(page)
    assert got == {world["new"]: "Applied", world["repeat"]: "Linked", world["dropped"]: "Declined"}, got
    verdicts = page.locator("[data-testid^='blueprint-proposal-verdict-']")
    expect(verdicts.filter(has_text="removed from the design before it was checked")).to_have_count(1)
    expect(verdicts.filter(has_text="Linked to the existing")).to_have_count(1)
    expect(page.get_by_text("being checked")).to_be_hidden()
    # A declined element shows no confidence chip beside its decline; the applied ones keep theirs.
    declined = page.locator("[data-testid='blueprint-proposal-name']").filter(has_text=world["dropped"]).locator("xpath=../..")
    expect(declined.get_by_test_id("blueprint-proposal-confidence")).to_be_hidden()
    applied = page.locator("[data-testid='blueprint-proposal-name']").filter(has_text=world["new"]).locator("xpath=../..")
    expect(applied.get_by_test_id("blueprint-proposal-confidence")).to_be_visible()
    # No internal ids or type names in the words a person reads.
    text = page.locator("body").inner_text()
    assert "(element " not in text and "ApplicationComponent" not in text
    # Another organisation's proposal is not listed here.
    assert world["rival"] not in text
    shoot(page, "blueprint-settled-1440.png")

    # Every verdict links to the review that made it, and that page has the reasons.
    links = page.locator("[data-testid^='blueprint-proposal-record-']")
    expect(links).to_have_count(3)
    for index in range(3):
        href = links.nth(index).get_attribute("href")
        page.goto(world["base"] + href, wait_until="domcontentloaded")
        page.wait_for_selector("[data-testid='review-record']")
        expect(page.get_by_test_id("review-record")).not_to_be_empty()
        expect(page.get_by_test_id("review-record-checks")).to_contain_text("Still in the design")
        page.go_back(wait_until="domcontentloaded")
        page.wait_for_selector("[data-testid^='blueprint-proposal-verdict-']")
    context.close()


def test_2_nothing_of_the_other_organisation_reached_this_one(world, app):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.models.archimate_core import ArchiMateElement

    with app.app_context():
        mine = ArchiMateElement.query.filter_by(organization_id=world["one"]["org_id"], name=world["rival"]).count()
        theirs = AIChatCRUDApproval.query.filter_by(
            organization_id=world["two"]["org_id"], source_table="solution_blueprint_proposals").count()
        stray = AIChatCRUDApproval.query.filter(
            AIChatCRUDApproval.organization_id == world["one"]["org_id"],
            AIChatCRUDApproval.summary.like("%" + world["rival"] + "%")).count()
        assert mine == 0 and stray == 0 and theirs == 1


def test_3_undo_on_the_approvals_record_puts_the_model_back_and_survives_a_reload(browser, live_server, world, app):
    from app.models.ai_chat_crud_approval import AIChatCRUDApproval
    from app.models.archimate_core import ArchiMateElement

    def added():
        with app.app_context():
            return ArchiMateElement.query.filter_by(
                organization_id=world["one"]["org_id"], name=world["new"], type="ApplicationComponent").count()

    with app.app_context():
        approval_id = AIChatCRUDApproval.query.filter_by(
            organization_id=world["one"]["org_id"], source_table="solution_blueprint_proposals",
            entity_type="solution_blueprint_element").filter(
            AIChatCRUDApproval.summary.like("%" + world["new"] + "%")).one().id
    assert added() == 1
    context, page = new_page(browser, world)
    page.goto(world["base"] + "/ai-chat/approvals/inbox?source=solution_blueprint_proposals", wait_until="domcontentloaded")
    undo = page.get_by_test_id("approval-undo-%d" % approval_id)
    expect(undo).to_be_visible()
    shoot(page, "blueprint-record-before-undo-1440.png")
    undo.click()
    page.wait_for_load_state("domcontentloaded")
    page.reload(wait_until="domcontentloaded")
    assert added() == 0, "undoing it removed the element it added"
    expect(page.get_by_test_id("approval-undo-%d" % approval_id)).to_have_count(0)
    body = page.locator("body").inner_text().lower()
    assert "or retired" not in body and "and retired" not in body, "the Approvals page does not offer a retired outcome"
    expect(page.get_by_text("applied, linked or declined, and why")).to_be_visible()
    expect(page.locator("body")).to_contain_text("Undone")
    assert "None" not in page.get_by_role("row").filter(has_text="Brand new desk").inner_text(), "no stray None in a cell"
    assert re.search(r"Undone on \d{1,2} [A-Z][a-z]{2} \d{4}", page.locator("body").inner_text()), "the date reads like 15 Nov 2026"
    shoot(page, "blueprint-record-after-undo-1440.png")
    context.close()


def test_4_a_narrow_dark_screen_shows_the_same_verdicts(browser, live_server, world):
    world["base"] = str(live_server)
    context, page = new_page(browser, world, width=390, height=844, scheme="dark")
    page.goto("%s/solutions/%d/codegen" % (world["base"], world["solution_one"]), wait_until="domcontentloaded")
    expect(page.get_by_text("3 elements extracted")).to_be_visible()
    expect(rows(page)).to_have_count(3)
    expect(page.get_by_role("button", name="Accept", exact=True)).to_have_count(0)
    # Each verdict sits fully inside the screen, beside its element's name, not off to one side.
    for index in range(3):
        box = rows(page).nth(index).bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390, box
    expect(page.get_by_test_id("blueprint-proposal-name").filter(has_text=world["new"])).to_be_visible()  # the whole name
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, "the page does not scroll sideways on a phone (%s px)" % overflow
    shoot(page, "blueprint-settled-390-dark.png")
    context.close()


def test_5_the_old_accept_addresses_accept_nothing_by_hand(browser, live_server, world, app):
    """A script or an old page that still posts to the accept addresses changes no proposal: the answer says
    nobody accepts them by hand, and the proposal's status is what the review left it."""
    from app.models.solution_blueprint_proposal import SolutionBlueprintProposal
    from tests.smoke.fresh_org import api

    def status_of(proposal_id):
        with app.app_context():
            return SolutionBlueprintProposal.query.filter_by(id=proposal_id).one().status

    with app.app_context():
        dropped = SolutionBlueprintProposal.query.filter_by(
            organization_id=world["one"]["org_id"], name=world["dropped"]).one().id
    world["base"] = str(live_server)
    context, page = new_page(browser, world)
    base = "%s/architecture-journey/%d" % (world["base"], world["solution_one"])
    before = status_of(dropped)
    code, one = api(page, "POST", "%s/proposals/%d/accept" % (base, dropped))
    assert code == 200 and (one.get("data") or one)["accepted"] == 0, one
    code, many = api(page, "POST", "%s/proposals/batch-accept" % base, {"proposal_ids": [dropped]})
    assert code == 200 and (many.get("data") or many)["accepted"] == 0, many
    assert "nobody accepts them by hand" in (many.get("data") or many)["message"]
    assert status_of(dropped) == before == "rejected", "a proposal taken out of the design stays out"
    context.close()
