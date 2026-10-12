"""An enterprise architect ties an initiative to the goal it serves, in a real browser.

Open the initiative, choose the goal and its contribution, press "Link goal",
reload and see it still listed. The portfolio list filtered to initiatives
serving no objective then drops that initiative and keeps the one never linked.
A second organisation sees none of it.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.models.motivation import Goal
    from app.models.vendor.vendor_organization import EnterpriseInitiative

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        goal = Goal(name="Grow Retail %s" % suffix, organization_id=org_id)
        distractor = Goal(name="Grow Wholesale %s" % suffix, organization_id=org_id)
        linked = EnterpriseInitiative(name="Digital Checkout %s" % suffix, organization_id=org_id)
        bare = EnterpriseInitiative(name="Back Office Tidy %s" % suffix, organization_id=org_id)
        db.session.add_all([goal, distractor, linked, bare])
        db.session.flush()
        for g in (goal, distractor):
            if g.archimate_element_id is None:
                el = ArchiMateElement(name=g.name, type="Goal", layer="motivation", organization_id=org_id)
                db.session.add(el)
                db.session.flush()
                g.archimate_element_id = el.id
        db.session.commit()
        out = {"goal": goal.name, "linked": linked.name, "linked_id": linked.id, "bare": bare.name}
        db.session.remove()
    return out


def _page(browser):
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return ctx, ctx.new_page()


def test_architect_links_an_initiative_to_a_goal(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    other = create_fresh_org("enterprise_architect")
    seeded = _seed(org["org_id"])

    ctx, page = _page(browser)
    sign_in(page, live_server, org["emails"]["enterprise_architect"])
    page.goto("%s/portfolio/%d" % (live_server, seeded["linked_id"]),
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    section = page.locator("#initiative-goals")
    expect(section).to_contain_text("No objective recorded")
    picker = section.get_by_role("combobox", name="Type a goal name")
    picker.fill(seeded["goal"])
    section.get_by_role("option", name=seeded["goal"]).click()
    section.locator("#initiative-goal-level").select_option("primary")
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        section.get_by_role("button", name="Link goal").click()
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    section = page.locator("#initiative-goals")
    expect(section).to_contain_text(seeded["goal"])
    expect(section).to_contain_text("primary")

    page.goto(live_server + "/portfolio/?filter=no-objective",
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text(seeded["bare"])).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.get_by_text(seeded["linked"])).to_have_count(0)
    ctx.close()

    ctx, page = _page(browser)
    sign_in(page, live_server, other["emails"]["enterprise_architect"])
    page.goto(live_server + "/portfolio/?filter=no-objective",
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_text(seeded["bare"])).to_have_count(0)
    ctx.close()
