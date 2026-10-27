"""A solution design shows only its own organisation's rows (T-sec03 batch 4).

The person: an architect opening the design of their own solution. The goal: read the
design and be sure its stakeholders, its roadmap work package and its capability belong to
their organisation and nobody else's. Two organisations each own a solution, a stakeholder
mapped onto it, a roadmap work package for it and a capability mapping. The architect of
organisation A opens A's solution design: the page carries A's content by name — the solution,
its stakeholder, its work package, its capability — and never B's. The stakeholder map the
page links to opens on a page with content. An architect of organisation B then opens A's
solution design and is refused, and A's rows never reach B through the interfaces the page calls.

Fenced tables exercised through the rendered page and its own interfaces: solution_stakeholder_mappings
(the stakeholder grid), roadmap_work_packages (the roadmap gantt data) and solution_capability_mappings.
"""

import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWS = [(1440, "light"), (390, "light"), (1440, "dark"), (390, "dark")]


def _seed(org_id, created_by_id, tag, side, planted_source_id=None, planted_name=None):
    """One solution with a stakeholder mapping, a roadmap work package and a capability, all in org_id.

    ``planted_source_id`` adds a second roadmap work package in THIS organisation that points its
    source_id at another organisation's solution (source_id is a plain integer, not a scoped key).
    Before the batch-4 fence the roadmap read of that solution returns this foreign row too; after
    it, the organisation filter hides it. That planted row is what makes the roadmap assertion fail
    before the fence and pass after.
    """
    from app import create_app, db
    from app.models.business_capability import BusinessCapability
    from app.models.roadmap_models import RoadmapWorkPackage
    from app.models.solution_models import Solution, SolutionCapabilityMapping
    from app.models.solution_stakeholder import (
        SolutionStakeholder,
        SolutionStakeholderMapping,
        StakeholderRole,
    )

    app = create_app("testing")
    with app.app_context():
        names = {
            "solution": f"{side} platform {tag}",
            "stakeholder": f"{side} sponsor {tag}",
            "work_package": f"{side} migration {tag}",
            "capability": f"{side} capability {tag}",
        }
        solution = Solution(name=names["solution"], organization_id=org_id, created_by_id=created_by_id)
        db.session.add(solution)
        db.session.flush()

        stakeholder = SolutionStakeholder(
            name=names["stakeholder"], organization_id=org_id, influence_level=5, interest_level=5)
        db.session.add(stakeholder)
        db.session.flush()
        db.session.add(SolutionStakeholderMapping(
            stakeholder_id=stakeholder.id, solution_id=solution.id,
            role=StakeholderRole.SPONSOR, organization_id=org_id))

        db.session.add(RoadmapWorkPackage(
            name=names["work_package"], status="planned", business_capability=names["capability"],
            source_type="solution", source_id=solution.id, created_by=created_by_id,
            organization_id=org_id))

        if planted_source_id is not None:
            # This organisation's row, but pointed at the other organisation's solution id.
            db.session.add(RoadmapWorkPackage(
                name=planted_name, status="planned", business_capability="planted",
                source_type="solution", source_id=planted_source_id, created_by=created_by_id,
                organization_id=org_id))

        capability = BusinessCapability(name=names["capability"], level=1, organization_id=org_id)
        db.session.add(capability)
        db.session.flush()
        db.session.add(SolutionCapabilityMapping(
            solution_id=solution.id, capability_id=capability.id, organization_id=org_id))

        db.session.commit()
        sid = solution.id
        db.session.remove()
    return sid, names


def _context(browser, width, theme):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.set_default_timeout(30000)
    if theme == "dark":
        ctx.add_init_script("try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
                            "document.documentElement.classList.add('dark');")
    return ctx


def _shot(page, tag, name):
    root = os.environ.get("JOURNEY_SHOT_DIR") or "/home/ubuntu/verify/seats/sec03d1/walk"
    folder = os.path.abspath(os.path.join(root, tag))
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, name + ".png"), full_page=True)


