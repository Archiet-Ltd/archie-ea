"""The roadmap capability sync and the SAP import stay inside the signed-in organisation.

Two real organisations. A signs in and runs the roadmap sync: A's capability gets its work
package, and nothing of B's is created, changed or archived. A then imports SAP metadata for
B's solution: the answer is 404, the same as for a solution that does not exist, and no
architecture model is written for either organisation.
"""

import uuid

import pytest

from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id, user_id, label):
    """One capability, an out-of-date work package for it, an orphaned work package, one solution."""
    from app import create_app, db
    from app.models.roadmap_models import RoadmapWorkPackage
    from app.models.solution_models import Solution
    from app.models.unified_capability import UnifiedCapability

    app = create_app("testing")
    with app.app_context():
        cap = UnifiedCapability(name="%s capability %s" % (label, uuid.uuid4().hex[:6]), level=1,
                                organization_id=org_id, scope="tenant")
        db.session.add(cap)
        db.session.flush()
        stale = RoadmapWorkPackage(name="old name", business_capability="old", status="planned",
                                   priority="low", source_type="capability", source_id=cap.id, created_by=user_id)
        orphan = RoadmapWorkPackage(name="%s orphan" % label, business_capability="gone", status="planned",
                                    source_type="capability", source_id=987000000 + org_id, created_by=user_id)
        solution = Solution(name="%s solution" % label, organization_id=org_id)
        db.session.add_all([stale, orphan, solution])
        db.session.commit()
        out = {"cap_id": cap.id, "cap_name": cap.name, "stale_id": stale.id, "orphan_id": orphan.id,
               "solution_id": solution.id}
        db.session.remove()
    return out


def _state(ids):
    from app import create_app, db
    from app.models.roadmap_models import RoadmapWorkPackage

    app = create_app("testing")
    with app.app_context():
        stale = db.session.get(RoadmapWorkPackage, ids["stale_id"])
        orphan = db.session.get(RoadmapWorkPackage, ids["orphan_id"])
        created = db.session.execute(
            db.text("select count(*) from roadmap_work_packages where source_type='capability' and source_id=:i"),
            {"i": ids["cap_id"]}).scalar()
        state = (stale.name, stale.status, orphan.status, created)
        db.session.remove()
    return state


def _models_for(solution_id):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        n = db.session.execute(db.text("select count(*) from architecture_models where solution_id=:s"),
                               {"s": solution_id}).scalar()
        db.session.remove()
    return n


def _two_orgs():
    a = create_fresh_org("solution_architect")
    b = create_fresh_org("solution_architect")
    a_ids = _seed(a["org_id"], a["user_ids"]["solution_architect"], "Alpha")
    b_ids = _seed(b["org_id"], b["user_ids"]["solution_architect"], "Beta")
    return a, b, a_ids, b_ids


def test_roadmap_sync_stays_inside_the_organisation(browser, live_server):
    a, b, a_ids, b_ids = _two_orgs()
    b_before = _state(b_ids)

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    sign_in(page, live_server, a["emails"]["solution_architect"])

    status, _ = api(page, "POST", "/api/roadmap/sync/capabilities", {})
    assert status == 200
    # A's own capability is synced: its work package now carries the capability's name.
    name, _, orphan_status, _ = _state(a_ids)
    assert name == "Implement " + a_ids["cap_name"]
    # B's rows are exactly as they were: not renamed, not archived, no new package.
    assert _state(b_ids) == b_before
    context.close()


def test_sap_import_stays_inside_the_organisation(browser, live_server):
    a, b, a_ids, b_ids = _two_orgs()
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    sign_in(page, live_server, a["emails"]["solution_architect"])

    # SAP import of B's solution from A: same 404 as a solution that does not exist, nothing written.
    status, _ = api(page, "POST", "/api/sap/import", {"solution_id": b_ids["solution_id"], "mock": True})
    assert status == 404
    missing, _ = api(page, "POST", "/api/sap/import", {"solution_id": 999999999, "mock": True})
    assert missing == 404
    assert _models_for(b_ids["solution_id"]) == 0

    # A's own solution still imports.
    status, _ = api(page, "POST", "/api/sap/import", {"solution_id": a_ids["solution_id"], "mock": True})
    assert status == 200
    assert _models_for(a_ids["solution_id"]) == 1
    context.close()
