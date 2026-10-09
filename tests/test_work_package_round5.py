"""R1-B04 PR 2 fix round 5: defects N4-01 to N4-05 of the fourth review of PR 421.

N4-01 the enterprise list's edit keeps the description; N4-02 a row placed after the
migration does not bring a stale link back; N4-03 the gap filter has one reader;
N4-04 the plateau and gap links held in the old store's association tables become
relationships (once at deploy, then through the session bridge); N4-05 concurrent
link edits of one work package serialise.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import pytest

from tests.test_work_package_round3 import (
    _cli,
    _copy,
    _gap,
    _json,
    _legacy,
    _merge,
    _org_with_user,
    _plateau,
    _relationships_of,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bridge_off_round5():
    from app.services import work_package_bridge

    with work_package_bridge.suspended():
        yield


def _links(db_session, org, wp):
    from app.services import work_package_service as svc

    db_session.expire_all()
    return svc.plateau_and_gap_links([wp], org.id)[wp.id]


def _raw_association(db_session, kind, old_id, target_id, role=None):
    """A row of the old store's association table written as an older release wrote it
    (Core insert, so the session bridge does not see it)."""
    from sqlalchemy import text

    if kind == "gap":
        db_session.execute(text(  # tenancy-ok: test fixture
            "INSERT INTO gap_work_packages (gap_id, work_package_id, resolution_role, created_at) "
            "VALUES (:t, :w, :r, :c)"), {"t": target_id, "w": old_id, "r": role, "c": datetime.utcnow()})
    else:
        db_session.execute(text(  # tenancy-ok: test fixture
            "INSERT INTO work_package_plateaus (plateau_id, work_package_id, created_at) "
            "VALUES (:t, :w, :c)"), {"t": target_id, "w": old_id, "c": datetime.utcnow()})
    db_session.flush()


def _marker(db_session, wp_id, value="unset"):
    """Read (or, with a value, set) association_links_migrated_at of a unified row."""
    from sqlalchemy import text

    if value != "unset":
        db_session.execute(text(  # tenancy-ok: test fixture
            "UPDATE unified_work_packages SET association_links_migrated_at = :v WHERE id = :i"),
            {"v": value, "i": wp_id})
        db_session.flush()
        return value
    return db_session.execute(text(  # tenancy-ok: test fixture
        "SELECT association_links_migrated_at FROM unified_work_packages WHERE id = :i"),
        {"i": wp_id}).scalar()


# -- N4-01: the enterprise list's edit keeps the description --------------------


def test_enterprise_list_status_edit_keeps_description(db_session, make_org, client, login_as):
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "n401")
    with_summary = svc.create_work_package(
        organization_id=org.id, name="Has a summary", summary="Short summary",
        description="A long description of the work", status="planned")
    no_summary = svc.create_work_package(
        organization_id=org.id, name="No summary", description="Only a long description",
        status="planned")
    db_session.commit()
    login_as(client, user)

    for wp, summary, description in (
        (with_summary, "Short summary", "A long description of the work"),
        (no_summary, None, "Only a long description"),
    ):
        listed = client.get("/enterprise/api/work-packages?per_page=100").get_json()
        row = [r for r in listed["work_packages"] if r["id"] == wp.id][0]
        assert row["summary"] == summary
        row["status"] = "in_progress"  # the modal sends the row back with one field changed
        resp = _json(client, "patch", "/enterprise/api/work-packages/%s" % wp.id, row)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        db_session.expire_all()
        fresh = svc.get_work_package(wp.id, org.id)
        assert fresh.status == "in_progress"
        assert fresh.description == description
        assert fresh.summary == summary  # unchanged, including when it is null

    # A request that carries a description does change it.
    resp = _json(client, "patch", "/enterprise/api/work-packages/%s" % no_summary.id,
                 {"description": "Rewritten"})
    assert resp.status_code == 200
    db_session.expire_all()
    assert svc.get_work_package(no_summary.id, org.id).description == "Rewritten"
    # to_roadmap_dict still falls back to the description for a row with no summary
    # (the capability roadmap modal reads summary only).
    assert svc.to_roadmap_dict(svc.get_work_package(no_summary.id, org.id), org.id)["summary"] == "Rewritten"


# -- N4-02: a row placed after the migration does not restore a stale link ------


def test_row_placed_after_migration_does_not_restore_stale_link(
        app, db_session, make_org, bridge_off_round5):
    from sqlalchemy import text

    from app.models.unified_work_package import UnifiedWorkPackage
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "n402")
    p1, p2 = _plateau(db_session, org, "P1"), _plateau(db_session, org, "P2")
    # No organisation of its own; its creator belongs to one.
    row = UnifiedWorkPackage(name="Unplaced", organization_id=None, created_by=user.id,
                             plateau_id=p1.id)
    db_session.add(row)
    db_session.flush()
    db_session.execute(text(  # tenancy-ok: test fixture
        "UPDATE unified_work_packages SET organization_id = NULL WHERE id = :i"), {"i": row.id})
    db_session.commit()

    _merge(app)  # the deploy cannot place it: its values wait
    db_session.expire_all()
    assert row.organization_id is None and row.plateau_id == p1.id

    result = _cli(app, "backfill-work-package-org")  # the run that places it migrates it
    assert result.exit_code == 0, result.output
    assert "plateau relationships created: 1" in result.output
    assert "link columns set to NULL: 1" in result.output
    db_session.expire_all()
    assert row.organization_id == org.id and row.plateau_id is None
    assert _links(db_session, org, row)["plateau_ids"] == [p1.id]

    # Moved on a new screen, then two more deploys: exactly the new plateau.
    svc.update_work_package(row.id, organization_id=org.id, plateau_id=p2.id)
    db_session.commit()
    _merge(app)
    _merge(app)
    _cli(app, "backfill-work-package-org")
    assert _links(db_session, org, row)["plateau_ids"] == [p2.id]

    # --dry-run counts and changes nothing.
    again = UnifiedWorkPackage(name="Unplaced again", organization_id=None, created_by=user.id,
                               gap_id=_gap(db_session, org).id)
    db_session.add(again)
    db_session.flush()
    db_session.execute(text(  # tenancy-ok: test fixture
        "UPDATE unified_work_packages SET organization_id = NULL WHERE id = :i"), {"i": again.id})
    db_session.commit()
    row_count = _cli(app, "backfill-work-package-org", "--dry-run").output
    assert "would attribute 1 row(s) via created_by" in row_count
    dry = _cli(app, "backfill-work-package-org", "--dry-run")
    assert dry.exit_code == 0, dry.output
    db_session.expire_all()
    assert again.gap_id is not None and again.organization_id is None


# -- N4-03: the gap filter has one reader ---------------------------------------


def test_gap_filter_has_one_reader(db_session, make_org, client, login_as):
    org, user = _org_with_user(db_session, make_org, "n403")
    gap = _gap(db_session, org)
    linked = _legacy(db_session, org, "Linked through the old association table")
    linked.gaps.append(gap)  # an old screen links the gap; the bridge makes the relationship
    unseen = _legacy(db_session, org, "Association row the bridge never saw")
    _raw_association(db_session, "gap", unseen.id, gap.id)
    other = _legacy(db_session, org, "Not linked")
    db_session.flush()
    copies = {r: _copy("work_packages", r.id, org) for r in (linked, unseen, other)}
    db_session.commit()
    login_as(client, user)

    def seen(wp):
        by_filter = client.get(
            "/capability-map/api/roadmap/work-packages?gap_id=%s&root_only=false" % gap.id
        ).get_json()["work_packages"]
        listed = client.get("/capability-map/api/roadmap/work-packages?root_only=false"
                            ).get_json()["work_packages"]
        row = [w for w in listed if w["id"] == wp.id][0]
        detail = client.get("/capability-map/api/roadmap/work-packages/%s" % wp.id
                            ).get_json()["work_package"]
        return (wp.id in [w["id"] for w in by_filter], gap.id in row["gap_ids"],
                gap.id in detail["gap_ids"])

    assert seen(copies[linked]) == (True, True, True)
    # A work package the relationships do not link is not listed under the gap by any
    # of the three views: they read the same relationships.
    assert seen(copies[unseen]) == (False, False, False)
    assert seen(copies[other]) == (False, False, False)

    source = (ROOT / "app/modules/capabilities/routes/roadmap_routes.py").read_text(encoding="utf-8")
    assert "gap_work_packages" not in source


# -- N4-04: the old store's association tables become relationships -------------


def test_association_gap_links_migrated_once(app, db_session, make_org):
    from app.services import work_package_bridge
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "n404a")
    gap_one, gap_two = _gap(db_session, org), _gap(db_session, org)
    p1 = _plateau(db_session, org)
    legacy = _legacy(db_session, org, "Old row with associations")
    _raw_association(db_session, "gap", legacy.id, gap_one.id, role="primary")
    _raw_association(db_session, "gap", legacy.id, gap_two.id)
    _raw_association(db_session, "plateau", legacy.id, p1.id)
    copy = _merge_copy(app, db_session, legacy, org)
    assert _marker(db_session, copy.id) is None

    with work_package_bridge.suspended():
        out = _merge(app, "--dry-run")
        assert "rows with association links to migrate: 1" in out
        assert _links(db_session, org, copy) == {"plateau_ids": [], "gap_ids": []}

        out = _merge(app)
    assert "gap association links migrated: 2" in out and "plateau association links migrated: 1" in out
    assert _marker(db_session, copy.id) is not None
    assert _links(db_session, org, copy) == {"plateau_ids": [p1.id], "gap_ids": [gap_one.id, gap_two.id]}
    assert _scalar(db_session, "SELECT count(*) FROM gap_work_packages WHERE work_package_id = %s"
                   % legacy.id) == 2  # the association rows stay

    # A gap link removed on a new screen stays removed after two more deploys.
    svc.update_work_package(copy.id, organization_id=org.id, gap_id=gap_two.id)
    assert _links(db_session, org, copy)["gap_ids"] == [gap_two.id]
    svc.update_work_package(copy.id, organization_id=org.id, gap_id=None)
    db_session.commit()
    with work_package_bridge.suspended():
        out = _merge(app)
        assert "association links migrated" not in out
        _merge(app)
    assert _links(db_session, org, copy)["gap_ids"] == []
    assert _links(db_session, org, copy)["plateau_ids"] == [p1.id]


def _merge_copy(app, db_session, legacy, org):
    """The unified copy of an old row as a release without this round left it: copied,
    with association rows the bridge never saw and no marker."""
    copy = _copy("work_packages", legacy.id, org)
    _marker(db_session, copy.id, None)
    db_session.commit()
    return copy


def _scalar(db_session, sql):
    from sqlalchemy import text

    return db_session.execute(text(sql)).scalar()  # tenancy-ok: test fixture


def test_old_screen_gap_append_and_remove_bridged(app, db_session, make_org):
    from flask import g

    from app.services.gap_archimate_service import gap_archimate_service

    org, _user = _org_with_user(db_session, make_org, "n404b")
    gap = _gap(db_session, org)
    with app.test_request_context("/"):
        g.current_org_id = org.id
        old = gap_archimate_service.create_work_package_for_gap(gap)  # wp.gaps.append(gap)
        db_session.flush()
        copy = _copy("work_packages", old.id, org)
        assert copy is not None
        assert _links(db_session, org, copy)["gap_ids"] == [gap.id]
        assert _marker(db_session, copy.id) is not None
        # The row is flushed before its element is made, so the copy briefly linked from
        # an element of its own: the copy now points at the shared element, the first one
        # stays in place (nothing is deleted) and the links read from the current element.
        from app.models import ArchiMateElement

        assert copy.archimate_element_id == old.archimate_element_id
        assert [t for _k, t in _relationships_of(copy)] == [gap.archimate_element_id]
        assert ArchiMateElement.query.filter_by(name=old.name, organization_id=org.id).count() == 2

        # A child inherits the parent's gaps: a new row whose association rows are
        # written in the same flush as the row.
        child = gap_archimate_service.create_child_work_package(old, {"name": "Child task"})
        db_session.flush()
        child_copy = _copy("work_packages", child.id, org)
        assert _links(db_session, org, child_copy)["gap_ids"] == [gap.id]
        assert _marker(db_session, child_copy.id) is not None

        # A second gap is appended, the first removed, on the old store.
        other = _gap(db_session, org)
        old.gaps.append(other)
        db_session.flush()
        assert _links(db_session, org, copy)["gap_ids"] == [gap.id, other.id]
        old.gaps.remove(gap)
        db_session.flush()
        assert _links(db_session, org, copy)["gap_ids"] == [other.id]
        assert [t for _k, t in _relationships_of(copy)] == [other.archimate_element_id]
        # The child kept its own link.
        assert _links(db_session, org, child_copy)["gap_ids"] == [gap.id]


def test_gap_resolution_insert_reaches_relationships(app, db_session, make_org, monkeypatch):
    from flask import g
    from sqlalchemy import text

    from app.modules.architecture.services.gap_resolution_service import GapResolutionService

    org, _user = _org_with_user(db_session, make_org, "n404c")
    gap = _gap(db_session, org)
    service = GapResolutionService()
    monkeypatch.setattr(service, "_generate_work_packages_from_gap", lambda *_a, **_k: [
        {"name": "Design the fix", "resolution_role": "primary"},
        {"name": "Roll out the fix", "resolution_role": "supporting"},
    ])
    with app.test_request_context("/"):
        g.current_org_id = org.id
        made = service.create_work_packages_from_gap(gap.id)
    assert len(made) == 2
    roles = dict(db_session.execute(text(  # tenancy-ok: test fixture
        "SELECT work_package_id, resolution_role FROM gap_work_packages WHERE gap_id = :g"),
        {"g": gap.id}).all())
    for old, role in zip(made, ("primary", "supporting")):
        assert roles[old.id] == role  # resolution_role is kept on the association row
        copy = _copy("work_packages", old.id, org)
        assert _links(db_session, org, copy)["gap_ids"] == [gap.id]
    from app.services import work_package_service as svc

    assert len(svc.work_package_ids_for_gap(gap.id, org.id)) == 2


def test_assistant_plateau_append_bridged(app, db_session, make_org, client, login_as):
    org, user = _org_with_user(db_session, make_org, "n404d")
    plateau = _plateau(db_session, org)
    db_session.commit()
    login_as(client, user)
    resp = _json(client, "post", "/api/architecture-assistant/roadmap-elements", {
        "gap_elements": [{"to_plateau_id": plateau.id}],
        "solution_option": {"implementation_weeks": 12},
    })
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)
    ids = {w["name"]: w["work_package_id"] for w in resp.get_json().get("work_packages", [])} \
        if isinstance(resp.get_json().get("work_packages"), list) else None
    if ids is None:  # the response lists the created rows under another key
        from app.models.implementation_migration import WorkPackage

        ids = {w.name: w.id for w in WorkPackage.query.filter_by(organization_id=org.id).all()}
    copy = _copy("work_packages", ids["Go-Live"], org)
    assert copy is not None
    assert _links(db_session, org, copy)["plateau_ids"] == [plateau.id]
    first = _copy("work_packages", ids["Discovery & Design"], org)
    assert _links(db_session, org, first)["plateau_ids"] == []


def test_column_move_keeps_association_plateau(db_session, make_org):
    org, _user = _org_with_user(db_session, make_org, "n404e")
    kept, first, second = (_plateau(db_session, org, n) for n in ("Kept", "First", "Second"))
    legacy = _legacy(db_session, org, "Old row")
    copy = _copy("work_packages", legacy.id, org)

    legacy.plateaus.append(kept)  # the association set
    db_session.flush()
    assert _links(db_session, org, copy)["plateau_ids"] == [kept.id]
    legacy.plateau_id = first.id  # the column's own link
    db_session.flush()
    assert sorted(_links(db_session, org, copy)["plateau_ids"]) == sorted([kept.id, first.id])
    legacy.plateau_id = second.id  # moving the column replaces only its own link
    db_session.flush()
    assert sorted(_links(db_session, org, copy)["plateau_ids"]) == sorted([kept.id, second.id])
    legacy.plateau_id = None
    db_session.flush()
    assert _links(db_session, org, copy)["plateau_ids"] == [kept.id]
    legacy.plateaus.remove(kept)
    db_session.flush()
    assert _links(db_session, org, copy)["plateau_ids"] == []


def test_l5_reports_gap_for_gap_generated_work_package(app, db_session, make_org):
    from flask import g

    from app.modules.intelligence.services.query_service import IntelligenceQueryService
    from app.services.gap_archimate_service import gap_archimate_service

    org, _user = _org_with_user(db_session, make_org, "n404f")
    gap = _gap(db_session, org)
    with app.test_request_context("/"):
        g.current_org_id = org.id
        old = gap_archimate_service.create_work_package_for_gap(gap)
        db_session.flush()
        copy = _copy("work_packages", old.id, org)
        result = IntelligenceQueryService.programme_for_element(copy.archimate_element_id)
    rows = [w for w in result["work_packages"] if w.get("name") == copy.name or w.get("id") == copy.id]
    assert rows, result
    assert rows[0]["gap"]["gap_id"] == gap.id
    assert rows[0]["gap"]["reason"] is None


# -- N4-05: concurrent link edits serialise -------------------------------------


def test_concurrent_plateau_edits_end_with_one_plateau(app, monkeypatch):
    from sqlalchemy import text

    from app import db
    from app.models.implementation_migration import Plateau, WorkPackage
    from app.models.organization import Organization
    from app.services import work_package_service as svc

    import uuid

    suffix = uuid.uuid4().hex[:10]
    with app.app_context():
        org = Organization(name="Test n405 %s" % suffix, slug="test-n405-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        org_id = org.id
        p1, p2, p3 = (Plateau(name="N405 %s %s" % (n, suffix), organization_id=org_id)
                      for n in ("one", "two", "three"))
        db.session.add_all([p1, p2, p3])
        db.session.flush()
        ids = (p1.id, p2.id, p3.id)
        old = WorkPackage(name="Concurrent %s" % suffix, organization_id=org_id)
        db.session.add(old)
        db.session.flush()
        old_id = old.id
        copy_id = svc.get_by_source("work_packages", old_id, org_id).id
        svc.update_work_package(copy_id, organization_id=org_id, plateau_id=ids[0])
        db.session.commit()

    barrier = threading.Barrier(2)
    real = svc._link_targets
    seen = threading.local()
    errors = []

    def read_then_meet(wp, key, organization_id):
        found = real(wp, key, organization_id)
        if not getattr(seen, "met", False):
            seen.met = True
            try:  # both sessions have read before either writes (a lock makes the second wait)
                barrier.wait(timeout=3)
            except threading.BrokenBarrierError:
                pass
        return found

    monkeypatch.setattr(svc, "_link_targets", read_then_meet)

    def new_screen():
        try:
            with app.app_context():
                svc.update_work_package(copy_id, organization_id=org_id, plateau_id=ids[1])
                db.session.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(("new", exc))
        finally:
            with app.app_context():
                db.session.remove()

    def old_screen():
        try:
            with app.app_context():
                row = db.session.get(WorkPackage, old_id)
                row.plateau_id = ids[2]
                db.session.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(("old", exc))
        finally:
            with app.app_context():
                db.session.remove()

    threads = [threading.Thread(target=new_screen), threading.Thread(target=old_screen)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert not any(t.is_alive() for t in threads)
        assert errors == []
        with app.app_context():
            db.session.remove()
            final = svc.plateau_and_gap_links([svc.get_work_package(copy_id, org_id)], org_id)[copy_id]
            assert len(final["plateau_ids"]) == 1, final
            assert final["plateau_ids"][0] in (ids[1], ids[2])
    finally:
        monkeypatch.undo()
        with app.app_context():
            db.session.rollback()
            params = {"o": org_id}
            for sql in (
                "DELETE FROM archimate_relationships WHERE organization_id = :o",
                "DELETE FROM unified_work_packages WHERE organization_id = :o",
                "DELETE FROM work_packages WHERE organization_id = :o",
                "DELETE FROM plateaus WHERE organization_id = :o",
                "DELETE FROM archimate_elements WHERE organization_id = :o",
                "DELETE FROM organizations WHERE id = :o",
            ):
                try:
                    db.session.execute(text(sql), params)  # tenancy-ok: test cleanup
                    db.session.commit()
                except Exception:  # noqa: BLE001 - best-effort cleanup of committed rows
                    db.session.rollback()
            db.session.remove()
