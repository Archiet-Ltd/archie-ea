"""R1-B04 PR 2 fix round 9: defects R8-01 to R8-07 of the eighth review of PR 421.

One ArchiMate element per work package, by one rule (element_refusal_sql in
work_package_service) and one partial unique index. R8-01 the deploy cleans existing copies before
links are migrated; R8-03 the index, its migration and the race; R8-02 a move takes only this
copy's links; R8-04 clearing the element moves the copy to its own; R8-05 the service uses the
same rule; R8-06 the warning names the condition; R8-07 the earliest old row takes a free element.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import threading
import time
import uuid
from pathlib import Path

import pytest

from tests.test_work_package_round3 import (
    _copy,
    _gap,
    _legacy,
    _merge,
    _org_with_user,
    _plateau,
    bridge_off,  # noqa: F401  (fixture)
)
from tests.test_work_package_round5 import _links, _raw_association, _scalar
from tests.test_work_package_round6 import _element

pytestmark = pytest.mark.usefixtures("db_session")

ROOT = Path(__file__).resolve().parents[1]
INDEX = "uq_unified_wp_archimate_element"


def _wp_element(db_session, org, name="Free work package element"):
    return _element(db_session, org, name, element_type="WorkPackage")


def _drop_index(db_session):
    """The state before the migration: the index is absent (rolled back with the test)."""
    from sqlalchemy import text

    db_session.execute(text(f"DROP INDEX IF EXISTS {INDEX}"))  # tenancy-ok: test fixture


def _unified(db_session, org, legacy, element_id):
    """A copy as main's merge left it: the element copied across unchecked."""
    from app.models.unified_work_package import UnifiedWorkPackage

    row = UnifiedWorkPackage(
        name=legacy.name, organization_id=org.id, source_table="work_packages",
        source_id=legacy.id, archimate_element_id=element_id)
    db_session.add(row)
    db_session.flush()
    return row


def _plateau_of(db_session, org, legacy):
    plateau = _plateau(db_session, org)
    _raw_association(db_session, "plateau", legacy.id, plateau.id)
    return plateau


def _element_row(db_session, element_id):
    from app.models.archimate_core import ArchiMateElement

    return db_session.get(ArchiMateElement, element_id)


# -- R8-01: the deploy cleans every existing copy before links are migrated --------


def test_r9_deploy_clears_shared_component_element(app, db_session, make_org, bridge_off):  # noqa: F811
    from app.commands.consolidate_work_packages import clear_untaken_elements

    org, _user = _org_with_user(db_session, make_org, "r901a")
    _drop_index(db_session)
    shared = _element(db_session, org, "Shared component")  # an ApplicationComponent element
    first, second = _legacy(db_session, org, "First"), _legacy(db_session, org, "Second")
    plateau_1, plateau_2 = _plateau_of(db_session, org, first), _plateau_of(db_session, org, second)
    shared_id = shared.id
    copy_1 = _unified(db_session, org, first, shared_id)
    copy_2 = _unified(db_session, org, second, shared_id)
    ids = (copy_1.id, copy_2.id)
    db_session.commit()

    clear_untaken_elements(db_session)  # the migration's step; the deploy commands no longer run it
    db_session.commit()
    _merge(app)

    db_session.expire_all()
    copies = [_copy("work_packages", row.id, org) for row in (first, second)]
    assert [c.id for c in copies] == list(ids)
    elements = [c.archimate_element_id for c in copies]
    assert None not in elements and len(set(elements)) == 2 and shared_id not in elements
    for element_id in elements:
        assert _element_row(db_session, element_id).type == "WorkPackage"
    assert _links(db_session, org, copies[0])["plateau_ids"] == [plateau_1.id]
    assert _links(db_session, org, copies[1])["plateau_ids"] == [plateau_2.id]
    assert _element_row(db_session, shared_id) is not None  # nothing was deleted


