"""R1-B04 PR 2 fix round 3: defects N-01 to N-17 of the second review of PR 421.

Rows of the four retired stores are seeded with the session bridge suspended
where the deploy merge itself is under test; the bridge is exercised directly
where a test is about an old screen saving a row.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import uuid
import warnings
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


def _json(client, method, path, data=None):
    return getattr(client, method)(
        path, data=json.dumps(data or {}), content_type="application/json"
    )


def _org_with_user(db_session, make_org, label, role="enterprise_architect"):
    from tests.test_work_package_writer import _user

    org = make_org(label)
    return org, _user(db_session, org, label.replace("-", "")[:12], role=role)


def _legacy(db_session, org, name, **extra):
    from app.models.implementation_migration import WorkPackage

    row = WorkPackage(name=name, organization_id=org.id, **extra)
    db_session.add(row)
    db_session.flush()
    return row


def _copy(table, source_id, org):
    from app.services import work_package_service

    return work_package_service.get_by_source(table, source_id, org.id)


def _plateau(db_session, org, name="Target plateau"):
    from app.models.implementation_migration import Plateau

    row = Plateau(name="%s %s" % (name, uuid.uuid4().hex[:6]), organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _gap(db_session, org, name="Coverage gap"):
    from app.models.implementation_migration import Gap

    row = Gap(name="%s %s" % (name, uuid.uuid4().hex[:6]), organization_id=org.id,
              resolution_status="identified", impact="high", severity="high")
    db_session.add(row)
    db_session.flush()
    return row


def _relationships_of(wp):
    from app.models.models import ArchiMateRelationship

    return sorted(
        (r.type, r.target_id)
        for r in ArchiMateRelationship.query.filter_by(source_id=wp.archimate_element_id).all()
    )


@pytest.fixture
def bridge_off():
    from app.services import work_package_bridge

    with work_package_bridge.suspended():
        yield


# -- N-01: an old screen saves one field, the new screens' edits stay --------


def test_old_screen_edit_keeps_new_screen_edits(db_session, make_org):
    from app.services import work_package_service as svc

    org = make_org("n01-keep")
    legacy = _legacy(db_session, org, "Old name", status="planned", priority="medium")
    copy = _copy("work_packages", legacy.id, org)
    other = svc.create_work_package(organization_id=org.id, name="Made on a new screen")

    svc.update_work_package(copy.id, organization_id=org.id, name="Renamed on a new screen",
                            description="Described on a new screen")
    svc.add_dependency(copy.id, other.id, organization_id=org.id)
    db_session.flush()

    legacy.status = "in_progress"  # the old screen changes only the status
    db_session.flush()
    db_session.refresh(copy)

    assert copy.status == "in_progress"
    assert copy.name == "Renamed on a new screen"
    assert copy.description == "Described on a new screen"
    assert svc.dependency_ids(copy) == [other.id]


def test_old_dependency_removed_removes_only_that_one(db_session, make_org):
    from app.services import work_package_service as svc

    org = make_org("n01-deps")
    first = _legacy(db_session, org, "First")
    second = _legacy(db_session, org, "Second")
    third = _legacy(db_session, org, "Third")
    third.dependencies = [first.id, second.id]
    db_session.flush()
    u_first, u_second = _copy("work_packages", first.id, org), _copy("work_packages", second.id, org)
    u_third = _copy("work_packages", third.id, org)
    assert svc.dependency_ids(u_third) == [u_first.id, u_second.id]

    made_here = svc.create_work_package(organization_id=org.id, name="Added on a new screen")
    svc.add_dependency(u_third.id, made_here.id, organization_id=org.id)
    db_session.flush()

    third.dependencies = [first.id]  # the old screen removes the second one
    db_session.flush()
    db_session.refresh(u_third)
    assert sorted(svc.dependency_ids(u_third)) == sorted([u_first.id, made_here.id])

    third.dependencies = [first.id, second.id]  # and adds it back
    db_session.flush()
    db_session.refresh(u_third)
    assert sorted(svc.dependency_ids(u_third)) == sorted([u_first.id, made_here.id, u_second.id])


def test_old_edit_with_no_mapped_column_writes_nothing(db_session, make_org):
    from app.models.implementation_migration import TechnologyRoadmapInitiative
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "n01-none")
    arch = _architecture(db_session, org)
    row = TechnologyRoadmapInitiative(name="Initiative", fiscal_year_start=2026,
                                      fiscal_year_end=2027, architecture_id=arch.id)
    db_session.add(row)
    db_session.flush()
    copy = _copy("technology_roadmap_initiatives", row.id, org)
    svc.update_work_package(copy.id, organization_id=org.id, name="Edited here")
    db_session.flush()

    row.category = "Cloud"  # not a column of the one store
    db_session.flush()
    db_session.refresh(copy)
    assert copy.name == "Edited here"


def _architecture(db_session, org):
    from tests.test_work_package_consolidation import _architecture_model

    return _architecture_model(db_session, org)


# -- N-02: plateau and gap links are ArchiMate relationships -----------------


def test_plateau_and_gap_links_are_relationships(db_session, make_org, client, login_as):
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "n02-links")
    other_org = make_org("n02-other")
    plateau, plateau_two = _plateau(db_session, org), _plateau(db_session, org, "Second plateau")
    gap = _gap(db_session, org)
    theirs = _plateau(db_session, other_org, "Foreign plateau")

    wp = svc.create_work_package(organization_id=org.id, name="Linked", plateau_id=plateau.id,
                                 gap_id=gap.id)
    assert wp.plateau_id is None and wp.gap_id is None
    first = _relationships_of(wp)
    assert [kind for kind, _target in first] == ["association", "realization"]

    # Saving the same links again makes nothing new.
    svc.update_work_package(wp.id, organization_id=org.id, plateau_id=plateau.id, gap_id=gap.id)
    assert _relationships_of(wp) == first

    # Changing the plateau moves the relationship.
    svc.update_work_package(wp.id, organization_id=org.id, plateau_id=plateau_two.id)
    moved = _relationships_of(wp)
    assert len(moved) == 2 and moved != first
    links = svc.plateau_and_gap_links([wp], org.id)[wp.id]
    assert links == {"plateau_ids": [plateau_two.id], "gap_ids": [gap.id]}
    assert wp.plateau_id is None and wp.gap_id is None

    # to_dict and the roadmap screens show the links.
    assert svc.to_dict(wp)["plateau_ids"] == [plateau_two.id]
    assert svc.to_dict(wp)["gap_ids"] == [gap.id]
    shown = svc.to_roadmap_dict(wp, org.id)
    assert shown["gap_ids"] == [gap.id] and shown["plateau_ids"] == [plateau_two.id]
    login_as(client, user)
    one = client.get("/capability-map/api/roadmap/work-packages/%s" % wp.id).get_json()["work_package"]
    assert one["gap_ids"] == [gap.id] and one["plateau_ids"] == [plateau_two.id]
    listed = client.get("/capability-map/api/roadmap/work-packages?gap_id=%s&root_only=false" % gap.id).get_json()
    assert [w["id"] for w in listed["work_packages"]] == [wp.id]
    assert listed["work_packages"][0]["gap_ids"] == [gap.id]

    # Another organisation's plateau is a missing one.
    with pytest.raises(svc.WorkPackageNotFound):
        svc.update_work_package(wp.id, organization_id=org.id, plateau_id=theirs.id)
    with pytest.raises(svc.WorkPackageNotFound):
        svc.create_work_package(organization_id=org.id, name="Sneaky", plateau_id=theirs.id)
    assert svc.query_for(org.id).filter_by(name="Sneaky").count() == 0
    login_as(client, user)
    resp = _json(client, "put", "/capability-map/api/roadmap/work-packages/%s" % wp.id, {"plateau_id": theirs.id})
    assert resp.status_code == 404
    assert svc.plateau_and_gap_links([wp], org.id)[wp.id]["plateau_ids"] == [plateau_two.id]

    # Clearing the plateau removes the relationship and keeps the gap link.
    svc.update_work_package(wp.id, organization_id=org.id, plateau_id=None)
    assert svc.plateau_and_gap_links([wp], org.id)[wp.id] == {
        "plateau_ids": [], "gap_ids": [gap.id]}

    # The other organisation never sees these links.
    assert svc.plateau_and_gap_links([wp], other_org.id)[wp.id] == {"plateau_ids": [], "gap_ids": []}
    assert svc.work_package_ids_for_gap(gap.id, other_org.id) == []


def test_merge_turns_link_columns_into_relationships(app, db_session, make_org, bridge_off):
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.services import work_package_service as svc

    org = make_org("n02-merge")
    other_org = make_org("n02-merge-other")
    plateau, gap = _plateau(db_session, org), _gap(db_session, org)
    foreign = _plateau(db_session, other_org)
    mine = UnifiedWorkPackage(name="Carries link columns", organization_id=org.id,
                              plateau_id=plateau.id, gap_id=gap.id)
    stray = UnifiedWorkPackage(name="Points at another organisation", organization_id=org.id,
                               plateau_id=foreign.id)
    db_session.add_all([mine, stray])
    db_session.flush()
    db_session.commit()

    out = _merge(app)
    assert "plateau relationships created: 1" in out
    assert "gap relationships created: 1" in out
    assert "link not migrated" in out
    db_session.expire_all()
    assert svc.plateau_and_gap_links([mine], org.id)[mine.id] == {
        "plateau_ids": [plateau.id], "gap_ids": [gap.id]}
    assert svc.plateau_and_gap_links([stray], org.id)[stray.id]["plateau_ids"] == []
    assert mine.plateau_id == plateau.id, "the columns themselves are left as they were"

    count = len(_relationships_of(mine))
    out = _merge(app)
    assert "relationships created" not in out
    assert len(_relationships_of(mine)) == count

    dry = _merge(app, "--dry-run")
    assert "relationships created" not in dry


def test_writer_no_longer_has_plateau_or_gap_columns():
    from app.services import work_package_service as svc

    assert "plateau_id" not in svc._EDITABLE and "gap_id" not in svc._EDITABLE


# -- N-04, N-14: one deliverable store ---------------------------------------


def test_one_deliverable_store(app, db_session, make_org, client, login_as, bridge_off):
    from sqlalchemy.exc import SAWarning

    from app.models.implementation_migration import Deliverable
    from app.models.roadmap_models import RoadmapDeliverable, RoadmapWorkPackage
    from app.services import work_package_service as svc
    from tests.test_work_package_writer_ratchet import _constructor_counts

    org, user = _org_with_user(db_session, make_org, "n04-store")
    other_org, other_user = _org_with_user(db_session, make_org, "n04-other")
    wp = svc.create_work_package(organization_id=org.id, name="With deliverables")
    theirs = svc.create_work_package(organization_id=other_org.id, name="Theirs")
    db_session.flush()

    # The capability roadmap screen reads and writes `deliverables`.
    login_as(client, user)
    base = "/api/capability-work-packages/%s/deliverables" % wp.id
    made = _json(client, "post", base, {
        "name": "Design", "due_date": "2026-03-01", "status": "planned",
        "approval_criteria": "Signed off", "deliverable_type": "document"})
    assert made.status_code == 201, made.get_data(as_text=True)
    created = made.get_json()["deliverable"]
    rows = Deliverable.query.filter_by(unified_work_package_id=wp.id).all()
    assert [r.name for r in rows] == ["Design"]
    assert rows[0].target_date.isoformat() == "2026-03-01"
    assert rows[0].approval_criteria == "Signed off"
    assert RoadmapDeliverable.query.filter_by(unified_work_package_id=wp.id).count() == 0
    assert created["status"] == "planned" and created["due_date"] == "2026-03-01"

    updated = _json(client, "put", base + "/%s" % created["id"], {
        "status": "delivered", "approval_status": "approved", "quality_score": 4.5,
        "review_date": "2026-03-05", "related_task_ids": "[1, 2]"})
    assert updated.status_code == 200, updated.get_data(as_text=True)
    db_session.refresh(rows[0])
    assert (rows[0].delivery_status, rows[0].approval_status, rows[0].quality_score) == (
        "delivered", "approved", 4.5)
    assert rows[0].review_date is not None and rows[0].related_task_ids == "[1, 2]"
    listed = client.get(base).get_json()
    assert [d["status"] for d in listed["deliverables"]] == ["delivered"]
    details = client.get("/api/capability-work-packages/%s/details" % wp.id).get_json()
    assert details["statistics"]["delivered_count"] == 1
    assert details["statistics"]["approved_count"] == 1

    # Another organisation's work package and deliverable answer as missing.
    assert client.get("/api/capability-work-packages/%s/deliverables" % theirs.id).status_code == 404
    login_as(client, other_user)
    assert _json(client, "put", base + "/%s" % created["id"], {"name": "x"}).status_code == 404
    assert client.delete(base + "/%s" % created["id"]).status_code == 404
    login_as(client, user)
    assert client.delete(base + "/%s" % created["id"]).status_code == 200
    assert Deliverable.query.filter_by(unified_work_package_id=wp.id).count() == 0

    # roadmap_deliverables rows are copied once, and their sources are marked.
    roadmap = RoadmapWorkPackage(name="Roadmap row", business_capability="Cap", created_by=user.id)
    db_session.add(roadmap)
    db_session.flush()
    with_unified = RoadmapDeliverable(
        name="Old, keyed on the unified id", organization_id=org.id,
        unified_work_package_id=wp.id, status="delivered", approval_status="approved")
    from tests.test_work_package_consolidation import _app_component

    component = _app_component(db_session, org)
    by_roadmap = RoadmapDeliverable(
        name="Old, keyed on the roadmap row", organization_id=org.id,
        work_package_id=roadmap.id, status="planned", source_application_id=component.id)
    db_session.add_all([with_unified, by_roadmap])
    db_session.flush()
    db_session.commit()
    out = _merge(app)
    assert "roadmap_deliverables: copied: 2" in out
    copies = Deliverable.query.filter_by(name="Old, keyed on the unified id").all()
    assert len(copies) == 1 and copies[0].unified_work_package_id == wp.id
    assert copies[0].delivery_status == "delivered" and copies[0].approval_status == "approved"
    roadmap_copy = _copy("roadmap_work_packages", roadmap.id, org)
    second = Deliverable.query.filter_by(name="Old, keyed on the roadmap row").one()
    assert second.unified_work_package_id == roadmap_copy.id
    assert second.application_component_id == component.id
    db_session.expire_all()
    source = db_session.get(RoadmapDeliverable, with_unified.id)
    assert source.retired_into_id == copies[0].id and source.retired_at is not None
    out = _merge(app)
    assert "roadmap_deliverables: copied" not in out
    assert Deliverable.query.filter_by(name="Old, keyed on the unified id").count() == 1

    # Deleting a work package removes its deliverables, once, with no warning.
    legacy = _legacy(db_session, org, "Old-store work package")
    mirror = svc.create_work_package(organization_id=org.id, name="Mirror to delete")
    db_session.add(Deliverable(name="D1", unified_work_package_id=mirror.id))
    db_session.add(Deliverable(name="D2", unified_work_package_id=mirror.id,
                               work_package_id=legacy.id))
    db_session.flush()
    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        svc.delete_work_package(mirror.id, organization_id=org.id)
        db_session.flush()
    assert Deliverable.query.filter_by(unified_work_package_id=mirror.id).count() == 0

    # The ratchet: nothing outside the consolidation command builds a RoadmapDeliverable.
    assert _constructor_counts()[2] == {}


def test_old_screen_delete_with_deliverables_raises_no_warning(db_session, make_org):
    from sqlalchemy.exc import SAWarning

    from app.models.implementation_migration import Deliverable
    from app.services import work_package_service as svc

    org = make_org("n14-double")
    legacy = _legacy(db_session, org, "Deleted on an old screen")
    copy = _copy("work_packages", legacy.id, org)
    db_session.add(Deliverable(name="Both keys", work_package_id=legacy.id,
                               unified_work_package_id=copy.id))
    db_session.flush()
    copy_id = copy.id
    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        db_session.delete(legacy)
        db_session.flush()
    assert svc.query_for(org.id).filter_by(id=copy_id).count() == 0
    assert Deliverable.query.filter_by(unified_work_package_id=copy_id).count() == 0


def test_orchestrator_deliverables_belong_to_the_copy(db_session, make_org):
    from app.models.implementation_migration import Deliverable
    from app.models.roadmap_models import RoadmapWorkPackage
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "n04-orch")
    row = RoadmapWorkPackage(name="Acme onboarding", business_capability="Vendor Management",
                             created_by=user.id)
    db_session.add(row)
    db_session.flush()
    copy = _copy("roadmap_work_packages", row.id, org)
    made = svc.create_deliverables_for_roadmap_copy(row, [
        {"name": "Contract", "delivery_status": "pending", "target_date": row.created_at}])
    assert [d.unified_work_package_id for d in made] == [copy.id]
    assert Deliverable.query.filter_by(unified_work_package_id=copy.id).count() == 1


# -- N-05: rows written outside a request ------------------------------------


def test_bridge_out_of_request_uses_job_tenant(db_session, make_org, caplog):
    from flask import g

    from app.jobs.tenant_safe_job import tenant_scope
    from app.models.roadmap_models import RoadmapWorkPackage
    from app.models.unified_work_package import UnifiedWorkPackage

    org = make_org("n05-job")
    org_id = org.id
    db_session.commit()
    with tenant_scope(org_id):
        row = RoadmapWorkPackage(name="Made by a scheduled job", business_capability="Cap")
        db_session.add(row)
        db_session.flush()
        row_id = row.id
        copy = UnifiedWorkPackage.query.filter_by(
            source_table="roadmap_work_packages", source_id=row_id).one()
        assert copy.organization_id == org_id

    # No tenant context at all: the copy is quarantined and the gap is logged.
    assert getattr(g, "current_org_id", None) is None
    with caplog.at_level(logging.WARNING):
        orphan = RoadmapWorkPackage(name="Made with no tenant", business_capability="Cap")
        db_session.add(orphan)
        db_session.flush()
    assert any("has no organisation" in r.getMessage() and "roadmap_work_packages" in r.getMessage()
               for r in caplog.records)


def test_verify_fails_on_attributable_null_org_copy(app, db_session, make_org, bridge_off):
    from tests.test_work_package_consolidation import _enterprise_initiative, _unified_wp

    org = make_org("n05-verify")
    make_org("n05-verify-second")  # with one organisation the column default fills it in
    programme = _enterprise_initiative(db_session, org)
    _unified_wp(db_session, name="Copied, never attributed", source_table="work_packages",
                source_id=987001, enterprise_initiative_id=programme.id, organization_id=None)
    db_session.commit()

    failed = _cli(app, "merge-work-package-stores", "--verify")
    assert failed.exit_code == 1, failed.output
    assert "1 copied row(s) with no organisation that the attribution chain would place" in failed.output

    assert _cli(app, "backfill-work-package-org").exit_code == 0
    ok = _cli(app, "merge-work-package-stores", "--verify")
    assert ok.exit_code == 0, ok.output

    # A copy nothing can place is quarantined: reported, and not a failure.
    _unified_wp(db_session, name="Unplaceable", source_table="work_packages", source_id=987002,
                organization_id=None)
    db_session.commit()
    quarantined = _cli(app, "merge-work-package-stores", "--verify")
    assert quarantined.exit_code == 0, quarantined.output
    assert "unattributable (quarantined; information only)" in quarantined.output
    assert " 1 copied row(s) unattributable" in quarantined.output.replace("  ", " ")


# -- N-06, N-13: aliases and create-from-gap ---------------------------------


def test_create_from_gap_returns_unified_id(db_session, make_org, client, login_as, monkeypatch):
    from app import db
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "n13-gap")
    gap = _gap(db_session, org)
    db_session.commit()

    login_as(client, user)
    resp = _json(client, "post", "/capability-map/api/roadmap/gaps/%s/work-packages" % gap.id,
                 {"name": "Close it"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    shown = resp.get_json()["work_package"]
    row = UnifiedWorkPackage.query.get(shown["id"])
    assert row is not None and row.name == "Close it" and row.organization_id == org.id

    # When the older store cannot be saved, the writer makes the row and links the gap.
    from app.services.gap_archimate_service import gap_archimate_service

    real_commit = db.session.commit
    real_create = gap_archimate_service.create_work_package_for_gap
    armed = {"on": False}

    def create_then_arm(*args, **kwargs):
        made = real_create(*args, **kwargs)
        armed["on"] = True
        return made

    def failing_once():
        if armed["on"]:
            armed["on"] = False
            raise RuntimeError("column missing")
        return real_commit()

    login_as(client, user)
    monkeypatch.setattr(gap_archimate_service, "create_work_package_for_gap", create_then_arm)
    monkeypatch.setattr(db.session, "commit", failing_once)
    resp = _json(client, "post", "/capability-map/api/roadmap/gaps/%s/work-packages" % gap.id,
                 {"name": "Fallback row"})
    monkeypatch.undo()
    assert resp.status_code == 200, resp.get_data(as_text=True)
    shown = resp.get_json()["work_package"]
    row = UnifiedWorkPackage.query.get(shown["id"])
    assert row is not None and row.name == "Fallback row" and row.source_table is None
    assert svc.plateau_and_gap_links([row], org.id)[row.id]["gap_ids"] == [gap.id]
    assert shown["gap_ids"] == [gap.id]


# -- N-07: foreign keys in production ---------------------------------------


def _scratch_url():
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["TEST_DATABASE_URL"])
    return url, url.set(database=url.database + "_reconcile")


def _flask(url, *args):
    env = dict(os.environ, TEST_DATABASE_URL=url.render_as_string(hide_password=False),
               DATABASE_URL=url.render_as_string(hide_password=False), FLASK_CONFIG="testing",
               SECRET_KEY=os.environ.get("SECRET_KEY", "ci-only-not-secret"))
    return subprocess.run(
        [sys.executable, "-m", "flask", "--app", "manage", *args],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=1500)


def test_reconcile_added_columns_get_foreign_keys():
    """Production never runs create_all: reconcile-schema adds these columns as plain
    nullable ones. Build a scratch database with the deploy schema sequence, remove
    the columns, let reconcile-schema add them back, then run the consolidation."""
    import sqlalchemy as sa

    main_url, scratch = _scratch_url()
    admin = sa.create_engine(main_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(sa.text('DROP DATABASE IF EXISTS "%s" WITH (FORCE)' % scratch.database))
            conn.execute(sa.text('CREATE DATABASE "%s"' % scratch.database))
    except Exception as exc:  # pragma: no cover - no rights to make a scratch database
        pytest.skip("cannot create the scratch database: %s" % exc)

    for step in ("init-db", "schema-upgrade", "reconcile-schema"):
        done = _flask(scratch, step)
        assert done.returncode == 0, (step, done.stdout[-1500:], done.stderr[-1500:])

    columns = (
        ("unified_work_packages", "parent_id"),
        ("deliverables", "unified_work_package_id"),
        ("kanban_cards", "unified_work_package_id"),
        ("work_packages", "retired_into_id"),
        ("roadmap_work_packages", "retired_into_id"),
        ("implementation_work_packages", "retired_into_id"),
        ("technology_roadmap_initiatives", "retired_into_id"),
        ("roadmap_deliverables", "retired_into_id"),
    )
    engine = sa.create_engine(scratch)
    try:
        with engine.begin() as conn:
            for table, column in columns:
                conn.execute(sa.text('ALTER TABLE "%s" DROP COLUMN "%s"' % (table, column)))
        added = _flask(scratch, "reconcile-schema")
        assert added.returncode == 0, added.stdout[-1500:]
        for table, column in columns:
            assert "%s.%s" % (table, column) in added.stdout, (table, column)
        def foreign_keys(conn):
            return {
                (row[0], row[1]): (row[2], row[3]) for row in conn.execute(sa.text(
                    "SELECT c.conrelid::regclass::text, a.attname, c.confrelid::regclass::text, "
                    "c.confdeltype::text FROM pg_constraint c JOIN pg_attribute a "
                    "ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) WHERE c.contype = 'f'"))
            }

        with engine.connect() as conn:
            present = foreign_keys(conn)
            assert not [pair for pair in columns if pair in present], (
                "a reconcile-added column has no foreign key")
            # Production data: a child pointing at a parent that no longer exists.
        with engine.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO unified_work_packages (id, name, context, scope, parent_id) "
                "VALUES (9101, 'Orphaned child', 'architecture', 'enterprise', 999999)"))

        merged = _flask(scratch, "merge-work-package-stores")
        assert merged.returncode == 0, merged.stdout[-2000:] + merged.stderr[-1500:]
        assert "unified_work_packages.parent_id: orphan values set to NULL: 1" in merged.stdout

        expected = {
            ("unified_work_packages", "parent_id"): ("unified_work_packages", "n"),
            ("deliverables", "unified_work_package_id"): ("unified_work_packages", "c"),
            ("kanban_cards", "unified_work_package_id"): ("unified_work_packages", "n"),
            ("work_packages", "retired_into_id"): ("unified_work_packages", "n"),
            ("roadmap_work_packages", "retired_into_id"): ("unified_work_packages", "n"),
            ("implementation_work_packages", "retired_into_id"): ("unified_work_packages", "n"),
            ("technology_roadmap_initiatives", "retired_into_id"): ("unified_work_packages", "n"),
            ("roadmap_deliverables", "retired_into_id"): ("deliverables", "n"),
        }
        with engine.connect() as conn:
            found = foreign_keys(conn)
            assert {pair: found.get(pair) for pair in expected} == expected, found
            assert conn.execute(sa.text(
                "SELECT parent_id FROM unified_work_packages WHERE id = 9101")).scalar() is None

        # Deleting a parent leaves its child at root level.
        with engine.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO unified_work_packages (id, name, context, scope) "
                "VALUES (9102, 'Parent', 'architecture', 'enterprise')"))
            conn.execute(sa.text(
                "INSERT INTO unified_work_packages (id, name, context, scope, parent_id) "
                "VALUES (9103, 'Child', 'architecture', 'enterprise', 9102)"))
            conn.execute(sa.text("DELETE FROM unified_work_packages WHERE id = 9102"))
            assert conn.execute(sa.text(
                "SELECT parent_id FROM unified_work_packages WHERE id = 9103")).scalar() is None

        again = _flask(scratch, "merge-work-package-stores")
        assert again.returncode == 0 and "changed=0" in again.stdout, again.stdout[-1500:]
    finally:
        engine.dispose()
        admin.dispose()


def test_deleting_a_parent_re_parents_its_children(db_session, make_org, client, login_as):
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "n07-parent")
    parent = svc.create_work_package(organization_id=org.id, name="Parent")
    child = svc.create_work_package(organization_id=org.id, name="Child", parent_id=parent.id)
    db_session.flush()
    svc.delete_work_package(parent.id, organization_id=org.id)
    db_session.flush()
    db_session.refresh(child)
    assert child.parent_id is None
    login_as(client, user)
    roots = client.get("/capability-map/api/roadmap/work-packages").get_json()["work_packages"]
    assert child.id in {w["id"] for w in roots}


# -- N-08: statistics ----------------------------------------------------------


def test_roadmap_stats_deliverables_are_org_scoped(db_session, make_org, client, login_as):
    from app.services import work_package_service as svc

    org_a, user_a = _org_with_user(db_session, make_org, "n08-a")
    org_b = make_org("n08-b")
    mine = svc.create_work_package(organization_id=org_a.id, name="A")
    theirs = svc.create_work_package(organization_id=org_b.id, name="B")
    svc.create_deliverable(mine.id, organization_id=org_a.id, name="A1", delivery_status="planned")
    for i in range(3):
        svc.create_deliverable(theirs.id, organization_id=org_b.id, name="B%s" % i,
                               delivery_status="delivered")
    db_session.flush()

    login_as(client, user_a)
    stats = client.get("/api/roadmap/statistics").get_json()["deliverables"]
    assert stats["total"] == 1
    assert stats["by_status"] == {"planned": 1}


# -- N-09: AI orchestrator ----------------------------------------------------


def test_ai_orchestrator_creates_one_element(db_session, make_org, tenant_ctx):
    from app.models.archimate_core import ArchiMateElement
    from app.models.solution_models import Solution, SolutionArchiMateElement
    from app.modules.solutions_strategic.v2.services.solution_ai_orchestrator import (
        SolutionAIOrchestrator,
    )

    org, user = _org_with_user(db_session, make_org, "n09-ai")
    solution = Solution(name="Orchestrated %s" % uuid.uuid4().hex[:6], organization_id=org.id)
    db_session.add(solution)
    db_session.flush()
    long_name = "A very long work package name " * 6
    parsed = {
        "gaps": [{"name": "Capability gap", "description": "d"}],
        "work_packages": [
            {"name": long_name, "gap_name": "Capability gap"},
            {"name": "Same name", "description": "one"},
            {"name": "Same name", "description": "two"},
        ],
    }
    with tenant_ctx(org.id):
        created = SolutionAIOrchestrator()._create_implementation_entities(
            solution, parsed, [], user.id)
        db_session.flush()
        assert created["work_packages"] == 3, created
        elements = ArchiMateElement.query.filter_by(organization_id=org.id, type="WorkPackage").all()
        assert len(elements) == 3, [e.name for e in elements]
        linked = SolutionArchiMateElement.query.filter_by(solution_id=solution.id).all()
        assert {r.element_id for r in linked} >= {e.id for e in elements}
        gap_elements = ArchiMateElement.query.filter_by(organization_id=org.id, type="Gap").all()
        assert len(gap_elements) == 1


# -- N-10: long status -------------------------------------------------------


def test_long_roadmap_status_saves_and_merges(app, db_session, make_org, bridge_off):
    from app.models.roadmap_models import RoadmapWorkPackage
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "n10-status")
    status = "waiting_for_the_steering_committee_decision_x"  # 44 characters
    assert 40 < len(status) <= 50

    made = svc.create_work_package(organization_id=org.id, name="Long status", status=status)
    assert made.status == status
    with pytest.raises(svc.WorkPackageError):
        svc.create_work_package(organization_id=org.id, name="Too long", status="x" * 51)
    svc.delete_work_package(made.id, organization_id=org.id)
    db_session.flush()

    # A database that still has the narrow column: the merge widens it first.
    import sqlalchemy as sa

    db_session.execute(sa.text("ALTER TABLE unified_work_packages ALTER COLUMN status TYPE varchar(30)"))
    row = RoadmapWorkPackage(name="Long roadmap status", business_capability="Cap",
                             created_by=user.id, status=status)
    db_session.add(row)
    db_session.flush()
    db_session.commit()
    out = _merge(app)
    assert "unified_work_packages.status: widened to 50" in out
    width = db_session.execute(sa.text(
        "SELECT character_maximum_length FROM information_schema.columns "
        "WHERE table_name = 'unified_work_packages' AND column_name = 'status'")).scalar()
    assert width == 50
    assert _copy("roadmap_work_packages", row.id, org).status == status


# -- N-11: one copy per source row --------------------------------------------


def test_duplicate_copy_refused_by_unique_index(app, db_session, make_org, bridge_off):
    import sqlalchemy as sa
    from sqlalchemy.exc import IntegrityError

    from tests.test_work_package_consolidation import _unified_wp

    org = make_org("n11-unique")
    _unified_wp(db_session, name="First copy", organization_id=org.id,
                source_table="work_packages", source_id=881001)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            _unified_wp(db_session, name="Second copy", organization_id=org.id,
                        source_table="work_packages", source_id=881001)
    # Rows that are not copies may share a source_id (it means something else there).
    _unified_wp(db_session, name="Generated 1", organization_id=org.id, source_id=77)
    _unified_wp(db_session, name="Generated 2", organization_id=org.id, source_id=77)

    # The copy insert skips a row another writer already copied.
    legacy = _legacy(db_session, org, "Raced")
    from app.commands.consolidate_work_packages import sync_source_rows

    _unified_wp(db_session, name="Winner", organization_id=org.id, source_table="work_packages",
                source_id=legacy.id)
    stats = sync_source_rows(db_session.connection(), "work_packages", [legacy.id])
    assert stats.get("work_packages: copied") is None
    assert db_session.execute(sa.text(
        "SELECT count(*) FROM unified_work_packages WHERE source_table = 'work_packages' "
        "AND source_id = :i"), {"i": legacy.id}).scalar() == 1
    db_session.commit()

    # The command refuses, and does not choose, when duplicates exist.
    db_session.execute(sa.text("DROP INDEX uq_unified_wp_source_copy"))
    _unified_wp(db_session, name="Loser", organization_id=org.id, source_table="work_packages",
                source_id=legacy.id)
    db_session.commit()
    refused = _cli(app, "merge-work-package-stores")
    assert refused.exit_code == 1
    assert "work_packages id %s: 2 copies" % legacy.id in refused.output
    assert db_session.execute(sa.text(
        "SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_unified_wp_source_copy'")).scalar() == 0


# -- N-12: kanban push --------------------------------------------------------


def test_kanban_push_writer_error_is_400(db_session, make_org, client, login_as, monkeypatch):
    from app.services import work_package_service as svc
    from tests.test_work_package_fix_round1 import _kanban_card

    org, user = _org_with_user(db_session, make_org, "n12-push")
    card = _kanban_card(db_session, org, user)

    def refuse(*_args, **_kwargs):
        raise svc.WorkPackageError("Name is required.")

    monkeypatch.setattr(svc, "create_work_package", refuse)
    login_as(client, user)
    resp = _json(client, "post", "/api/adm-kanban/v2/cards/task:%s/push-to-gantt" % card.id, {})
    assert resp.status_code == 400
    assert resp.get_json() == {"success": False, "error": "Name is required."}


# -- N-15: ArchiMate roadmap plateau counts ------------------------------------


def test_archimate_roadmap_plateau_counts_include_new_work_packages(
    app, db_session, make_org, client, login_as
):
    from flask import template_rendered

    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "n15-count")
    other_org = make_org("n15-other")
    busy, empty = _plateau(db_session, org, "Busy"), _plateau(db_session, org, "Empty")
    svc.create_work_package(organization_id=org.id, name="New one", plateau_id=busy.id)
    svc.create_work_package(organization_id=org.id, name="New two", plateau_id=busy.id)
    svc.create_work_package(organization_id=other_org.id, name="Not mine",
                            plateau_id=_plateau(db_session, other_org).id)
    legacy = _legacy(db_session, org, "Old list", plateau_id=busy.id)
    assert _copy("work_packages", legacy.id, org) is not None
    db_session.flush()

    counts = svc.plateau_work_package_ids([busy.id, empty.id], org.id)
    assert len(counts[busy.id]) == 3 and counts[empty.id] == set()

    seen = []

    def capture(sender, template, context, **extra):
        seen.append(context)

    template_rendered.connect(capture, app)
    try:
        login_as(client, user)
        assert client.get("/archimate-roadmap").status_code == 200
    finally:
        template_rendered.disconnect(capture, app)
    plateaus = {p["id"]: p for ctx in seen for p in ctx.get("plateaus", [])}
    assert plateaus[busy.id]["work_package_count"] == 3
    assert plateaus[empty.id]["work_package_count"] == 0


# -- N-17: enterprise create keeps every field -----------------------------------


def test_enterprise_create_keeps_every_field(db_session, make_org, client, login_as):
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "n17-fields")
    architecture = _architecture(db_session, org)
    login_as(client, user)
    resp = _json(client, "post", "/enterprise/api/work-packages", {
        "name": "Every field", "summary": "The short summary", "description": "The long description",
        "estimated_effort_hours": 120, "actual_effort_hours": 30, "architecture_id": architecture.id,
        "level": 2, "color": "#3B82F6"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    row = svc.get_work_package(resp.get_json()["id"], org.id)
    assert (row.summary, row.description) == ("The short summary", "The long description")
    assert (row.estimated_effort_hours, row.actual_effort_hours) == (120, 30)
    assert row.context_id == architecture.id
    assert (row.level, row.color) == (2, "#3B82F6")
    shown = svc.to_roadmap_dict(row, org.id)
    assert shown["summary"] == "The short summary" and shown["description"] == "The long description"
    assert shown["estimated_effort_hours"] == 120 and shown["actual_effort_hours"] == 30
    assert shown["level"] == 2 and shown["color"] == "#3B82F6"

    only_summary = _json(client, "post", "/enterprise/api/work-packages",
                         {"name": "Summary only", "summary": "Only a summary"})
    row = svc.get_work_package(only_summary.get_json()["id"], org.id)
    assert row.description == "Only a summary" and row.summary == "Only a summary"


# -- idempotency of the whole deploy sequence -------------------------------------


def test_round3_deploy_sequence_idempotent(app, db_session, make_org, bridge_off):
    from app.models.roadmap_models import RoadmapDeliverable, RoadmapWorkPackage
    from app.models.unified_work_package import UnifiedWorkPackage
    org, user = _org_with_user(db_session, make_org, "idem-seq")
    plateau, gap = _plateau(db_session, org), _gap(db_session, org)
    legacy = _legacy(db_session, org, "Old row", plateau_id=plateau.id)
    legacy_two = _legacy(db_session, org, "Old row two")
    legacy_two.dependencies = [legacy.id]
    roadmap = RoadmapWorkPackage(name="Roadmap row", business_capability="Cap", created_by=user.id)
    db_session.add(roadmap)
    db_session.flush()
    db_session.add(RoadmapDeliverable(name="Roadmap deliverable", organization_id=org.id,
                                      work_package_id=roadmap.id, status="planned"))
    db_session.add(UnifiedWorkPackage(name="Carries columns", organization_id=org.id,
                                      plateau_id=plateau.id, gap_id=gap.id))
    db_session.flush()
    db_session.commit()

    for step in (("backfill-work-package-org",), ("merge-work-package-stores",),
                 ("backfill-work-package-org",), ("merge-work-package-stores", "--verify")):
        result = _cli(app, *step)
        assert result.exit_code == 0, (step, result.output)
    first = _merge(app)
    assert "changed=0" in first, first
    assert "relationships created" not in first
    dry = _merge(app, "--dry-run")
    assert "would merge" not in dry
    assert "copied" not in dry


# -- round 3b: child gap and bridged plateau come from relationships -------------


def test_child_inherits_parent_gap_from_relationship(db_session, make_org, client, login_as):
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "r3b-child")
    gap = _gap(db_session, org)
    parent = svc.create_work_package(organization_id=org.id, name="Parent", gap_id=gap.id)
    db_session.commit()
    login_as(client, user)
    resp = _json(client, "post", "/capability-map/api/roadmap/work-packages/%s/children" % parent.id,
                 {"name": "Child"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    child_id = resp.get_json()["work_package"]["id"]
    child = svc.require_work_package(child_id, org.id)
    assert child.gap_id is None
    assert svc.plateau_and_gap_links([child], org.id)[child.id]["gap_ids"] == [gap.id]
    assert [kind for kind, _t in _relationships_of(child)] == ["association"]


def test_bridged_plateau_link_is_a_relationship_at_once(app, db_session, make_org):
    from app.services import work_package_service as svc

    org = make_org("r3b-bridge")
    plateau = _plateau(db_session, org)
    legacy = _legacy(db_session, org, "Bridged", plateau_id=plateau.id)
    db_session.flush()
    copy = _copy("work_packages", legacy.id, org)
    assert copy is not None
    assert [kind for kind, _t in _relationships_of(copy)] == ["realization"]
    assert svc.plateau_work_package_ids([plateau.id], org.id)[plateau.id] == {copy.id}

    db_session.commit()
    before = len(_relationships_of(copy))
    out = _merge(app)
    assert "relationships created" not in out
    db_session.expire_all()
    assert len(_relationships_of(copy)) == before
