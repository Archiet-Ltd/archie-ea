"""R1-B04 PR 2 fix round 4: defects N3-01 to N3-12 of the third review of PR 421.

The ArchiMate relationships are the only store of a work package's plateau and gap
links. The unified plateau_id and gap_id columns are read once, by the deploy
migration, which then sets them to NULL.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path

import pytest

from tests.test_work_package_round3 import (
    _copy,
    _gap,
    _json,
    _legacy,
    _merge,
    _org_with_user,
    _plateau,
    _relationships_of,
)

pytestmark = pytest.mark.usefixtures("db_session")

ROOT = Path(__file__).resolve().parents[1]


def _links(db_session, org, wp):
    from app.services import work_package_service as svc

    db_session.expire_all()
    return svc.plateau_and_gap_links([wp], org.id)[wp.id]


def _raw_columns(db_session, wp, plateau_id=None, gap_id=None):
    """Put a value in the unified link columns, as a row from before this round has it."""
    from sqlalchemy import text

    db_session.execute(
        text("UPDATE unified_work_packages SET plateau_id = :p, gap_id = :g WHERE id = :i"),  # tenancy-ok: test fixture
        {"p": plateau_id, "g": gap_id, "i": wp.id},
    )
    db_session.flush()


# -- N3-01: the capability roadmap modal round-trips -------------------------


def test_capability_roadmap_edit_keeps_links(db_session, make_org, client, login_as):
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "n301")
    plateau, gap = _plateau(db_session, org), _gap(db_session, org)
    wp = svc.create_work_package(organization_id=org.id, name="Linked", plateau_id=plateau.id,
                                 gap_id=gap.id)
    db_session.commit()
    login_as(client, user)
    path = "/capability-map/api/roadmap/work-packages/%s" % wp.id

    shown = client.get(path).get_json()["work_package"]
    assert shown["plateau_id"] == plateau.id and shown["gap_id"] == gap.id
    assert shown["plateau_ids"] == [plateau.id] and shown["gap_ids"] == [gap.id]

    shown["name"] = "Renamed in the modal"  # the modal sends back what it was given
    resp = _json(client, "put", path, shown)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _links(db_session, org, wp) == {"plateau_ids": [plateau.id], "gap_ids": [gap.id]}
    assert len(_relationships_of(wp)) == 2

    # The plateau filter, timeline grouping and gap column read the singular keys.
    listed = client.get("/capability-map/api/roadmap/work-packages?root_only=false").get_json()
    row = [w for w in listed["work_packages"] if w["id"] == wp.id][0]
    assert row["name"] == "Renamed in the modal"
    assert row["plateau_id"] == plateau.id and row["gap_id"] == gap.id
    by_gap = client.get("/capability-map/api/roadmap/work-packages?gap_id=%s&root_only=false" % gap.id).get_json()
    assert [w["id"] for w in by_gap["work_packages"]] == [wp.id]

    # A request that does not carry a link key leaves the links alone; an explicit
    # null clears that one.
    assert _json(client, "put", path, {"name": "Only a name"}).status_code == 200
    assert _links(db_session, org, wp) == {"plateau_ids": [plateau.id], "gap_ids": [gap.id]}
    assert _json(client, "put", path, {"gap_id": None}).status_code == 200
    assert _links(db_session, org, wp) == {"plateau_ids": [plateau.id], "gap_ids": []}


def test_modal_round_trip_keeps_a_second_plateau(db_session, make_org):
    """The modal shows one plateau (the first). Sending it back unchanged leaves the
    others of the work package alone."""
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "n301b")
    first, second = _plateau(db_session, org), _plateau(db_session, org, "Second")
    wp = svc.create_work_package(organization_id=org.id, name="Two plateaus", plateau_id=first.id)
    svc._apply_links(wp, svc._resolve_links({"plateau_id": second.id}, org.id), org.id, replace=False)
    assert _links(db_session, org, wp)["plateau_ids"] == [first.id, second.id]
    shown = svc.to_roadmap_dict(wp, org.id)
    svc.update_work_package(wp.id, organization_id=org.id, plateau_id=shown["plateau_id"])
    assert _links(db_session, org, wp)["plateau_ids"] == [first.id, second.id]
    svc.update_work_package(wp.id, organization_id=org.id, plateau_id=second.id)
    assert _links(db_session, org, wp)["plateau_ids"] == [second.id]


# -- N3-02: a deploy never brings back a moved or cleared link ---------------


def test_deploy_does_not_restore_moved_or_cleared_links(app, db_session, make_org):
    from app.services import work_package_bridge
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "n302")
    p1, p2 = _plateau(db_session, org, "P1"), _plateau(db_session, org, "P2")
    gap = _gap(db_session, org)
    legacy = _legacy(db_session, org, "Old store row", plateau_id=p1.id, status="planned")
    copy = _copy("work_packages", legacy.id, org)
    svc.update_work_package(copy.id, organization_id=org.id, gap_id=gap.id)
    assert _links(db_session, org, copy) == {"plateau_ids": [p1.id], "gap_ids": [gap.id]}
    # A row written before this round also carries the values in the columns.
    _raw_columns(db_session, copy, plateau_id=p1.id, gap_id=gap.id)
    db_session.commit()

    with work_package_bridge.suspended():
        out = _merge(app)  # the first deploy migrates the columns once ...
    assert "link columns set to NULL" in out
    db_session.expire_all()
    assert _links(db_session, org, copy) == {"plateau_ids": [p1.id], "gap_ids": [gap.id]}

    # ... then the work package is moved and cleared on the new screens.
    svc.update_work_package(copy.id, organization_id=org.id, plateau_id=p2.id, gap_id=None)
    db_session.commit()
    with work_package_bridge.suspended():
        _merge(app)
        _merge(app)
    assert _links(db_session, org, copy) == {"plateau_ids": [p2.id], "gap_ids": []}

    # An old-screen status-only edit restores nothing either.
    legacy.status = "in_progress"
    db_session.flush()
    assert _links(db_session, org, copy) == {"plateau_ids": [p2.id], "gap_ids": []}


def test_new_screen_link_change_survives_old_screen_status_edit(db_session, make_org):
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "n302b")
    p1, p2 = _plateau(db_session, org, "P1"), _plateau(db_session, org, "P2")
    gap = _gap(db_session, org)
    legacy = _legacy(db_session, org, "Old store row", plateau_id=p1.id, status="planned")
    copy = _copy("work_packages", legacy.id, org)
    svc.update_work_package(copy.id, organization_id=org.id, gap_id=gap.id)
    assert _links(db_session, org, copy) == {"plateau_ids": [p1.id], "gap_ids": [gap.id]}

    # Moved and cleared on the new screens.
    svc.update_work_package(copy.id, organization_id=org.id, plateau_id=p2.id, gap_id=None)
    db_session.flush()
    assert _links(db_session, org, copy) == {"plateau_ids": [p2.id], "gap_ids": []}

    # An old-screen status-only edit (bridged) restores nothing.
    legacy.status = "in_progress"
    db_session.flush()
    assert _links(db_session, org, copy) == {"plateau_ids": [p2.id], "gap_ids": []}
    legacy.status = "on_hold"
    db_session.flush()
    assert _links(db_session, org, copy) == {"plateau_ids": [p2.id], "gap_ids": []}


# -- N3-03: an old screen's move or clear replaces the link -------------------


def test_old_screen_plateau_move_and_clear_replace_links(db_session, make_org):
    org, _user = _org_with_user(db_session, make_org, "n303")
    p1, p2 = _plateau(db_session, org, "P1"), _plateau(db_session, org, "P2")
    legacy = _legacy(db_session, org, "Old store row")
    copy = _copy("work_packages", legacy.id, org)
    assert _links(db_session, org, copy)["plateau_ids"] == []

    legacy.plateau_id = p1.id  # the old screen sets a plateau
    db_session.flush()
    assert _links(db_session, org, copy)["plateau_ids"] == [p1.id]

    legacy.plateau_id = p2.id  # and moves it
    db_session.flush()
    assert _links(db_session, org, copy)["plateau_ids"] == [p2.id]

    legacy.plateau_id = None  # and clears it
    db_session.flush()
    assert _links(db_session, org, copy)["plateau_ids"] == []
    assert _relationships_of(copy) == []

    legacy.plateau = p1  # set through the relationship rather than the id
    db_session.flush()
    assert _links(db_session, org, copy)["plateau_ids"] == [p1.id]


def test_new_old_store_row_with_a_plateau_creates_the_link(db_session, make_org):
    org, _user = _org_with_user(db_session, make_org, "n303b")
    p1 = _plateau(db_session, org, "P1")
    legacy = _legacy(db_session, org, "Created with a plateau", plateau_id=p1.id)
    copy = _copy("work_packages", legacy.id, org)
    assert _links(db_session, org, copy)["plateau_ids"] == [p1.id]
    db_session.refresh(copy)
    assert copy.plateau_id is None and copy.gap_id is None, "the bridge no longer fills the columns"


# -- N3-04: the L5 answer reads the relationships ------------------------------


def test_l5_answer_reads_relationships(app, db_session, make_org, monkeypatch):
    from flask import g

    from app.modules.intelligence.services import query_service
    from app.modules.intelligence.services.query_service import IntelligenceQueryService
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "n304")
    plateau, gap = _plateau(db_session, org), _gap(db_session, org)
    wp = svc.create_work_package(organization_id=org.id, name="Linked", plateau_id=plateau.id,
                                 gap_id=gap.id)
    bare = svc.create_work_package(organization_id=org.id, name="Not linked")
    db_session.commit()
    monkeypatch.setattr(query_service, "current_org_id", lambda: org.id)

    with app.test_request_context("/"):
        g.current_org_id = org.id
        answer = IntelligenceQueryService.programme_for_element(wp.archimate_element_id)
        none = IntelligenceQueryService.programme_for_element(bare.archimate_element_id)
    block = answer["work_packages"][0]
    assert block["plateau"]["plateau_id"] == plateau.id and block["plateau"]["reason"] is None
    assert block["gap"]["gap_id"] == gap.id and block["gap"]["reason"] is None
    assert none["work_packages"][0]["plateau"]["reason"] == "no_plateau_recorded"
    assert none["work_packages"][0]["gap"]["reason"] == "no_gap_recorded"


# -- N3-02, N3-10: the columns are migrated once, then never read -----------------


def test_link_columns_null_after_migration_and_never_read(app, db_session, make_org, bridge_off_round4):
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "n310a")
    other = make_org("n310a-other")
    plateau, gap = _plateau(db_session, org), _gap(db_session, org)
    foreign = _plateau(db_session, other)
    rows = [
        UnifiedWorkPackage(name="Both", organization_id=org.id, plateau_id=plateau.id, gap_id=gap.id),
        UnifiedWorkPackage(name="Foreign", organization_id=org.id, plateau_id=foreign.id),
        UnifiedWorkPackage(name="No organisation", organization_id=None, plateau_id=plateau.id),
    ]
    db_session.add_all(rows)
    db_session.flush()
    db_session.commit()

    dry = _merge(app, "--dry-run")
    assert "link columns to migrate: 2" in dry
    db_session.expire_all()
    assert rows[0].plateau_id == plateau.id, "a dry run changes nothing"

    out = _merge(app)
    assert "link columns to migrate: 2" in out and "link columns set to NULL: 2" in out
    db_session.expire_all()
    assert rows[0].plateau_id is None and rows[0].gap_id is None
    assert rows[1].plateau_id is None
    assert rows[2].plateau_id == plateau.id, "a row with no organisation keeps its values"
    assert svc.plateau_and_gap_links([rows[0]], org.id)[rows[0].id] == {
        "plateau_ids": [plateau.id], "gap_ids": [gap.id]}
    assert svc.plateau_and_gap_links([rows[1]], org.id)[rows[1].id]["plateau_ids"] == []

    again = _merge(app)
    assert "link columns" not in again and "relationships created" not in again

    # Nothing under app/ reads the unified columns after the migration.
    pattern = re.compile(r"\.(plateau_id|gap_id)\b")
    for name in ("app/services/work_package_service.py",
                 "app/modules/intelligence/services/query_service.py",
                 "app/modules/capabilities/routes/roadmap_routes.py",
                 "app/main/routes_archimate_roadmap.py",
                 "app/services/work_package_bridge.py"):
        hits = [line for line in (ROOT / name).read_text(encoding="utf-8").splitlines()
                if pattern.search(line) and ".c." not in line and not line.lstrip().startswith("#")]
        assert hits == [], (name, hits)


@pytest.fixture
def bridge_off_round4():
    from app.services import work_package_bridge

    with work_package_bridge.suspended():
        yield


def test_one_link_reader(db_session, make_org):
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "n310b")
    other = make_org("n310b-other")
    plateau, gap = _plateau(db_session, org), _gap(db_session, org)
    wp = svc.create_work_package(organization_id=org.id, name="Mine", plateau_id=plateau.id,
                                 gap_id=gap.id)
    theirs = svc.create_work_package(organization_id=other.id, name="Theirs",
                                     plateau_id=_plateau(db_session, other).id)

    # Every reader returns the same links for the same work package.
    assert svc.plateau_and_gap_links([wp], org.id)[wp.id] == {
        "plateau_ids": [plateau.id], "gap_ids": [gap.id]}
    assert svc.to_dict(wp)["plateau_ids"] == [plateau.id]
    assert svc.to_dicts([wp])[0]["gap_ids"] == [gap.id]
    shown = svc.to_roadmap_dict(wp, org.id)
    assert (shown["plateau_ids"], shown["gap_ids"]) == ([plateau.id], [gap.id])
    assert svc.work_package_ids_for_gap(gap.id, org.id) == [wp.id]
    assert svc.plateau_work_package_ids([plateau.id], org.id) == {plateau.id: {wp.id}}
    assert svc._link_targets(wp, "plateau_id", org.id)[0][1] == plateau.id
    assert [row[0] for row in svc._link_rows(org.id)] == [wp.id, wp.id]
    assert [row[0] for row in svc._link_rows(org.id, plateau_ids=[plateau.id])] == [wp.id]
    assert [row[0] for row in svc._link_rows(org.id, gap_ids=[gap.id])] == [wp.id]

    # Organisation B's relationships never appear for organisation A, and the reverse.
    assert svc.plateau_and_gap_links([theirs], org.id)[theirs.id] == {"plateau_ids": [], "gap_ids": []}
    assert svc.work_package_ids_for_gap(gap.id, other.id) == []
    assert svc.plateau_work_package_ids([plateau.id], other.id) == {plateau.id: set()}
    assert svc._link_rows(None) == []
    assert theirs.id not in {row[0] for row in svc._link_rows(org.id)}

    # A relationship that points into another organisation's plateau is not shown.
    from app.models.models import ArchiMateRelationship

    foreign_plateau = _plateau(db_session, other, "Foreign")
    svc._ensure_element(foreign_plateau)
    db_session.add(ArchiMateRelationship(
        source_id=wp.archimate_element_id, target_id=foreign_plateau.archimate_element_id,
        type="realization", organization_id=org.id))
    db_session.flush()
    assert svc.plateau_and_gap_links([wp], org.id)[wp.id]["plateau_ids"] == [plateau.id]

    # Linking without an organisation is refused.
    with pytest.raises(svc.WorkPackageError):
        svc._apply_links(wp, {"plateau_id": plateau}, None)


# -- N3-05: the link step runs on the session that flushed ---------------------


def test_link_step_uses_flushing_session(db_session, make_org, monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app import db
    from app.models.implementation_migration import WorkPackage
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "n305")
    plateau = _plateau(db_session, org)
    org_id, plateau_id = org.id, plateau.id
    db_session.commit()
    assert not db.session.registry().in_transaction()

    seen = []
    original = svc._apply_links

    def spy(wp, links, organization_id, *args, **kwargs):
        seen.append(db.session.registry())
        return original(wp, links, organization_id, *args, **kwargs)

    monkeypatch.setattr(svc, "_apply_links", spy)

    application_session = db.session.registry()
    other = Session(bind=db_session.get_bind(), join_transaction_mode="create_savepoint")
    try:
        row = WorkPackage(name="Written through another session", organization_id=org_id,
                          plateau_id=plateau_id)
        other.add(row)
        other.flush()
        copied = other.execute(
            text("SELECT id, archimate_element_id FROM unified_work_packages "  # tenancy-ok: test
                 "WHERE source_table = 'work_packages' AND source_id = :i"), {"i": row.id}).first()
        assert copied is not None
        made = other.execute(
            text("SELECT count(*) FROM archimate_relationships WHERE source_id = :e "  # tenancy-ok: test
                 "AND type = 'realization' AND organization_id = :o"),
            {"e": copied[1], "o": org_id}).scalar()
        assert made == 1, "the link was written in the session that flushed"
        assert seen and all(s is other for s in seen), "the link step ran on the flushing session"
    finally:
        other.rollback()
        other.close()
    assert db.session.registry() is application_session, "the application's session is back in place"
    assert not application_session.in_transaction(), "db.session never joined that transaction"


def test_using_session_restores_registry_on_every_path(db_session):
    from sqlalchemy.orm import Session

    from app import db
    from app.services import work_package_bridge as bridge

    application_session = db.session.registry()
    other = Session(bind=db_session.get_bind())
    try:
        try:
            with bridge._using_session(other):
                assert db.session.registry() is other
                raise RuntimeError("escapes the savepoint")
        except RuntimeError:
            pass
        assert db.session.registry() is application_session
        # No session in the registry beforehand: it is left empty, not holding the other one.
        db.session.registry.clear()
        with bridge._using_session(other):
            assert db.session.registry() is other
        assert not db.session.registry.has()
    finally:
        other.close()
        db.session.registry.set(application_session)


# -- N3-06: a dependency removed on a new screen stays removed -------------------


def test_roadmap_dependency_removed_stays_removed(db_session, make_org):
    from app.models.roadmap_models import RoadmapWorkPackage
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "n306")
    rows = {}
    for name in ("one", "two", "three", "four"):
        rows[name] = RoadmapWorkPackage(name=name, business_capability="Cap", created_by=user.id)
        db_session.add(rows[name])
    db_session.flush()
    rows["three"].dependencies = [rows["one"], rows["two"]]
    db_session.flush()
    copies = {n: _copy("roadmap_work_packages", r.id, org) for n, r in rows.items()}
    db_session.refresh(copies["three"])
    assert sorted(svc.dependency_ids(copies["three"])) == sorted([copies["one"].id, copies["two"].id])

    # The new screens remove one dependency.
    svc.remove_dependency(copies["three"].id, copies["two"].id, organization_id=org.id)
    db_session.flush()
    assert svc.dependency_ids(copies["three"]) == [copies["one"].id]

    # An unrelated old-screen dependency edit adds one: only that one arrives.
    rows["three"].dependencies = [rows["one"], rows["two"], rows["four"]]
    db_session.flush()
    db_session.refresh(copies["three"])
    assert sorted(svc.dependency_ids(copies["three"])) == sorted([copies["one"].id, copies["four"].id])

    # Removing one on the old screen removes just that one.
    rows["three"].dependencies = [rows["one"], rows["two"]]
    db_session.flush()
    db_session.refresh(copies["three"])
    assert svc.dependency_ids(copies["three"]) == [copies["one"].id]


def test_roadmap_capability_edit_is_a_diff(db_session, make_org):
    from app.models.unified_capability import UnifiedCapability
    from app.models.roadmap_models import RoadmapWorkPackage
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "n306b")
    caps = []
    for name in ("Cap A", "Cap B"):
        cap = UnifiedCapability(name="%s %s" % (name, uuid.uuid4().hex[:6]), organization_id=org.id)
        db_session.add(cap)
        caps.append(cap)
    db_session.flush()
    row = RoadmapWorkPackage(name="caps", business_capability="Cap", created_by=user.id)
    db_session.add(row)
    db_session.flush()
    copy = _copy("roadmap_work_packages", row.id, org)
    svc.update_work_package(copy.id, organization_id=org.id, capability_ids=[caps[0].id])
    db_session.flush()
    row.capabilities = [caps[1]]  # the old screen links a capability
    db_session.flush()
    db_session.refresh(copy)
    assert sorted(copy.capability_ids) == sorted([caps[0].id, caps[1].id])
    row.capabilities = []
    db_session.flush()
    db_session.refresh(copy)
    assert copy.capability_ids == [caps[0].id]


# -- N3-08: a link failure rolls back only the links --------------------------


def test_link_failure_rolls_back_only_links(db_session, make_org, monkeypatch, caplog):
    from app.models.implementation_migration import Plateau
    from app.modules.architecture.services import archimate_relationship_service as rel_service

    org, _user = _org_with_user(db_session, make_org, "n308")
    plateau = _plateau(db_session, org)
    survivor = _plateau(db_session, org, "Written before the failure")

    def boom(*args, **kwargs):
        raise RuntimeError("relationship store unavailable")

    monkeypatch.setattr(rel_service.ArchiMateRelationshipService, "create_relationship", boom)
    with caplog.at_level(logging.WARNING):
        legacy = _legacy(db_session, org, "Old store row", plateau_id=plateau.id)
    copy = _copy("work_packages", legacy.id, org)
    assert copy is not None, "the copy is made; only its links are rolled back"
    assert _relationships_of(copy) == []
    messages = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
    assert str(copy.id) in messages and "relationship store unavailable" in messages
    assert str(plateau.id) in messages

    # The outer transaction is still usable and holds what was written before.
    assert db_session.get(Plateau, survivor.id) is not None
    extra = _plateau(db_session, org, "Written after the failure")
    db_session.flush()
    assert db_session.get(Plateau, extra.id) is not None
    assert _legacy(db_session, org, "Another row") is not None


def test_other_org_plateau_link_is_logged(db_session, make_org, caplog):
    org, _user = _org_with_user(db_session, make_org, "n308b")
    other = make_org("n308b-other")
    theirs = _plateau(db_session, other, "Foreign")
    with caplog.at_level(logging.WARNING):
        legacy = _legacy(db_session, org, "Points at another organisation", plateau_id=theirs.id)
    copy = _copy("work_packages", legacy.id, org)
    assert _relationships_of(copy) == []
    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(str(copy.id) in m and str(theirs.id) in m and "another organisation" in m
               for m in messages), messages


# -- N3-09: a bulk edit that changes no link does not go to the link step ----------


def test_bulk_priority_edit_does_not_touch_links(db_session, make_org, monkeypatch, capsys):
    from sqlalchemy import event

    from app import db
    from app.commands import consolidate_work_packages as consolidate
    from app.models.implementation_migration import WorkPackage
    from app.services import work_package_bridge

    org, _user = _org_with_user(db_session, make_org, "n309")
    plateau = _plateau(db_session, org)
    db_session.commit()

    def _insert(n):
        rows = [WorkPackage(name="Bulk %s" % i, organization_id=org.id, plateau_id=plateau.id,
                            priority="medium") for i in range(n)]
        db_session.add_all(rows)
        db_session.flush()
        return rows

    started = time.perf_counter()
    with work_package_bridge.suspended():
        _insert(300)
    plain = time.perf_counter() - started

    started = time.perf_counter()
    rows = _insert(300)
    bridged = time.perf_counter() - started

    calls = []
    real = consolidate.apply_link_changes
    monkeypatch.setattr(consolidate, "apply_link_changes",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    statements = []

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def edited(batch, priority, bridge=True):
        statements.clear()
        for row in batch:
            row.priority = priority
        if bridge:
            db_session.flush()
        else:
            with work_package_bridge.suspended():
                db_session.flush()
        return len(statements)

    event.listen(db.engine, "before_cursor_execute", count)
    try:
        with_bridge = edited(rows, "high")
        without_bridge = edited(rows, "low", bridge=False)
        small_bridged = edited(rows[:30], "high")
    finally:
        event.remove(db.engine, "before_cursor_execute", count)

    with capsys.disabled():
        print("\nROUND4 TIMINGS: 300 inserts with a plateau, bridge suspended %.2fs, bridged %.2fs; "
              "priority edit: %d statements for 300 rows bridged, %d for 30 rows bridged, "
              "%d for 300 rows with the bridge suspended"
              % (plain, bridged, with_bridge, small_bridged, without_bridge))
    assert calls == [], "the link step is not called for a priority-only edit"
    # The bridge costs a fixed handful of statements per flush, not some per row.
    assert with_bridge == small_bridged, (with_bridge, small_bridged)
    assert with_bridge <= without_bridge + 8, (with_bridge, without_bridge)


# -- N3-11, N3-12 ------------------------------------------------------------------


def test_enterprise_list_summary(db_session, make_org, client, login_as):
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "n311")
    svc.create_work_package(organization_id=org.id, name="Has both", summary="Short summary",
                            description="A long description of the work")
    db_session.commit()
    login_as(client, user)
    body = client.get("/enterprise/api/work-packages").get_json()
    item = [i for i in body["work_packages"] if i["name"] == "Has both"][0]
    assert item["summary"] == "Short summary"


def test_deliverable_merge_keeps_provenance(app, db_session, make_org, bridge_off_round4):
    from app.models.implementation_migration import Deliverable
    from app.models.roadmap_models import RoadmapDeliverable
    from app.services import work_package_service as svc
    from tests.test_work_package_consolidation import _app_component

    org, user = _org_with_user(db_session, make_org, "n312")
    wp = svc.create_work_package(organization_id=org.id, name="Owner")
    component = _app_component(db_session, org)
    db_session.add(RoadmapDeliverable(
        name="Generated for an application", organization_id=org.id,
        unified_work_package_id=wp.id, status="planned", auto_generated=True,
        generation_method="application_analysis", source_application_id=component.id,
        created_by=user.id, updated_by=user.id))
    db_session.flush()
    db_session.commit()

    out = _merge(app)
    assert "roadmap_deliverables: copied: 1" in out
    copy = Deliverable.query.filter_by(name="Generated for an application").one()
    assert copy.auto_generated is True and copy.generation_method == "application_analysis"
    assert copy.created_by == user.id and copy.updated_by == user.id
    shown = svc.deliverable_to_roadmap_dict(copy)
    assert shown["auto_generated"] is True
    assert shown["generation_method"] == "application_analysis"
    assert shown["source_application_id"] == component.id

    plain = svc.create_deliverable(wp.id, organization_id=org.id, name="Made by hand")
    shown = svc.deliverable_to_roadmap_dict(plain)
    assert shown["auto_generated"] is False and shown["source_application_id"] is None