def test_r9_deploy_clears_other_org_element(app, db_session, make_org, bridge_off):  # noqa: F811
    from app.commands.consolidate_work_packages import clear_untaken_elements

    org, _user = _org_with_user(db_session, make_org, "r902a")
    other = make_org("r902b")
    _drop_index(db_session)
    foreign = _wp_element(db_session, other, "Their element")
    legacy = _legacy(db_session, org, "Old row")
    plateau = _plateau_of(db_session, org, legacy)
    foreign_id = foreign.id
    copy = _unified(db_session, org, legacy, foreign_id)
    copy_id = copy.id
    db_session.commit()

    clear_untaken_elements(db_session)  # the migration's step; the deploy commands no longer run it
    db_session.commit()
    _merge(app)

    db_session.expire_all()
    copy = _copy("work_packages", legacy.id, org)
    assert copy.id == copy_id
    assert copy.archimate_element_id not in (None, foreign_id)
    assert _scalar(db_session, "SELECT count(*) FROM archimate_relationships WHERE organization_id = %s "
                   "AND source_id = %s" % (org.id, foreign_id)) == 0
    assert _links(db_session, org, copy)["plateau_ids"] == [plateau.id]


# -- R8-03: a partial unique index --------------------------------------------------


def test_r9_element_unique_index_exists(db_session):
    definition = _scalar(db_session, "SELECT indexdef FROM pg_indexes WHERE tablename = "
                         "'unified_work_packages' AND indexname = '%s'" % INDEX)
    assert definition, "the index is missing"
    assert "UNIQUE" in definition and "archimate_element_id" in definition
    assert re.search(r"WHERE \(?\(?archimate_element_id IS NOT NULL", definition), definition


