"""R1-B04 PR 2 fix round 1, D-02: the bridge.

A screen that still writes one of the four retired work package stores must not
leave the one store behind: a row it creates, edits or deletes appears, changes
and disappears in ``work_package_service.query_for(org)`` in the same
transaction, attributed to the right organisation, and the other organisation
never sees it.

Also the three repointed list screens: they return the one store's ids, and
edit and delete by those ids work.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label):
    from tests.test_work_package_writer import _user as writer_user

    return writer_user(db_session, org, label)


def _copy(table, source_id, org):
    from app.services import work_package_service

    return work_package_service.get_by_source(table, source_id, org.id)


def test_bridged_row_appears_changes_and_disappears_in_the_one_store(db_session, make_org):
    from app.models.implementation_migration import WorkPackage
    from app.services import work_package_service as svc

    org_a, org_b = make_org("bridge-a"), make_org("bridge-b")
    legacy = WorkPackage(name="Created by an old screen", organization_id=org_a.id,
                         status="planned", priority="high")
    db_session.add(legacy)
    db_session.flush()

    # Created: visible to its organisation in the same transaction, not to the other.
    copy = _copy("work_packages", legacy.id, org_a)
    assert copy is not None
    assert copy.name == "Created by an old screen"
    assert copy.organization_id == org_a.id
    assert copy.priority == "high"
    assert copy.id in {r.id for r in svc.query_for(org_a.id).all()}
    assert svc.query_for(org_b.id).filter_by(name="Created by an old screen").count() == 0
    assert svc.get_work_package(copy.id, org_b.id) is None
    assert legacy.retired_into_id == copy.id and legacy.retired_at is not None
    copy_id = copy.id

    # Edited: the copy follows.
    legacy.name = "Renamed by an old screen"
    legacy.status = "in_progress"
    db_session.flush()
    db_session.refresh(copy)
    assert copy.name == "Renamed by an old screen"
    assert copy.status == "in_progress"
    assert svc.query_for(org_b.id).filter_by(name="Renamed by an old screen").count() == 0

    # Deleted: the copy goes, through the one writer.
    db_session.delete(legacy)
    db_session.flush()
    assert svc.query_for(org_a.id).filter_by(id=copy_id).count() == 0


def test_every_retired_store_is_bridged(db_session, make_org):
    from app.models.implementation_migration import TechnologyRoadmapInitiative
    from app.models.implementation_planning import ImplementationWorkPackage
    from app.models.roadmap_models import RoadmapWorkPackage
    from tests.test_work_package_consolidation import _app_component, _architecture_model, _user as mkuser

    org = make_org("bridge-all")
    user = mkuser(db_session, org)
    component = _app_component(db_session, org)
    architecture = _architecture_model(db_session, org)

    roadmap = RoadmapWorkPackage(name="rm", business_capability="Cap", created_by=user.id,
                                 source_type="capability", auto_generated=True,
                                 confidence_score=0.4)
    implementation = ImplementationWorkPackage(name="impl", application_component_id=component.id)
    initiative = TechnologyRoadmapInitiative(name="tri", fiscal_year_start=2026,
                                             fiscal_year_end=2027, architecture_id=architecture.id)
    db_session.add_all([roadmap, implementation, initiative])
    db_session.flush()

    for table, row in (("roadmap_work_packages", roadmap),
                       ("implementation_work_packages", implementation),
                       ("technology_roadmap_initiatives", initiative)):
        copy = _copy(table, row.id, org)
        assert copy is not None, table
        assert copy.name == row.name
        assert copy.organization_id == org.id
        assert row.retired_into_id == copy.id
    rm_copy = _copy("roadmap_work_packages", roadmap.id, org)
    assert rm_copy.created_by == user.id and rm_copy.source_type == "capability"


def test_real_bridged_service_function_reaches_the_one_store(db_session, make_org, tenant_ctx):
    """GoalService still builds work packages in the older list; the bridge carries them across."""
    from app.models.motivation import Goal
    from app.modules.architecture.services.goal_service import GoalService
    from app.services import work_package_service as svc
    from tests.test_work_package_consolidation import _architecture_model

    org_a, org_b = make_org("bridge-goal-a"), make_org("bridge-goal-b")
    architecture = _architecture_model(db_session, org_a)
    goal = Goal(name="Grow revenue %s" % uuid.uuid4().hex[:6], architecture_id=architecture.id,
                organization_id=org_a.id)
    db_session.add(goal)
    db_session.flush()

    with tenant_ctx(org_a.id):
        created = GoalService().create_work_packages_from_goal(
            goal.id, work_package_descriptions=["Plan the quarter", "Ship the change"])
        assert len(created) == 2
        names = {r.name for r in svc.query_for(org_a.id).filter_by(goal_id=goal.id).all()}
    assert names == {"%s - Work Package 1" % goal.name, "%s - Work Package 2" % goal.name}
    assert svc.query_for(org_b.id).filter_by(goal_id=goal.id).count() == 0


def test_a_deleted_copy_is_not_recreated_by_a_later_edit(db_session, make_org):
    from app.models.implementation_migration import WorkPackage
    from app.services import work_package_service as svc

    org = make_org("bridge-tomb")
    legacy = WorkPackage(name="Tombstoned", organization_id=org.id)
    db_session.add(legacy)
    db_session.flush()
    copy = _copy("work_packages", legacy.id, org)
    svc.delete_work_package(copy.id, organization_id=org.id)

    legacy.name = "Edited after the copy was deleted"
    db_session.flush()
    assert _copy("work_packages", legacy.id, org) is None
    assert svc.query_for(org.id).filter_by(name="Edited after the copy was deleted").count() == 0


# -- the three repointed list screens ---------------------------------------


def _json(client, method, path, data=None):
    return getattr(client, method)(
        path, data=json.dumps(data or {}), content_type="application/json"
    )


@pytest.fixture
def screens(db_session, make_org):
    org_a, org_b = make_org("screen-a"), make_org("screen-b")
    return {
        "a": org_a,
        "b": org_b,
        "user_a": _user(db_session, org_a, "screena"),
        "user_b": _user(db_session, org_b, "screenb"),
    }


def test_enterprise_screen_lists_edits_and_deletes_by_unified_ids(
    db_session, screens, client, login_as
):
    from app.models.implementation_migration import WorkPackage
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.services import work_package_service as svc

    org_a, org_b = screens["a"], screens["b"]
    # An old-list row (merged or bridged) and a writer row, one organisation each.
    legacy = WorkPackage(name="Old list row", organization_id=org_a.id)
    db_session.add(legacy)
    db_session.flush()
    mine = svc.create_work_package(organization_id=org_a.id, name="Enterprise mine")
    theirs = svc.create_work_package(organization_id=org_b.id, name="Enterprise theirs")

    login_as(client, screens["user_a"])
    listing = client.get("/enterprise/api/work-packages?per_page=50").get_json()
    ids = {row["id"] for row in listing["work_packages"]}
    names = {row["name"] for row in listing["work_packages"]}
    assert mine.id in ids and "Old list row" in names and "Enterprise theirs" not in names
    assert listing["total"] == 2
    bridged = svc.get_by_source("work_packages", legacy.id, org_a.id)
    assert bridged.id in ids

    created = _json(client, "post", "/enterprise/api/work-packages", {
        "name": "Created on the enterprise screen", "summary": "text", "target_date": "2026-12-31",
        "percent_complete": 30, "togaf_phase": "B"})
    assert created.status_code == 201, created.get_data(as_text=True)
    new_id = created.get_json()["id"]
    row = UnifiedWorkPackage.query.get(new_id)
    assert row.organization_id == org_a.id and row.togaf_phase == "B"
    assert row.progress_percentage == 30 and row.end_date.date().isoformat() == "2026-12-31"

    patched = _json(client, "patch", "/enterprise/api/work-packages/%s" % new_id,
                    {"name": "Renamed on screen", "status": "in_progress"})
    assert patched.status_code == 200, patched.get_data(as_text=True)
    assert UnifiedWorkPackage.query.get(new_id).name == "Renamed on screen"

    assert _json(client, "patch", "/enterprise/api/work-packages/%s" % theirs.id,
                 {"name": "hijack"}).status_code == 404
    assert client.delete("/enterprise/api/work-packages/%s" % theirs.id).status_code == 404
    assert UnifiedWorkPackage.query.get(theirs.id).name == "Enterprise theirs"

    assert client.delete("/enterprise/api/work-packages/%s" % new_id).status_code == 200
    assert UnifiedWorkPackage.query.get(new_id) is None
    bulk = _json(client, "delete", "/enterprise/api/work-packages/bulk",
                 {"ids": [mine.id, theirs.id]})
    assert bulk.get_json() == {"deleted": 1}
    assert UnifiedWorkPackage.query.get(theirs.id) is not None


def test_capability_map_roadmap_screen_uses_unified_ids(screens, client, login_as):
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.services import work_package_service as svc

    org_a = screens["a"]
    theirs = svc.create_work_package(organization_id=screens["b"].id, name="Cap map theirs")
    login_as(client, screens["user_a"])

    created = _json(client, "post", "/capability-map/api/roadmap/work-packages", {
        "name": "Cap map mine", "status": "planned", "start_date": "2026-01-01",
        "target_date": "2026-02-01"})
    assert created.status_code == 201, created.get_data(as_text=True)
    new_id = created.get_json()["work_package"]["id"]
    assert UnifiedWorkPackage.query.get(new_id).organization_id == org_a.id

    listing = client.get("/capability-map/api/roadmap/work-packages?root_only=false").get_json()
    assert new_id in {w["id"] for w in listing["work_packages"]}
    assert "Cap map theirs" not in {w["name"] for w in listing["work_packages"]}
    assert listing["total_count"] == 1

    child = _json(client, "post", "/capability-map/api/roadmap/work-packages/%s/children" % new_id,
                  {"name": "Cap map child", "start_date": "2026-01-05", "target_date": "2026-01-20"})
    assert child.status_code == 200, child.get_data(as_text=True)
    child_id = child.get_json()["work_package"]["id"]
    roots = client.get("/capability-map/api/roadmap/work-packages").get_json()["work_packages"]
    assert [w["id"] for w in roots] == [new_id]
    assert [c["id"] for c in roots[0]["children"]] == [child_id]

    updated = _json(client, "put", "/capability-map/api/roadmap/work-packages/%s" % new_id,
                    {"name": "Cap map renamed", "percent_complete": 50})
    assert updated.status_code == 200, updated.get_data(as_text=True)
    assert UnifiedWorkPackage.query.get(new_id).name == "Cap map renamed"
    assert _json(client, "put", "/capability-map/api/roadmap/work-packages/%s" % theirs.id,
                 {"name": "hijack"}).status_code == 404
    assert client.get("/capability-map/api/roadmap/work-packages/%s" % theirs.id).status_code == 404
    assert client.delete("/capability-map/api/roadmap/work-packages/%s" % theirs.id).status_code == 404

    assert client.delete("/capability-map/api/roadmap/work-packages/%s" % new_id).status_code == 200
    assert UnifiedWorkPackage.query.get(new_id) is None
    assert UnifiedWorkPackage.query.get(child_id) is None  # cascade (default)


def test_roadmap_builder_screen_uses_unified_ids(screens, client, login_as):
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.services import work_package_service as svc

    org_a = screens["a"]
    theirs = svc.create_work_package(organization_id=screens["b"].id, name="Builder theirs")
    login_as(client, screens["user_a"])

    first = _json(client, "post", "/api/roadmap-builder/work-packages", {"name": "Builder one"})
    assert first.status_code == 201, first.get_data(as_text=True)
    first_id = first.get_json()["data"]["work_package"]["id"]
    second = _json(client, "post", "/api/roadmap-builder/work-packages",
                   {"name": "Builder two", "dependencies": [first_id]})
    assert second.status_code == 201, second.get_data(as_text=True)
    second_id = second.get_json()["data"]["work_package"]["id"]
    assert UnifiedWorkPackage.query.get(first_id).organization_id == org_a.id

    listing = client.get("/api/roadmap-builder/work-packages").get_json()["data"]
    assert listing["total"] == 2
    assert {w["id"] for w in listing["work_packages"]} == {first_id, second_id}

    detail = client.get("/api/roadmap-builder/work-packages/%s" % second_id).get_json()["data"]
    assert [d["id"] for d in detail["work_package"]["dependencies_detail"]] == [first_id]

    # a cycle is refused
    cyc = _json(client, "post", "/api/roadmap-builder/work-packages/%s/dependencies" % first_id,
                {"depends_on_id": second_id})
    assert cyc.status_code == 400 and cyc.get_json()["success"] is False

    assert _json(client, "put", "/api/roadmap-builder/work-packages/%s" % first_id,
                 {"name": "Builder renamed"}).status_code == 200
    assert UnifiedWorkPackage.query.get(first_id).name == "Builder renamed"
    assert _json(client, "put", "/api/roadmap-builder/work-packages/%s" % theirs.id,
                 {"name": "hijack"}).status_code == 404
    assert client.get("/api/roadmap-builder/work-packages/%s" % theirs.id).status_code == 404
    assert client.delete("/api/roadmap-builder/work-packages/%s" % theirs.id).status_code == 404

    assert client.delete("/api/roadmap-builder/work-packages/%s" % first_id).status_code == 200
    assert UnifiedWorkPackage.query.get(first_id) is None
    assert svc.dependency_ids(UnifiedWorkPackage.query.get(second_id)) == []


def test_unattributable_bridged_row_belongs_to_the_caller(db_session, make_org, tenant_ctx):
    """An older-list row with no programme, element or creator would be quarantined;
    written from a request it belongs to the organisation that made the request."""
    from app.models.implementation_planning import ImplementationWorkPackage
    from app.services import work_package_service as svc

    org_a, org_b = make_org("bridge-caller-a"), make_org("bridge-caller-b")
    with tenant_ctx(org_a.id):
        row = ImplementationWorkPackage(name="No links at all")
        db_session.add(row)
        db_session.flush()
        copy = _copy("implementation_work_packages", row.id, org_a)
    assert copy is not None and copy.organization_id == org_a.id
    assert svc.get_work_package(copy.id, org_b.id) is None
