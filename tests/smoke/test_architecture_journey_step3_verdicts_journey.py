"""Journey: an architect opens step 3 of the Architecture Journey for a solution whose proposed elements the
review has settled. They find no Accept control anywhere on the step, each element shows the outcome the review
recorded with its reasons, and the link beside it opens the review record, which has content. Nothing reads
accepted that the review has not decided.

Run at 1440 wide in the light scheme and at 390 wide in the dark scheme. Set STEP3_VERDICTS_JOURNEY_SHOTS to a
folder to keep screenshots.
"""

import os
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.test_connect_github_to_cited_answer_journey import remove_organisations

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("STEP3_VERDICTS_JOURNEY_SHOTS")
SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"


def shoot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name), full_page=True)


@pytest.fixture(scope="module")
def world(app):
    from app import db
    from app.jobs.tenant_safe_job import tenant_scope
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_models import Solution
    from app.modules.ai_chat.services import change_review
    from app.services.solution_blueprint_service import create_solution_blueprint_proposal

    suffix = uuid.uuid4().hex[:6]
    one = create_fresh_org("solution_architect", org_admin=True)
    out = {"one": one, "new": "Fresh desk %s" % suffix, "repeat": "Known hub %s" % suffix}
    with app.app_context():
        solution = Solution(name="Step three solution %s" % suffix, organization_id=one["org_id"],
                            created_by_id=one["user_ids"]["solution_architect"],
                            journey_state={"current_step": 3, "completed_steps": [1, 2], "domains_populated": True})
        db.session.add(solution)
        db.session.flush()
        out["solution"] = solution.id
        db.session.add(ArchiMateElement(name=out["repeat"], type="ApplicationComponent", layer="application",
                                        organization_id=one["org_id"]))
        for key in ("new", "repeat"):
            create_solution_blueprint_proposal(
                solution_id=solution.id, organization_id=one["org_id"], archimate_type="ApplicationComponent",
                name=out[key], source="llm", acm_domain="APP")
        db.session.commit()
        with tenant_scope(one["org_id"]):
            change_review.settle_proposals(one["org_id"])
    yield out
    with app.app_context():
        from sqlalchemy import text

        db.session.execute(text("DELETE FROM solution_archimate_elements WHERE solution_id = :id"), {"id": out["solution"]})
        db.session.commit()
    remove_organisations(app, [one["org_id"]])


def open_step3(browser, world, base, width, height, scheme):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, base, world["one"]["emails"]["solution_architect"])
    page.goto("%s/architecture-journey/%d" % (base, world["solution"]), wait_until="domcontentloaded")
    return context, page


def check_step(page, world, base, shot):
    page.wait_for_selector("[data-testid^='step3-element-verdict-']", state="attached")
    # Layers start collapsed; open each so every element is on screen.
    verdicts = page.locator("[data-testid^='step3-element-verdict-']")
    count = verdicts.count()
    assert count >= 2, "every settled element is listed"
    text = page.locator("body").inner_text()
    assert "Accept all" not in text, "no bulk accept on the step"
    expect(page.get_by_label("Accept or reject element")).to_have_count(0)
    # Each element shows what the review recorded, not a tick the page made up.
    outcomes = {verdicts.nth(i).locator("span").first.inner_text().strip() for i in range(count)}
    assert "Applied" in outcomes and outcomes <= {"Applied", "Linked", "Not reviewed"}, outcomes
    assert "In review" not in outcomes, "nothing here waits on a person"
    fresh = verdicts.filter(has_text="Applied")
    expect(fresh.first).to_contain_text("Applied on the deterministic checks")
    verdicts.first.scroll_into_view_if_needed()
    shoot(page, shot)
    links = page.locator("[data-testid^='step3-element-record-']")
    assert links.count() >= 2
    href = links.first.get_attribute("href")
    page.goto(base + href, wait_until="domcontentloaded")
    page.wait_for_selector("[data-testid='review-record']")
    expect(page.get_by_test_id("review-record")).not_to_be_empty()
    expect(page.get_by_test_id("review-record-checks")).to_contain_text("Still in the design")


def test_1_step_three_has_no_accept_control_and_each_element_shows_the_recorded_outcome(browser, live_server, world):
    base = str(live_server)
    context, page = open_step3(browser, world, base, 1440, 900, "light")
    check_step(page, world, base, "step3-verdicts-1440.png")
    context.close()


def test_2_a_narrow_dark_screen_shows_the_same(browser, live_server, world):
    base = str(live_server)
    context, page = open_step3(browser, world, base, 390, 844, "dark")
    check_step(page, world, base, "step3-verdicts-390.png")
    context.close()