def _load_revision():
    path = ROOT / "migrations/versions/20261008_uwp_element_unique.py"
    spec = importlib.util.spec_from_file_location("uwp_element_unique_revision", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(connection):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    revision = _load_revision()
    with Operations.context(MigrationContext.configure(connection)):
        revision.upgrade()
    return revision


def test_r9_migration_cleans_then_indexes_and_is_idempotent(db_session, make_org, bridge_off):  # noqa: F811
    org, _user = _org_with_user(db_session, make_org, "r903a")
    _drop_index(db_session)
    shared = _wp_element(db_session, org)
    rows = [_legacy(db_session, org, "Row %s" % i) for i in range(3)]
    shared_id = shared.id
    copies = [_unified(db_session, org, row, shared_id) for row in rows]
    copy_ids = [c.id for c in copies]

    connection = db_session.connection()
    revision = _run_upgrade(connection)
    _run_upgrade(connection)  # a second run changes nothing

    db_session.expire_all()
    held = _scalar(db_session, "SELECT array_agg(id ORDER BY id) FROM unified_work_packages "
                   "WHERE archimate_element_id = %s" % shared_id)
    assert held == [min(copy_ids)]
    assert _scalar(db_session, "SELECT count(*) FROM (SELECT archimate_element_id FROM "
                   "unified_work_packages WHERE archimate_element_id IS NOT NULL GROUP BY 1 "
                   "HAVING count(*) > 1) d") == 0
    assert _scalar(db_session, "SELECT count(*) FROM pg_indexes WHERE indexname = '%s'" % INDEX) == 1
    assert revision.revision == "20261008_uwp_element_unique"
    assert _element_row(db_session, shared_id) is not None


def test_r9_concurrent_creates_one_takes_element(app):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app import db
    from app.models.implementation_migration import WorkPackage
    from app.models.organization import Organization
    from app.services.archimate_backbone import create_backbone_element

    engine = db.engine
    suffix = uuid.uuid4().hex[:10]
    org_id = None
    try:
        with app.app_context(), Session(engine) as setup:
            org = Organization(name="Race %s" % suffix, slug="race-%s" % suffix)
            setup.add(org)
            setup.flush()
            org_id = org.id
            element = create_backbone_element(
                element_type="WorkPackage", layer="Implementation", name="Race element %s" % suffix,
                organization_id=org_id, session=setup)
            legacy = [WorkPackage(name="Race %s %s" % (i, suffix), organization_id=org_id) for i in (1, 2)]
            setup.add_all(legacy)
            setup.flush()
            element_id, legacy_ids = element.id, [row.id for row in legacy]
            setup.commit()

        results = [{"flushed": threading.Event(), "release": threading.Event()} for _ in legacy_ids]

        def worker(legacy_id, result):
            with app.app_context(), Session(engine) as session:
                try:
                    row = session.get(WorkPackage, legacy_id)
                    row.archimate_element_id = element_id
                    session.flush()  # the second one waits here for the first to commit
                    result["flushed"].set()
                    result["release"].wait(timeout=60)
                    session.commit()
                except Exception as exc:  # noqa: BLE001
                    result["error"] = exc
                    session.rollback()
                    result["flushed"].set()

        threads = [threading.Thread(target=worker, args=(legacy_ids[0], results[0]), daemon=True)]
        threads[0].start()
        assert results[0]["flushed"].wait(timeout=60)
        threads.append(threading.Thread(target=worker, args=(legacy_ids[1], results[1]), daemon=True))
        threads[1].start()
        time.sleep(2)
        assert not results[1]["flushed"].is_set(), "the second create should wait on the index"
        results[0]["release"].set()
        assert results[1]["flushed"].wait(timeout=60)
        results[1]["release"].set()
        for thread in threads:
            thread.join(timeout=60)

        assert not any("error" in r for r in results), [r.get("error") for r in results]
        with Session(engine) as check:
            holders = check.execute(text(
                "SELECT source_id FROM unified_work_packages WHERE organization_id = :o "  # tenancy-ok: test fixture
                "AND archimate_element_id = :e"), {"o": org_id, "e": element_id}).fetchall()
            copies = check.execute(text(
                "SELECT count(*) FROM unified_work_packages WHERE organization_id = :o"),  # tenancy-ok: test fixture
                {"o": org_id}).scalar()
        assert [r[0] for r in holders] == [legacy_ids[0]]
        assert copies == 2
    finally:
        if org_id is not None:
            with Session(engine) as cleanup:
                for sql in (
                    "DELETE FROM unified_work_packages WHERE organization_id = :o",  # tenancy-ok: test cleanup
                    "DELETE FROM work_packages WHERE organization_id = :o",  # tenancy-ok: test cleanup
                    "DELETE FROM archimate_elements WHERE organization_id = :o",  # tenancy-ok: test cleanup
                    "DELETE FROM organizations WHERE id = :o",
                ):
                    cleanup.execute(text(sql), {"o": org_id})
                cleanup.commit()


# -- R8-02: a move takes only this copy's links --------------------------------------


def test_r9_move_takes_only_this_copys_links(db_session, make_org):
    from app.models.models import ArchiMateRelationship
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "r904")
    plateau_a, plateau_b = _plateau(db_session, org), _plateau(db_session, org)
    legacy_a, legacy_b = _legacy(db_session, org, "A"), _legacy(db_session, org, "B")
    copy_a, copy_b = _copy("work_packages", legacy_a.id, org), _copy("work_packages", legacy_b.id, org)
    svc.update_work_package(copy_a.id, organization_id=org.id, plateau_id=plateau_a.id)
    svc.update_work_package(copy_b.id, organization_id=org.id, plateau_id=plateau_b.id)
    element_a, element_b = copy_a.archimate_element_id, copy_b.archimate_element_id
    assert element_a != element_b
    a_ids = sorted(r.id for r in ArchiMateRelationship.query.filter_by(source_id=element_a))
    b_ids = sorted(r.id for r in ArchiMateRelationship.query.filter_by(source_id=element_b))
    assert a_ids and b_ids
    free = _wp_element(db_session, org)

    legacy_a.archimate_element_id = free.id
    db_session.flush()
    db_session.expire_all()

    copy_a, copy_b = _copy("work_packages", legacy_a.id, org), _copy("work_packages", legacy_b.id, org)
    assert copy_a.archimate_element_id == free.id and copy_b.archimate_element_id == element_b
    assert _links(db_session, org, copy_a)["plateau_ids"] == [plateau_a.id]
    assert _links(db_session, org, copy_b)["plateau_ids"] == [plateau_b.id]
    assert sorted(r.id for r in ArchiMateRelationship.query.filter_by(source_id=free.id)) == a_ids
    assert sorted(r.id for r in ArchiMateRelationship.query.filter_by(source_id=element_b)) == b_ids
    assert ArchiMateRelationship.query.filter_by(source_id=element_a).count() == 0


