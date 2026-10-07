"""R1-B04 PR 2 fix round 1: defects D-03 to D-13 of the review of PR 421.

Rows of the four retired stores are seeded with the session bridge suspended,
so the deploy merge itself is what these tests exercise; the bridge has its own
tests (test_work_package_bridge.py).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

ROOT = Path(__file__).resolve().parents[1]


# -- helpers ----------------------------------------------------------------


def _cli(app, *args):
    return app.test_cli_runner().invoke(args=list(args))


def _merge(app, *extra):
    result = _cli(app, "merge-work-package-stores", *extra)
    assert result.exit_code == 0, result.output
    return result.output


def _org_with_user(db_session, make_org, label):
    from tests.test_work_package_writer import _user

    org = make_org(label)
    return org, _user(db_session, org, label.replace("-", "")[:12])


def _programme(db_session, org, name):
    from tests.test_work_package_writer import _programme as make

    return make(db_session, org, name)


def _json(client, method, path, data=None):
    return getattr(client, method)(
        path, data=json.dumps(data or {}), content_type="application/json"
    )


def _enable_implementation_module(db_session):
    from app.models.feature_flags import FeatureFlag, FeatureState

    flag = FeatureFlag.query.filter_by(key="architecture_implementation_planning").first()
    if flag is None:
        db_session.add(FeatureFlag(
            key="architecture_implementation_planning",
            name="Architecture Implementation Planning",
            description="Implementation planning module", enabled=True,
            state=FeatureState.STABLE))
        db_session.flush()
    elif not flag.is_active:
        flag.enabled = True
        flag.state = FeatureState.STABLE
        db_session.flush()


def _legacy(db_session, org, name, **extra):
    from app.models.implementation_migration import WorkPackage

    row = WorkPackage(name=name, organization_id=org.id, **extra)
    db_session.add(row)
    db_session.flush()
    return row


def _copy(table, source_id, org):
    from app.services import work_package_service

    return work_package_service.get_by_source(table, source_id, org.id)


@pytest.fixture
def bridge_off():
    from app.services import work_package_bridge

    with work_package_bridge.suspended():
        yield


# -- D-03 -------------------------------------------------------------------


def test_deleted_work_package_stays_deleted_after_merge(
    app, db_session, make_org, bridge_off
):
    from app.models.implementation_migration import Deliverable
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "d03")
    legacy = _legacy(db_session, org, "Will be deleted")
    db_session.add(Deliverable(name="Old deliverable", work_package_id=legacy.id))
    db_session.flush()

    _merge(app)
    copy = _copy("work_packages", legacy.id, org)
    assert copy is not None
    assert Deliverable.query.filter_by(name="Old deliverable").one().unified_work_package_id == copy.id

    svc.delete_work_package(copy.id, organization_id=org.id)
    db_session.expire_all()
    legacy = db_session.get(type(legacy), legacy.id)
    assert legacy.retired_into_id is None, "ON DELETE SET NULL"
    assert legacy.retired_at is not None, "the tombstone survives"

    out = _merge(app)
    assert "work_packages: copied" not in out
    assert _copy("work_packages", legacy.id, org) is None
    assert svc.query_for(org.id).filter_by(name="Will be deleted").count() == 0
    assert Deliverable.query.filter_by(name="Old deliverable",
                                       unified_work_package_id=copy.id).count() == 0


# -- D-04 -------------------------------------------------------------------


def test_dependencies_remapped_to_unified_ids(app, db_session, make_org, client, login_as, bridge_off):
    from app.models.implementation_planning import ImplementationWorkPackage
    from app.models.roadmap_models import RoadmapWorkPackage, work_package_dependencies
    from app.services import work_package_service as svc
    from tests.test_work_package_consolidation import _app_component

    org, user = _org_with_user(db_session, make_org, "d04")
    prog_a, prog_b = _programme(db_session, org, "Alpha"), _programme(db_session, org, "Beta")
    # Shift the unified id sequence away from the legacy ids.
    for i in range(3):
        svc.create_work_package(organization_id=org.id, name="Offset %s" % i)

    needs = _legacy(db_session, org, "Needs the platform", enterprise_initiative_id=prog_a.id)
    platform = _legacy(db_session, org, "The platform", enterprise_initiative_id=prog_b.id)
    unrelated = _legacy(db_session, org, "Unrelated", enterprise_initiative_id=prog_b.id)
    needs.dependencies = [platform.id, 987654321]  # one id that never existed
    component = _app_component(db_session, org)
    impl_one = ImplementationWorkPackage(name="Impl one", application_component_id=component.id)
    impl_two = ImplementationWorkPackage(name="Impl two", application_component_id=component.id)
    road_one = RoadmapWorkPackage(name="Road one", business_capability="Cap", created_by=user.id)
    road_two = RoadmapWorkPackage(name="Road two", business_capability="Cap", created_by=user.id)
    db_session.add_all([impl_one, impl_two, road_one, road_two])
    db_session.flush()
    impl_one.work_dependencies = [impl_two.id]
    db_session.execute(work_package_dependencies.insert().values(
        work_package_id=road_one.id, dependency_id=road_two.id))
    db_session.flush()

    out = _merge(app)
    assert "dependencies dropped (no copy): 1" in out

    u_needs = _copy("work_packages", needs.id, org)
    u_platform = _copy("work_packages", platform.id, org)
    u_unrelated = _copy("work_packages", unrelated.id, org)
    assert svc.dependency_ids(u_needs) == [u_platform.id]
    blocked = svc.blocked_by_another_programme(org.id)
    assert [b["work_package"].id for b in blocked] == [u_needs.id]
    assert [x.id for x in blocked[0]["blocked_by"]] == [u_platform.id]
    assert u_unrelated.id not in {b["work_package"].id for b in blocked}
    assert svc.dependents_of(u_unrelated.id, org.id) == []

    assert svc.dependency_ids(_copy("implementation_work_packages", impl_one.id, org)) == [
        _copy("implementation_work_packages", impl_two.id, org).id]
    assert svc.dependency_ids(_copy("roadmap_work_packages", road_one.id, org)) == [
        _copy("roadmap_work_packages", road_two.id, org).id]

    # Roadmap DELETE of a work package nothing waits on succeeds.
    login_as(client, user)
    assert client.delete("/api/roadmap/work-packages/%s" % u_unrelated.id).status_code == 200

    before = {u.id: list(svc.dependency_ids(u)) for u in svc.query_for(org.id).all()}
    out = _merge(app)
    assert "changed=0" in out
    db_session.expire_all()
    after = {u.id: list(svc.dependency_ids(u)) for u in svc.query_for(org.id).all()}
    assert after == before


# -- D-05 -------------------------------------------------------------------


def _kanban_card(db_session, org, user, **extra):
    from app.models.adm_kanban import ADMPhase, KanbanBoard, KanbanCard

    phase = ADMPhase.query.filter_by(code="A").first()
    if phase is None:
        phase = ADMPhase(code="A", name="Architecture Vision", order=1)
        db_session.add(phase)
        db_session.flush()
    board = KanbanBoard(name="Board %s" % uuid.uuid4().hex[:6], created_by_id=user.id,
                        organization_id=org.id, current_adm_phase="A")
    db_session.add(board)
    db_session.flush()
    card = KanbanCard(title="Pushed card", board_id=board.id, adm_phase_id=phase.id,
                      card_type="implementation", created_by_id=user.id,
                      organization_id=org.id, status="backlog", priority="medium", **extra)
    db_session.add(card)
    db_session.flush()
    return card


def test_kanban_push_to_gantt_reuses_merged_work_package(
    app, db_session, make_org, client, login_as
):
    from app.models.roadmap_models import RoadmapWorkPackage
    from app.services import work_package_bridge, work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "d05")
    with work_package_bridge.suspended():
        road = RoadmapWorkPackage(name="Pushed before the change", business_capability="ADM Phase A",
                                  created_by=user.id)
        db_session.add(road)
        db_session.flush()
        card = _kanban_card(db_session, org, user, work_package_id=road.id)
    _merge(app)
    merged = _copy("roadmap_work_packages", road.id, org)
    assert merged is not None
    db_session.expire_all()
    assert card.unified_work_package_id == merged.id, "the merge backfills the card"

    # A card as it stood before the backfill still finds the row.
    card.unified_work_package_id = None
    db_session.flush()
    count = svc.query_for(org.id).count()

    login_as(client, user)
    resp = _json(client, "post", "/api/adm-kanban/v2/cards/task:%s/push-to-gantt" % card.id,
                 {"target_start_date": "2026-03-01", "target_end_date": "2026-04-01"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["updated"] is True and body["work_package_id"] == merged.id
    assert svc.query_for(org.id).count() == count
    db_session.expire_all()
    assert card.unified_work_package_id == merged.id
    assert svc.get_work_package(merged.id, org.id).end_date.date().isoformat() == "2026-04-01"

    # A card that was never pushed creates one row, and a second push reuses it.
    fresh = _kanban_card(db_session, org, user)
    first = _json(client, "post", "/api/adm-kanban/v2/cards/task:%s/push-to-gantt" % fresh.id, {})
    assert first.status_code == 201 and first.get_json()["updated"] is False
    second = _json(client, "post", "/api/adm-kanban/v2/cards/task:%s/push-to-gantt" % fresh.id, {})
    assert second.get_json()["work_package_id"] == first.get_json()["work_package_id"]
    assert svc.query_for(org.id).count() == count + 1


# -- D-06 / D-07 ------------------------------------------------------------


def test_deliverable_added_to_new_work_package(app, db_session, make_org, client, login_as):
    from app.models.implementation_migration import Deliverable
    from app.services import work_package_bridge, work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "d06")
    other_org, _other = _org_with_user(db_session, make_org, "d06x")
    fresh = svc.create_work_package(organization_id=org.id, name="Fresh, no legacy row")
    theirs = svc.create_work_package(organization_id=other_org.id, name="Their work package")
    with work_package_bridge.suspended():
        legacy = _legacy(db_session, org, "Carried over")
        db_session.add(Deliverable(name="Carried deliverable", work_package_id=legacy.id))
        db_session.flush()
    _merge(app)
    carried = _copy("work_packages", legacy.id, org)

    login_as(client, user)
    created = _json(client, "post", "/api/roadmap/deliverables",
                    {"name": "New deliverable", "work_package_id": fresh.id})
    assert created.status_code == 201, created.get_data(as_text=True)
    assert created.get_json()["deliverable"]["work_package_id"] == fresh.id
    row = Deliverable.query.filter_by(name="New deliverable").one()
    assert row.unified_work_package_id == fresh.id and row.work_package_id is None

    listed = client.get("/api/roadmap/deliverables?work_package_id=%s" % fresh.id).get_json()
    assert [d["name"] for d in listed["deliverables"]] == ["New deliverable"]
    old = client.get("/api/roadmap/deliverables?work_package_id=%s" % carried.id).get_json()
    assert [d["name"] for d in old["deliverables"]] == ["Carried deliverable"]

    # A merged row accepts one too, and keeps its older key.
    on_merged = _json(client, "post", "/api/roadmap/deliverables",
                      {"name": "On merged", "work_package_id": carried.id})
    assert on_merged.status_code == 201
    assert Deliverable.query.filter_by(name="On merged").one().work_package_id == legacy.id

    # Another organisation's work package is a 404 and nothing is written.
    refused = _json(client, "post", "/api/roadmap/deliverables",
                    {"name": "Sneaky", "work_package_id": theirs.id})
    assert refused.status_code == 404
    assert Deliverable.query.filter_by(name="Sneaky").count() == 0
    assert client.get("/api/roadmap/deliverables?work_package_id=%s" % theirs.id).status_code == 404


def test_implementation_deliverable_uses_unified_id_and_org(db_session, make_org, client, login_as):
    from app.models.implementation_migration import Deliverable
    from app.services import work_package_service as svc

    _enable_implementation_module(db_session)
    org_a, user_a = _org_with_user(db_session, make_org, "d07a")
    org_b, user_b = _org_with_user(db_session, make_org, "d07b")
    mine = svc.create_work_package(organization_id=org_a.id, name="A package")
    theirs = svc.create_work_package(organization_id=org_b.id, name="B package")
    svc.create_deliverable(theirs.id, organization_id=org_b.id, name="B deliverable")

    login_as(client, user_a)
    ok = _json(client, "post", "/implementation/api/deliverables",
               {"name": "A deliverable", "work_package_id": mine.id})
    assert ok.status_code == 200, ok.get_data(as_text=True)
    assert Deliverable.query.filter_by(name="A deliverable").one().unified_work_package_id == mine.id

    refused = _json(client, "post", "/implementation/api/deliverables",
                    {"name": "Planted in B", "work_package_id": theirs.id})
    assert refused.status_code == 404
    assert Deliverable.query.filter_by(name="Planted in B").count() == 0
    missing = _json(client, "post", "/implementation/api/deliverables", {"name": "No package"})
    assert missing.status_code == 400

    names = {d["name"] for d in client.get("/implementation/api/deliverables").get_json()["deliverables"]}
    assert names == {"A deliverable"}


# -- D-08 -------------------------------------------------------------------


def test_roadmap_automation_reads_unified_rows(db_session, make_org, client, login_as):
    from datetime import datetime

    from app.services import work_package_service as svc

    org_a, user_a = _org_with_user(db_session, make_org, "d08a")
    org_b, _user_b = _org_with_user(db_session, make_org, "d08b")
    common = dict(assigned_to="Team Overlap", start_date=datetime(2026, 1, 1),
                  end_date=datetime(2026, 3, 1))
    a1 = svc.create_work_package(organization_id=org_a.id, name="A one", **common)
    a2 = svc.create_work_package(organization_id=org_a.id, name="A two", **common)
    b1 = svc.create_work_package(organization_id=org_b.id, name="B one", **common)
    b2 = svc.create_work_package(organization_id=org_b.id, name="B two", **common)

    login_as(client, user_a)
    found = _json(client, "post", "/api/roadmap/automation/detect-conflicts",
                  {"work_package_ids": [a1.id, a2.id]}).get_json()
    assert found["conflict_count"] >= 1
    kinds = {c["conflict_type"] for c in found["conflicts"]}
    assert "timeline" in kinds and "resource" in kinds

    theirs = _json(client, "post", "/api/roadmap/automation/detect-conflicts",
                   {"work_package_ids": [b1.id, b2.id]}).get_json()
    assert theirs["conflict_count"] == 0

    optimised = _json(client, "post", "/api/roadmap/automation/optimize-timeline",
                      {"work_package_ids": [a1.id, a2.id]})
    assert optimised.status_code == 200, optimised.get_data(as_text=True)
    path = optimised.get_json()["optimized_timeline"]["critical_path"]
    assert {step["id"] for step in path} == {a1.id, a2.id}
    refused = _json(client, "post", "/api/roadmap/automation/optimize-timeline",
                    {"work_package_ids": [b1.id, b2.id]})
    assert refused.status_code == 404


# -- D-09 -------------------------------------------------------------------


def test_merge_verify_fails_on_unmerged_rows(app, db_session, make_org, bridge_off):
    org, _user = _org_with_user(db_session, make_org, "d09")
    _legacy(db_session, org, "Not copied yet")

    failed = _cli(app, "merge-work-package-stores", "--verify")
    assert failed.exit_code != 0
    assert "work_packages: " in failed.output and "unmerged" in failed.output
    assert "roadmap_work_packages: 0 unmerged" in failed.output

    _merge(app)
    ok = _cli(app, "merge-work-package-stores", "--verify")
    assert ok.exit_code == 0, ok.output

    script = (ROOT / "scripts" / "database" / "deploy-schema.sh").read_text()
    lines = [ln.strip() for ln in script.splitlines()
             if ln.strip().startswith("flask --app manage")
             and ("work-package" in ln)]
    assert len(lines) == 4, lines
    assert [ln for ln in lines if "||" in ln] == []
    assert lines[-1].endswith("merge-work-package-stores --verify")
    order = [ln.split("manage ", 1)[1] for ln in lines]
    assert order == ["backfill-work-package-org", "merge-work-package-stores",
                     "backfill-work-package-org", "merge-work-package-stores --verify"]


# -- D-10 -------------------------------------------------------------------


def test_merge_copies_created_by_and_roadmap_fields(app, db_session, make_org, bridge_off):
    from sqlalchemy import text

    from app.models.implementation_planning import ImplementationWorkPackage
    from app.models.roadmap_models import RoadmapWorkPackage, work_package_capabilities
    from app.models.unified_capability import UnifiedCapability
    from tests.test_work_package_consolidation import _app_component

    org, user = _org_with_user(db_session, make_org, "d10")
    capability = UnifiedCapability(name="Cap %s" % uuid.uuid4().hex[:6],
                                   code="C%s" % uuid.uuid4().hex[:8], level=1,
                                   scope="tenant", organization_id=org.id)
    db_session.add(capability)
    component = _app_component(db_session, org)
    road = RoadmapWorkPackage(
        name="Roadmap fields", business_capability="Cap", created_by=user.id,
        source_type="capability", source_id=77, source_data='{"k": 1}',
        auto_generated=True, confidence_score=0.4)
    impl = ImplementationWorkPackage(name="Impl creator", application_component_id=component.id,
                                     created_by=str(user.id))
    db_session.add_all([road, impl])
    db_session.flush()
    db_session.execute(work_package_capabilities.insert().values(
        work_package_id=road.id, capability_id=capability.id))
    db_session.flush()

    _merge(app)
    merged = _copy("roadmap_work_packages", road.id, org)
    assert merged.created_by == user.id
    assert merged.source_type == "capability"
    assert merged.source_id == road.id, "unified source_id keeps meaning the retired row's id"
    assert json.loads(merged.source_data) == {"k": 1, "origin_source_id": 77}
    assert merged.auto_generated is True and merged.confidence_score == 0.4
    assert merged.capability_ids == [capability.id]
    assert _copy("implementation_work_packages", impl.id, org).created_by == user.id

    # A row merged by the earlier version lacks them: the fill step supplies them.
    db_session.execute(text(
        "UPDATE unified_work_packages SET created_by = NULL, source_type = NULL, "
        "auto_generated = NULL, confidence_score = NULL, source_data = NULL, "
        "capability_ids = NULL, dependencies_remapped_at = NULL "
        "WHERE source_table IN ('roadmap_work_packages', 'implementation_work_packages') "
        "AND organization_id = :o"), {"o": org.id})
    db_session.expire_all()
    out = _merge(app)
    assert "filled created_by" in out and "filled source_data" in out
    merged = _copy("roadmap_work_packages", road.id, org)
    assert merged.created_by == user.id and merged.source_type == "capability"
    assert json.loads(merged.source_data) == {"k": 1, "origin_source_id": 77}
    assert merged.auto_generated is True and merged.confidence_score == 0.4
    assert merged.capability_ids == [capability.id]
    assert _copy("implementation_work_packages", impl.id, org).created_by == user.id

    assert "changed=0" in _merge(app)


# -- idempotency of the whole deploy sequence -------------------------------


def test_work_package_deploy_sequence_idempotent(app, db_session, make_org, bridge_off):
    from app.models.adm_kanban import KanbanCard  # noqa: F401  (registers the table)
    from app.models.implementation_migration import Deliverable
    from app.models.implementation_planning import ImplementationWorkPackage
    from app.models.roadmap_models import RoadmapWorkPackage
    from tests.test_work_package_consolidation import _app_component

    org, user = _org_with_user(db_session, make_org, "idem")
    component = _app_component(db_session, org)
    first = _legacy(db_session, org, "Seq one")
    second = _legacy(db_session, org, "Seq two")
    second.dependencies = [first.id]
    db_session.add(Deliverable(name="Seq deliverable", work_package_id=first.id))
    db_session.add(RoadmapWorkPackage(name="Seq road", business_capability="Cap",
                                      created_by=user.id, source_data="not json"))
    db_session.add(ImplementationWorkPackage(name="Seq impl",
                                             application_component_id=component.id))
    db_session.flush()

    def sequence():
        outputs = []
        for args in (("backfill-work-package-org",), ("merge-work-package-stores",),
                     ("backfill-work-package-org",), ("merge-work-package-stores", "--verify")):
            result = _cli(app, *args)
            assert result.exit_code == 0, (args, result.output)
            outputs.append(result.output)
        return outputs

    one = sequence()
    assert any(line.strip().startswith("+") for line in one[1].splitlines())
    two = sequence()
    for out in two:
        assert not [ln for ln in out.splitlines() if ln.strip().startswith("+")], out
    assert "changed=0" in two[1]
    assert "0 unmerged" in two[3]


# -- D-11 to D-13 -----------------------------------------------------------


def test_capability_roadmap_other_org_404(db_session, make_org, client, login_as):
    from app.services import work_package_service as svc

    org_a, user_a = _org_with_user(db_session, make_org, "d11a")
    org_b, _user_b = _org_with_user(db_session, make_org, "d11b")
    mine = svc.create_work_package(organization_id=org_a.id, name="Mine")
    theirs = svc.create_work_package(organization_id=org_b.id, name="Theirs")

    login_as(client, user_a)
    assert client.get("/api/capability-work-packages/%s/tasks" % mine.id).status_code == 200
    assert client.get("/api/capability-work-packages/%s/tasks" % theirs.id).status_code == 404
    assert _json(client, "post", "/api/capability-work-packages/%s/tasks" % theirs.id,
                 {"title": "planted"}).status_code == 404


def test_add_dependency_refuses_cycle(db_session, make_org, client, login_as):
    from app.services import work_package_service as svc

    _enable_implementation_module(db_session)
    org, user = _org_with_user(db_session, make_org, "d12a")
    one, two, three = (svc.create_work_package(organization_id=org.id, name="Cycle %s" % i)
                       for i in range(3))
    svc.add_dependency(one.id, two.id, organization_id=org.id)
    svc.add_dependency(two.id, three.id, organization_id=org.id)
    with pytest.raises(svc.WorkPackageError):
        svc.add_dependency(three.id, one.id, organization_id=org.id)
    assert svc.dependency_ids(three) == []
    # not a cycle
    svc.add_dependency(one.id, three.id, organization_id=org.id)

    login_as(client, user)
    refused = _json(client, "post", "/implementation/work-packages/%s/dependencies" % three.id,
                    {"dependency_id": one.id})
    assert refused.status_code == 400, refused.get_data(as_text=True)


def test_dependency_picker_filters_by_name(db_session, make_org, client, login_as):
    from app.services import work_package_service as svc

    _enable_implementation_module(db_session)
    org, user = _org_with_user(db_session, make_org, "d12b")
    main = svc.create_work_package(organization_id=org.id, name="Edited package")
    for i in range(60):
        svc.create_work_package(organization_id=org.id, name="Picker item %02d" % i)
    svc.create_work_package(organization_id=org.id, name="Zebra crossing upgrade")

    login_as(client, user)
    edit = "/implementation/work-packages/%s/edit" % main.id
    plain = client.get(edit).get_data(as_text=True)
    assert 0 < plain.count("Picker item") <= 50, "a bounded list, not a silent cap on everything"
    filtered = client.get(edit + "?q=zebra").get_data(as_text=True)
    assert "Zebra crossing upgrade" in filtered
    assert "Picker item" not in filtered


def test_roadmap_breakdown_matches_totals(db_session, make_org, client, login_as):
    from app.services import work_package_service as svc

    org_a, user_a = _org_with_user(db_session, make_org, "d13a")
    org_b, _user_b = _org_with_user(db_session, make_org, "d13b")
    svc.create_work_package(organization_id=org_a.id, name="One", status="planned",
                            priority="high", estimated_cost=100)
    svc.create_work_package(organization_id=org_a.id, name="Two", status="in_progress",
                            priority="low", estimated_cost=50)
    svc.create_work_package(organization_id=org_a.id, name="Three", status="in_progress",
                            priority="high", business_capability="Payments")
    for i in range(4):
        svc.create_work_package(organization_id=org_b.id, name="Other %s" % i, estimated_cost=999)

    login_as(client, user_a)
    stats = client.get("/api/roadmap/statistics").get_json()["work_packages"]
    assert stats["total"] == 3
    assert sum(stats["by_status"].values()) == 3
    assert sum(stats["by_priority"].values()) == 3
    assert stats["by_status"] == {"planned": 1, "in_progress": 2}
    assert stats["total_cost"] == 150

    _enable_implementation_module(db_session)
    login_as(client, user_a)
    data = client.get("/implementation/api/roadmap-data").get_json()
    domains = {i["name"]: i["domain_name"] for i in data["items"] if i["type"] == "work_package"}
    assert domains["Three"] == "Payments"
    assert domains["One"] != "Architecture"