@pytest.mark.parametrize("width,theme", VIEWS)
def test_a_solution_design_shows_only_its_own_organisation(browser, live_server, width, theme):
    tag = uuid.uuid4().hex[:6]
    a = create_fresh_org("solution_architect")
    b = create_fresh_org("solution_architect")
    sol_a, names_a = _seed(a["org_id"], a["user_ids"]["solution_architect"], tag, "Alpha")
    planted_name = "Beta planted onto Alpha %s" % tag
    # Organisation B owns a work package that names A's solution id as its source.
    sol_b, names_b = _seed(b["org_id"], b["user_ids"]["solution_architect"], tag, "Beta",
                           planted_source_id=sol_a, planted_name=planted_name)

    ctx_a = _context(browser, width, theme)
    page_a = ctx_a.new_page()
    ctx_b = _context(browser, width, theme)
    page_b = ctx_b.new_page()
    try:
        # Organisation A opens its own solution design. The page carries A's content by name.
        sign_in(page_a, live_server, a["emails"]["solution_architect"])
        resp = page_a.goto("%s/solutions/%d?edit=1" % (live_server, sol_a),
                           wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert resp.status == 200
        # The solution's name is the page heading a person reads (not a hidden <option> in a picker).
        expect(page_a.get_by_role("heading", name=names_a["solution"]).first).to_be_visible()
        body_a = page_a.content()
        assert names_a["capability"] in body_a, "the solution's own capability is not on the page"
        # Not an empty state: the stakeholder section counts the one mapped stakeholder.
        assert "No stakeholders mapped yet" not in body_a
        # None of organisation B's names appear anywhere on A's page, including the planted row.
        for other in list(names_b.values()) + [planted_name]:
            assert other not in body_a, "a foreign row name reached A's page: %s" % other
        _shot(page_a, "%d-%s" % (width, theme), "01-own-design")

        # The stakeholder grid the page draws reads the fenced mapping table: A's stakeholder, not B's.
        status, grid = api(page_a, "GET", "/api/stakeholders/map-data?solution_id=%d" % sol_a)
        assert status == 200
        grid_names = {row.get("name") for row in grid} if isinstance(grid, list) else set()
        assert names_a["stakeholder"] in grid_names, "A's own stakeholder is missing from the grid"
        assert names_b["stakeholder"] not in grid_names
        assert grid, "the stakeholder grid is empty — the page would show no stakeholders"

        # The roadmap the page draws reads the fenced work-package table: A's package, not B's.
        status, roadmap = api(page_a, "GET", "/solutions/%d/gantt-data" % sol_a)
        assert status == 200
        task_names = {t.get("name") for t in roadmap.get("gantt", {}).get("tasks", [])}
        assert names_a["work_package"] in task_names, "A's own work package is missing from the roadmap"
        assert names_b["work_package"] not in task_names
        # The planted row (B's, pointed at A's solution id) must not reach A's roadmap: this is the
        # cross-organisation leak the batch-4 fence closes — without it the roadmap read returns it.
        assert planted_name not in task_names, "a foreign organisation's work package reached A's roadmap"

        # The stakeholder map the page links to opens on a page with content, not an empty shell.
        map_resp = page_a.goto("%s/stakeholders/map" % live_server,
                               wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert map_resp.status == 200
        # The map page carries content: its heading renders and A's solution is offered to open.
        expect(page_a.get_by_role("heading").first).to_be_visible()
        map_body = page_a.content()
        assert names_a["solution"] in map_body, "A's solution is not offered on the stakeholder map"
        assert names_b["solution"] not in map_body, "B's solution leaked onto A's stakeholder map"
        _shot(page_a, "%d-%s" % (width, theme), "02-stakeholder-map")

        # Organisation B opens A's solution design and is refused, and no interface leaks A's rows.
        sign_in(page_b, live_server, b["emails"]["solution_architect"])
        refused = page_b.goto("%s/solutions/%d?edit=1" % (live_server, sol_a),
                              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert refused.status in (403, 404), "organisation B reached A's solution (status %s)" % refused.status
        refused_body = page_b.content()
        for a_name in names_a.values():
            assert a_name not in refused_body, "A's content leaked to B on the refusal page: %s" % a_name
        _shot(page_b, "%d-%s" % (width, theme), "03-other-org-refused")

        status, grid_b = api(page_b, "GET", "/api/stakeholders/map-data?solution_id=%d" % sol_a)
        assert status == 404
        assert names_a["stakeholder"] not in str(grid_b)
        status, roadmap_b = api(page_b, "GET", "/solutions/%d/gantt-data" % sol_a)
        assert status in (403, 404)
        assert names_a["work_package"] not in str(roadmap_b)

        # B's own solution design works and shows only B's rows.
        own = page_b.goto("%s/solutions/%d?edit=1" % (live_server, sol_b),
                          wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert own.status == 200
        expect(page_b.get_by_role("heading", name=names_b["solution"]).first).to_be_visible()
        own_body = page_b.content()
        for a_name in names_a.values():
            assert a_name not in own_body
    finally:
        ctx_a.close()
        ctx_b.close()
