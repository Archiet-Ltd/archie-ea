"""A visitor weighing Startup reads one description of it, on the home page and on /pricing.

The words come from the plan record. The head count they state is the number of
people a Startup organisation really admits, counted here by adding people until
the plan refuses one.
"""
import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("PLAN_WORDS_SHOTS")
WORDS = {"three": 3, "five": 5, "ten": 10, "fifty": 50}


@pytest.fixture(params=[1440, 390], ids=["wide", "narrow"])
def visitor(browser, request):
    context = browser.new_context(viewport={"width": request.param, "height": 900})
    page = context.new_page()
    yield page, request.param
    context.close()


def _shot(page, name, width):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%d.png" % (name, width)), full_page=True)


def _people_a_startup_organisation_holds(db_session):
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.services.billing_plans import PlanLimitReached

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name="Words startup %s" % suffix, slug="words-startup-%s" % suffix, plan="startup")
    db_session.add(org)
    db_session.flush()
    role = Role.query.filter_by(name="User").first()
    held = 0
    try:
        while held < 100:
            with db_session.begin_nested():
                db_session.add(User(email="words-%s@example.com" % uuid.uuid4().hex[:10], first_name="A",
                                    last_name="B", organization_id=org.id, role=role, confirmed=True))
                db_session.flush()
            held += 1
    except PlanLimitReached:
        pass
    return held


def test_startup_is_described_the_same_way_on_both_pages_and_matches_what_it_holds(visitor, live_server, db_session):
    page, width = visitor
    held = _people_a_startup_organisation_holds(db_session)

    page.goto(live_server + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    card = page.get_by_test_id("plan-card-startup")
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
    card.scroll_into_view_if_needed()
    _shot(page, "pricing", width)
    audience = card.locator(".pp-for").inner_text().strip()
    features = [t.strip() for t in card.locator(".pp-list li:not(.pp-lead-in)").all_inner_texts()]

    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row_for = page.get_by_test_id("home-plan-for-startup")
    row_for.scroll_into_view_if_needed()
    _shot(page, "home", width)
    expect(row_for).to_have_text(audience)
    expect(page.get_by_test_id("home-plan-includes-startup")).to_have_text(". ".join(features) + ".")

    stated = {WORDS[w.lower()] for w in re.findall(r"\b(three|five|ten|fifty)\b(?:-| )people", audience + " " + " ".join(features), re.I)}
    assert stated == {held} == {10}, (stated, held)
    assert "5 to 50" not in audience

    # Team reads the same on both pages too, and says more than ten, not a range.
    page.goto(live_server + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    team = page.get_by_test_id("plan-card-team")
    team_audience = team.locator(".pp-for").inner_text().strip()
    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("home-plan-for-team")).to_have_text(team_audience)
    assert "more than ten people" in team_audience

    # The link a person takes from the Startup card lands on a page with content.
    page.goto(live_server + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("talk-startup").click()
    page.wait_for_url(lambda url: "/contact" in url, timeout=PAGE_TIMEOUT)
    expect(page.locator("main h1").first).to_be_visible(timeout=PAGE_TIMEOUT)
    assert len(page.locator("main").inner_text().strip()) > 80