# -- R8-04: clearing the element moves the copy to its own element -------------------


def test_r9_cleared_element_moves_links_to_own_element(db_session, make_org, client, login_as):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.models import ArchiMateRelationship
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "r905")
    plateau, gap = _plateau(db_session, org), _gap(db_session, org)
    component = ApplicationComponent(name="Billing", organization_id=org.id)
    db_session.add(component)
    db_session.flush()
    from datetime import date

    element = _wp_element(db_session, org)
    legacy = _legacy(db_session, org, "Old row", application_component_id=component.id,
                     start_date=date(2030, 1, 1), target_date=date(2030, 6, 1),
                     archimate_element_id=element.id)
    copy = _copy("work_packages", legacy.id, org)
    assert copy.archimate_element_id == element.id
    svc.update_work_package(copy.id, organization_id=org.id, plateau_id=plateau.id, gap_id=gap.id)
    old_element = copy.archimate_element_id
    legacy_id, component_id, copy_id, old_id = legacy.id, component.id, copy.id, old_element
    db_session.commit()
    login_as(client, user)

    resp = client.put("/api/applications/%s/work-packages/%s" % (component_id, legacy_id),
                      json={"archimate_element_id": None})
    assert resp.status_code == 200, resp.get_data(as_text=True)[:400]

    db_session.expire_all()
    copy = svc.get_work_package(copy_id, org.id)
    new_element = copy.archimate_element_id
    assert new_element not in (None, old_id)
    element = _element_row(db_session, new_element)
    assert element.type == "WorkPackage" and element.organization_id == org.id
    assert _links(db_session, org, copy) == {"plateau_ids": [plateau.id], "gap_ids": [gap.id]}
    assert ArchiMateRelationship.query.filter_by(source_id=old_id).count() == 0
    assert _element_row(db_session, old_id) is not None  # the left element still exists


# -- R8-05: one validator -----------------------------------------------------------


def test_r9_service_refuses_other_org_type_and_shared_element(db_session, make_org):
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "r906a")
    other = make_org("r906b")
    foreign = _wp_element(db_session, other, "Their element")
    component = _element(db_session, org, "Component element")
    holder = svc.create_work_package(organization_id=org.id, name="Holder")
    mine = svc.create_work_package(organization_id=org.id, name="Mine")
    held = holder.archimate_element_id
    assert held is not None

    cases = (
        (foreign.id, svc.WorkPackageNotFound),
        (component.id, svc.WorkPackageError),
        (held, svc.WorkPackageError),
    )
    for element_id, error in cases:
        with pytest.raises(error) as created:
            svc.create_work_package(organization_id=org.id, name="New", archimate_element_id=element_id)
        assert type(created.value) is error
        with pytest.raises(error) as updated:
            svc.update_work_package(mine.id, organization_id=org.id, archimate_element_id=element_id)
        assert type(updated.value) is error
    # The refusals changed nothing, and a free element of the organisation is taken.
    free = _wp_element(db_session, org)
    svc.update_work_package(mine.id, organization_id=org.id, archimate_element_id=free.id)
    assert svc.get_work_package(mine.id, org.id).archimate_element_id == free.id
    assert svc.element_refusal(free.id, org.id, mine.id) is None
    assert svc.element_refusal(held, org.id, holder.id) is None


def test_r9_one_element_rule():
    files = ("app/commands/consolidate_work_packages.py", "app/services/work_package_service.py",
             "app/services/work_package_bridge.py")
    sources = {name: (ROOT / name).read_text(encoding="utf-8") for name in files}
    assert "_element_taken_sql" not in "".join(sources.values())
    service = sources["app/services/work_package_service.py"]
    start = service.index("def element_refusal_sql(")
    end = service.index("\ndef ", start + 1)
    rule = service[start:end]
    assert "'WorkPackage'" in rule and "unified_work_packages ow" in rule
    rest = {name: text.replace(rule, "") for name, text in sources.items()}
    for name, text in rest.items():
        assert "'WorkPackage'" not in text, name
        assert "ow.archimate_element_id" not in text, name


# -- R8-06 and R8-07: the warning names the condition; the earliest row wins ---------


