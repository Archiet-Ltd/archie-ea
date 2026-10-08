"""R1-B04 PR 2 fix round 6: defects N5-01 to N5-04 of the fifth review of PR 421.

N5-02 a copy that takes its source row's element only moves links off, and removes, an
element made in this transaction; N5-01 both sides of the old store's association tables
are bridged and the deploy heals association rows written after its marker; N5-03 lock
conflicts fail the request and locks are taken in id order; N5-04 removing an association
keeps what the old row still holds in its own column.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from tests.test_work_package_round3 import (
    _cli,
    _copy,
    _gap,
    _legacy,
    _merge,
    _org_with_user,
    _plateau,
    _relationships_of,
)
from tests.test_work_package_round5 import _links, _marker, _raw_association, _scalar

ROOT = Path(__file__).resolve().parents[1]


def _element(db_session, org, name="Spare element", element_type="ApplicationComponent"):
    from app.services.archimate_backbone import create_backbone_element

    return create_backbone_element(
        element_type=element_type, layer="Application", name="%s %s" % (name, uuid.uuid4().hex[:6]),
        organization_id=org.id)


# -- N5-02: follow and delete only an element made in this transaction ------------


def test_element_follow_keeps_crosswalk_and_incoming_relationship(db_session, make_org):
    from app.models.archimate_core import ArchiMateElement
    from app.models.external_identity_crosswalk import ExternalIdentityCrosswalk
    from app.models.models import ArchiMateRelationship
    from app.services import work_package_service as svc
    from app.services.archimate_backbone import CREATED_ELEMENTS_KEY

    org, _user = _org_with_user(db_session, make_org, "n502a")
    plateau, gap = _plateau(db_session, org), _gap(db_session, org)
    legacy = _legacy(db_session, org, "Old row")
    copy = _copy("work_packages", legacy.id, org)
    svc.update_work_package(copy.id, organization_id=org.id, plateau_id=plateau.id, gap_id=gap.id)
    old_element_id = copy.archimate_element_id
    assert old_element_id in db_session.info[CREATED_ELEMENTS_KEY]  # made in this transaction

    # Another record has pointed at that element since: an identity crosswalk row and an
    # incoming relationship from another element.
    other = _element(db_session, org, "Other system")
    db_session.add(ExternalIdentityCrosswalk(
        source_system="crm", external_id="wp-1", element_id=old_element_id, organization_id=org.id))
    incoming = ArchiMateRelationship(
        source_id=other.id, target_id=old_element_id, type="association", organization_id=org.id)
    db_session.add(incoming)
    db_session.commit()
    assert not db_session.info.get(CREATED_ELEMENTS_KEY)  # the transaction ended

    before = _links(db_session, org, copy)
    assert before == {"plateau_ids": [plateau.id], "gap_ids": [gap.id]}
    incoming_id = incoming.id

    # An old screen gives the row an element of its own: the copy changes element.
    taken = _element(db_session, org, "Old screen element", element_type="WorkPackage")
    legacy.archimate_element_id = taken.id
    db_session.flush()
    db_session.expire_all()

    copy = _copy("work_packages", legacy.id, org)
    assert copy.archimate_element_id == taken.id
    assert db_session.get(ArchiMateElement, old_element_id) is not None
    assert _scalar(db_session, "SELECT count(*) FROM external_identity_crosswalk "
                   "WHERE element_id = %s" % old_element_id) == 1
    survivor = db_session.get(ArchiMateRelationship, incoming_id)
    assert survivor is not None and survivor.target_id == old_element_id
    # The copy's plateau and gap links read the same as before, now from the new element.
    assert _links(db_session, org, copy) == before
    assert sorted(t for t, _target in _relationships_of(copy)) == ["association", "realization"]
    # The old element keeps every relationship it had, outgoing ones included.
    assert _scalar(db_session, "SELECT count(*) FROM archimate_relationships "
                   "WHERE source_id = %s" % old_element_id) == 2


def test_element_made_in_this_transaction_is_followed_and_dropped(app, db_session, make_org):
    from flask import g

    from app.models import ArchiMateElement
    from app.services.gap_archimate_service import gap_archimate_service

    org, _user = _org_with_user(db_session, make_org, "n502b")
    gap = _gap(db_session, org)
    with app.test_request_context("/"):
        g.current_org_id = org.id
        old = gap_archimate_service.create_work_package_for_gap(gap)  # wp.gaps.append(gap)
        db_session.flush()
        copy = _copy("work_packages", old.id, org)
        assert _links(db_session, org, copy)["gap_ids"] == [gap.id]
        # The row was flushed before its element was made, so the copy briefly linked from
        # an element of its own: the link followed the copy to the shared element and the
        # spare element, which this transaction made, is gone.
        assert copy.archimate_element_id == old.archimate_element_id
        assert [t for _k, t in _relationships_of(copy)] == [gap.archimate_element_id]
        assert ArchiMateElement.query.filter_by(name=old.name, organization_id=org.id).count() == 1


def test_drop_unused_elements_only_deletes_created_ones(db_session, make_org):
    """The deletion cannot reach an element outside the set of those this transaction
    made, and not one that any table with a foreign key to it still holds."""
    from app.commands.consolidate_work_packages import _drop_unused_elements, _element_references
    from app.models.external_identity_crosswalk import ExternalIdentityCrosswalk

    org, _user = _org_with_user(db_session, make_org, "n502c")
    unreferenced = _element(db_session, org)
    referenced = _element(db_session, org)
    db_session.add(ExternalIdentityCrosswalk(
        source_system="crm", external_id="x-1", element_id=referenced.id, organization_id=org.id))
    db_session.flush()
    conn = db_session.connection()

    _drop_unused_elements(conn, "work_packages", [unreferenced.id, referenced.id], set())
    _drop_unused_elements(conn, "work_packages", [unreferenced.id, referenced.id])  # no set at all
    assert _scalar(db_session, "SELECT count(*) FROM archimate_elements WHERE id IN (%s, %s)"
                   % (unreferenced.id, referenced.id)) == 2

    _drop_unused_elements(conn, "work_packages", [unreferenced.id, referenced.id],
                          {unreferenced.id, referenced.id})
    assert _scalar(db_session, "SELECT count(*) FROM archimate_elements WHERE id = %s" % unreferenced.id) == 0
    assert _scalar(db_session, "SELECT count(*) FROM archimate_elements WHERE id = %s" % referenced.id) == 1  # the crosswalk row holds it
    assert ("external_identity_crosswalk", "element_id") in _element_references(conn)

    source = (ROOT / "app/commands/consolidate_work_packages.py").read_text(encoding="utf-8")
    assert "wanted = [e for e in element_ids if e in created]" in source


# -- N5-01: both sides of the association, and a deploy that heals -----------------


def test_gap_side_route_link_reaches_relationships(db_session, make_org, tenant_ctx, client, login_as):
    from app.models.implementation_migration import WorkPackage
    from tests.test_interface_programme_rollup import _setup

    org, user = _org_with_user(db_session, make_org, "n501a")
    with tenant_ctx(org.id):
        _initiative, gap = _setup(db_session, org)
    initiative_id = _initiative.id
    gap_id = gap.id
    db_session.commit()
    login_as(client, user)

    # A new work package, attached through the route (gap.work_packages.append(new)).
    resp = client.post("/interface-register/gaps/%s/work-packages" % gap_id, data={
        "initiative_id": initiative_id, "name": "Attached through the route"})
    assert resp.status_code in (302, 303), resp.get_data(as_text=True)[:400]
    created = WorkPackage.query.filter_by(name="Attached through the route").one()
    copy = _copy("work_packages", created.id, org)
    assert copy is not None
    assert _links(db_session, org, copy)["gap_ids"] == [gap_id]
    assert _marker(db_session, copy.id) is not None

    # An existing work package appended from the gap side (the statement the service runs).
    # Its own gaps collection is not loaded, so only the gap's side shows the change.
    from app.models.implementation_migration import Gap

    existing = _legacy(db_session, org, "Existing work package")
    existing_copy = _copy("work_packages", existing.id, org)
    assert _links(db_session, org, existing_copy)["gap_ids"] == []
    db_session.commit()
    gap = db_session.get(Gap, gap_id)
    gap.work_packages.append(existing)
    db_session.flush()
    assert _links(db_session, org, existing_copy)["gap_ids"] == [gap_id]


def test_gap_side_and_plateau_side_remove_bridged(db_session, make_org):
    from app.models.implementation_migration import Gap, Plateau

    org, _user = _org_with_user(db_session, make_org, "n501b")
    gap, plateau = _gap(db_session, org), _plateau(db_session, org)
    legacy = _legacy(db_session, org, "Old row")
    copy = _copy("work_packages", legacy.id, org)
    legacy.gaps.append(gap)
    legacy.plateaus.append(plateau)
    db_session.flush()
    assert _links(db_session, org, copy) == {"plateau_ids": [plateau.id], "gap_ids": [gap.id]}
    db_session.commit()  # the row's own collections are no longer loaded

    gap = db_session.get(Gap, gap.id)
    gap.work_packages.remove(legacy)
    db_session.flush()
    assert _links(db_session, org, copy) == {"plateau_ids": [plateau.id], "gap_ids": []}

    plateau = db_session.get(Plateau, plateau.id)
    plateau.work_packages.remove(legacy)
    db_session.flush()
    assert _links(db_session, org, copy) == {"plateau_ids": [], "gap_ids": []}
    db_session.commit()

    # And the same appends from those sides.
    gap = db_session.get(Gap, gap.id)
    plateau = db_session.get(Plateau, plateau.id)
    gap.work_packages.append(legacy)
    plateau.work_packages.append(legacy)
    db_session.flush()
    assert _links(db_session, org, copy) == {"plateau_ids": [plateau.id], "gap_ids": [gap.id]}
    # A change seen from both sides applies once.
    assert _scalar(db_session, "SELECT count(*) FROM archimate_relationships "
                   "WHERE source_id = %s" % copy.archimate_element_id) == 2


def test_unbridged_association_row_healed_by_deploy(app, db_session, make_org):
    from app.services import work_package_bridge

    org, _user = _org_with_user(db_session, make_org, "n501c")
    gap, plateau = _gap(db_session, org), _plateau(db_session, org)
    legacy = _legacy(db_session, org, "Old row")
    copy = _copy("work_packages", legacy.id, org)
    assert _marker(db_session, copy.id) is not None  # migrated: nothing to migrate when it was copied
    marker = _marker(db_session, copy.id)

    # A writer the bridge never sees (Core) adds association rows after the marker.
    _raw_association(db_session, "gap", legacy.id, gap.id, role="primary")
    _raw_association(db_session, "plateau", legacy.id, plateau.id)
    db_session.commit()
    assert _links(db_session, org, copy) == {"plateau_ids": [], "gap_ids": []}

    with work_package_bridge.suspended():
        dry = _merge(app, "--dry-run")
        assert "rows with association links to migrate: 1" in dry
        assert _links(db_session, org, copy) == {"plateau_ids": [], "gap_ids": []}
        first = _merge(app)
        assert "gap association links migrated: 1" in first
        assert "plateau association links migrated: 1" in first
        assert _links(db_session, org, copy) == {"plateau_ids": [plateau.id], "gap_ids": [gap.id]}
        advanced = _marker(db_session, copy.id)
        assert advanced > marker

        second = _merge(app)
        assert "association links migrated" not in second
        assert "rows with association links" not in second
    assert _marker(db_session, copy.id) == advanced
    assert _links(db_session, org, copy) == {"plateau_ids": [plateau.id], "gap_ids": [gap.id]}
    assert _scalar(db_session, "SELECT count(*) FROM gap_work_packages WHERE work_package_id = %s"
                   % legacy.id) == 1  # the association rows stay


def test_new_screen_removal_survives_heal(app, db_session, make_org):
    from app.services import work_package_bridge
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "n501d")
    gap = _gap(db_session, org)
    legacy = _legacy(db_session, org, "Old row")
    copy = _copy("work_packages", legacy.id, org)
    _marker(db_session, copy.id, None)  # as left by an older release
    _raw_association(db_session, "gap", legacy.id, gap.id)
    db_session.commit()
    with work_package_bridge.suspended():
        _merge(app)
    assert _links(db_session, org, copy)["gap_ids"] == [gap.id]

    # The association row predates the marker now. Its link is removed on a new screen.
    svc.update_work_package(copy.id, organization_id=org.id, gap_id=None)
    db_session.commit()
    assert _links(db_session, org, copy)["gap_ids"] == []
    with work_package_bridge.suspended():
        out = _merge(app)
        assert "association links migrated" not in out
        _merge(app)
    assert _links(db_session, org, copy)["gap_ids"] == []
    assert _scalar(db_session, "SELECT count(*) FROM gap_work_packages WHERE work_package_id = %s"
                   % legacy.id) == 1


def test_bridge_advances_marker_so_deploy_does_not_re_add(app, db_session, make_org):
    from app.services import work_package_bridge
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "n501e")
    gap = _gap(db_session, org)
    legacy = _legacy(db_session, org, "Old row")
    copy = _copy("work_packages", legacy.id, org)
    before = _marker(db_session, copy.id)
    legacy.gaps.append(gap)  # the bridge makes the relationship and advances the marker
    db_session.flush()
    assert _links(db_session, org, copy)["gap_ids"] == [gap.id]
    created = _scalar(db_session, "SELECT created_at FROM gap_work_packages WHERE work_package_id = %s"
                      % legacy.id)
    assert _marker(db_session, copy.id) > created

    svc.update_work_package(copy.id, organization_id=org.id, gap_id=None)  # removed on a new screen
    db_session.commit()
    with work_package_bridge.suspended():
        assert "association links migrated" not in _merge(app)
    assert _links(db_session, org, copy)["gap_ids"] == []


def test_marker_writes_use_the_utc_wall_clock():
    source = (ROOT / "app/commands/consolidate_work_packages.py").read_text(encoding="utf-8")
    for number, line in enumerate(source.splitlines(), 1):
        if "association_links_migrated_at =" in line and "SET" in line:
            assert "CURRENT_TIMESTAMP" not in line, (number, line)
            assert "_MARKER_NOW" in line, (number, line)
    assert "clock_timestamp()" in source


# -- N5-03: lock conflicts fail the request ---------------------------------------


def test_multi_package_call_locks_in_ascending_order(db_session, make_org, monkeypatch):
    from app.commands.consolidate_work_packages import apply_link_changes
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "n503a")
    plateau = _plateau(db_session, org)
    copies = [_copy("work_packages", _legacy(db_session, org, "Row %s" % i).id, org) for i in range(3)]
    ids = sorted(c.id for c in copies)
    calls = []
    real_lock, real_targets = svc.lock_work_package, svc._link_targets

    def spy_lock(work_package_id):
        calls.append(("lock", work_package_id))
        return real_lock(work_package_id)

    def spy_targets(wp, key, organization_id):
        calls.append(("read", wp.id))
        return real_targets(wp, key, organization_id)

    monkeypatch.setattr(svc, "lock_work_package", spy_lock)
    monkeypatch.setattr(svc, "_link_targets", spy_targets)
    # Handed over in descending order on purpose.
    apply_link_changes({i: {"plateau_id": plateau.id} for i in reversed(ids)}, replace=False)

    assert [c for c in calls[:3]] == [("lock", i) for i in ids]  # all locks, ascending, first
    assert all(kind == "read" or kind == "lock" for kind, _i in calls)
    first_read = next(n for n, c in enumerate(calls) if c[0] == "read")
    assert first_read >= 3
    for copy in copies:
        assert _links(db_session, org, copy)["plateau_ids"] == [plateau.id]


def test_lock_conflict_inside_the_savepoint_is_raised_other_errors_contained(
        db_session, make_org, monkeypatch, caplog):
    from sqlalchemy.exc import OperationalError

    from app.commands.consolidate_work_packages import apply_link_changes
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "n503b")
    plateau = _plateau(db_session, org)
    copy = _copy("work_packages", _legacy(db_session, org, "Row").id, org)

    for code in ("40P01", "40001", "55P03"):
        class Driver(Exception):
            sqlstate = code

        def conflict(*_a, **_k):
            raise OperationalError("SELECT 1", {}, Driver("conflict"))

        monkeypatch.setattr(svc, "_resolve_links", conflict)
        with pytest.raises(OperationalError):
            apply_link_changes({copy.id: {"plateau_id": plateau.id}}, replace=False)

    def other(*_a, **_k):
        raise RuntimeError("something else")

    monkeypatch.setattr(svc, "_resolve_links", other)
    with caplog.at_level(logging.WARNING):
        done = apply_link_changes({copy.id: {"plateau_id": plateau.id}}, replace=False)
    assert done == [] and "plateau/gap links rolled back" in caplog.text
    monkeypatch.undo()
    assert _links(db_session, org, copy)["plateau_ids"] == []  # the transaction is still usable


def test_opposite_order_lock_conflict_raises_and_stores_agree(app):
    from sqlalchemy import text

    from app import db
    from app.models.implementation_migration import Plateau, WorkPackage
    from app.models.organization import Organization
    from app.services import work_package_service as svc

    suffix = uuid.uuid4().hex[:10]
    with app.app_context():
        org = Organization(name="Test n503 %s" % suffix, slug="test-n503-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        org_id = org.id
        plateaus = [Plateau(name="N503 %s %s" % (n, suffix), organization_id=org_id) for n in "123"]
        db.session.add_all(plateaus)
        db.session.flush()
        p1, p2, p3 = (p.id for p in plateaus)
        rows = [WorkPackage(name="N503 %s %s" % (n, suffix), organization_id=org_id) for n in "AB"]
        db.session.add_all(rows)
        db.session.flush()
        old_a, old_b = (r.id for r in rows)
        copy_a = svc.get_by_source("work_packages", old_a, org_id).id
        copy_b = svc.get_by_source("work_packages", old_b, org_id).id
        db.session.commit()

    barrier = threading.Barrier(2)
    errors = {}

    def new_screen():
        # Holds copy A's lock through the new screens' service, then asks for copy B's.
        try:
            with app.app_context():
                svc.update_work_package(copy_a, organization_id=org_id, plateau_id=p1)
                barrier.wait(timeout=20)
                svc.update_work_package(copy_b, organization_id=org_id, plateau_id=p3)
                db.session.commit()
        except Exception as exc:  # noqa: BLE001
            errors["new"] = exc
            with app.app_context():
                db.session.rollback()
        finally:
            with app.app_context():
                db.session.remove()

    def old_screen():
        # Holds copy B's lock through the bridge (an old screen), then asks for copy A's.
        try:
            with app.app_context():
                db.session.execute(text("SET LOCAL lock_timeout = '400ms'"))
                row_b = db.session.get(WorkPackage, old_b)
                row_b.plateau_id = p2
                db.session.flush()
                barrier.wait(timeout=20)
                row_a = db.session.get(WorkPackage, old_a)
                row_a.plateau_id = p2
                db.session.commit()
        except Exception as exc:  # noqa: BLE001
            errors["old"] = exc
            with app.app_context():
                db.session.rollback()
        finally:
            with app.app_context():
                db.session.remove()

    threads = [threading.Thread(target=new_screen), threading.Thread(target=old_screen)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(90)
        assert not any(t.is_alive() for t in threads)

        # One side raises the lock error and rolls back; the other commits.
        assert "new" not in errors, errors
        failed = errors.get("old")
        assert failed is not None
        orig = getattr(failed, "orig", failed)
        assert (getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)) in (
            "40P01", "40001", "55P03"), repr(failed)

        with app.app_context():
            db.session.remove()
            links = svc.plateau_and_gap_links(
                [svc.get_work_package(copy_a, org_id), svc.get_work_package(copy_b, org_id)], org_id)
            assert links[copy_a]["plateau_ids"] == [p1]
            assert links[copy_b]["plateau_ids"] == [p3]
            # The stores agree: whatever the old store committed is in the relationships.
            for old_id, copy_id in ((old_a, copy_a), (old_b, copy_b)):
                held = set()
                column = db.session.execute(text(
                    "SELECT plateau_id FROM work_packages WHERE id = :i"), {"i": old_id}).scalar()  # tenancy-ok: test
                if column is not None:
                    held.add(column)
                held.update(r[0] for r in db.session.execute(text(
                    "SELECT plateau_id FROM work_package_plateaus WHERE work_package_id = :i"),  # tenancy-ok: test
                    {"i": old_id}))
                assert held <= set(links[copy_id]["plateau_ids"]), (old_id, held, links[copy_id])
                assert column is None  # the old screen's change did not commit, nor was it half applied
    finally:
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


# -- N5-04: a removal keeps what the row still holds ------------------------------


def test_association_remove_keeps_column_plateau(db_session, make_org):
    org, _user = _org_with_user(db_session, make_org, "n504")
    plateau = _plateau(db_session, org)
    legacy = _legacy(db_session, org, "Old row", plateau_id=plateau.id)  # the column holds P
    copy = _copy("work_packages", legacy.id, org)
    assert _links(db_session, org, copy)["plateau_ids"] == [plateau.id]

    legacy.plateaus.append(plateau)  # the association holds P too
    db_session.flush()
    assert _links(db_session, org, copy)["plateau_ids"] == [plateau.id]

    legacy.plateaus.remove(plateau)  # remove the association: the column still holds P
    db_session.flush()
    assert _links(db_session, org, copy)["plateau_ids"] == [plateau.id]

    legacy.plateau_id = None  # clear the column: nothing holds P
    db_session.flush()
    assert _links(db_session, org, copy)["plateau_ids"] == []