def _sync(db_session, legacy_rows):
    """Copy the rows with their elements, as the bridge does for new old-store rows (bridge suspended)."""
    from app.commands import consolidate_work_packages as cwp

    return cwp.sync_source_rows(
        db_session.connection(), "work_packages", [row.id for row in legacy_rows])


def test_r9_refusal_names_condition(db_session, make_org, bridge_off, caplog):  # noqa: F811
    from app.services import work_package_service as svc

    org, _user = _org_with_user(db_session, make_org, "r907a")
    other = make_org("r907b")
    held = svc.create_work_package(organization_id=org.id, name="Holder").archimate_element_id
    rows = [
        _legacy(db_session, org, "Other organisation", archimate_element_id=_wp_element(db_session, other).id),
        _legacy(db_session, org, "Wrong type", archimate_element_id=_element(db_session, org).id),
        _legacy(db_session, org, "Shared", archimate_element_id=held),
    ]
    with caplog.at_level(logging.WARNING):
        stats = _sync(db_session, rows)
    messages = [r.getMessage() for r in caplog.records if "element not taken" in r.getMessage()]
    for condition in ("organisation", "type", "shared"):
        assert sum("condition %s " % condition in m for m in messages) == 1, messages
    assert stats["work_packages: source element not taken"] == 3


def test_r9_earliest_old_row_takes_free_element(db_session, make_org, bridge_off):  # noqa: F811
    org, _user = _org_with_user(db_session, make_org, "r908")
    free = _wp_element(db_session, org)
    rows = [_legacy(db_session, org, "Row %s" % i, archimate_element_id=free.id) for i in range(3)]
    stats = _sync(db_session, list(reversed(rows)))  # given in reverse: the order is the old id's

    db_session.expire_all()
    copies = [_copy("work_packages", row.id, org) for row in rows]
    assert [c.archimate_element_id for c in copies] == [free.id, None, None]
    assert stats["work_packages: source element taken by an earlier row"] == 2


# -- R9-01 / R9-02: the clean-up is the migration's, and skips a copy with no organisation ---


def test_r9_retyped_element_keeps_links_after_link_step(app, db_session, make_org, bridge_off):  # noqa: F811
    """P10: a copy whose element was retyped keeps its plateau and gap links; only the migration cleans."""
    from app.commands.consolidate_work_packages import _Stats, _link_columns_to_relationships

    org, _user = _org_with_user(db_session, make_org, "r910a")
    legacy = _legacy(db_session, org, "Bridged")
    plateau, gap = _plateau_of(db_session, org, legacy), _gap(db_session, org)
    _raw_association(db_session, "gap", legacy.id, gap.id)
    element = _wp_element(db_session, org)
    element_id = element.id
    copy = _unified(db_session, org, legacy, element_id)
    copy_id = copy.id
    element.type = "Deliverable"
    db_session.commit()

    _link_columns_to_relationships(_Stats())

    db_session.expire_all()
    copy = _copy("work_packages", legacy.id, org)
    assert copy.id == copy_id and copy.archimate_element_id == element_id
    links = _links(db_session, org, copy)
    assert links["plateau_ids"] == [plateau.id]
    assert links["gap_ids"] == [gap.id]


def test_r9_copy_without_organisation_keeps_element(db_session, make_org):
    from sqlalchemy import text

    from app.commands.consolidate_work_packages import clear_untaken_elements
    from app.models.unified_work_package import UnifiedWorkPackage

    org = make_org("r911a")
    element = _wp_element(db_session, org)
    element_id = element.id
    row = UnifiedWorkPackage(name="No organisation", organization_id=None,
                             source_table="work_packages", source_id=987911,
                             archimate_element_id=element_id)
    db_session.add(row)
    db_session.flush()
    row_id = row.id
    db_session.execute(text("UPDATE unified_work_packages SET organization_id = NULL WHERE id = :i"),  # tenancy-ok: test fixture
                       {"i": row_id})  # a column default fills an unset organisation on insert
    db_session.expire_all()
    assert db_session.get(UnifiedWorkPackage, row_id).organization_id is None

    assert clear_untaken_elements(db_session) == 0

    db_session.expire_all()
    assert db_session.get(UnifiedWorkPackage, row_id).archimate_element_id == element_id
