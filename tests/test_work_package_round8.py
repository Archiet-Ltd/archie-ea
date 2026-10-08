"""R1-B04 PR 2 fix round 8: defects N7-01 to N7-04 of the seventh review of PR 421.

N7-01 a copy that takes another element moves its plateau and gap relationships with it (no
stray elements, no re-created links); N7-02 one marker writer; N7-03 a source element is taken
only from the copy's own organisation; N7-04 the marker never moves backwards. (N7-06, the
clock seam, is tested in test_work_package_round6.)
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from tests.test_work_package_round3 import (
    _copy,
    _gap,
    _legacy,
    _org_with_user,
    _plateau,
    _relationships_of,
)
from tests.test_work_package_round5 import _links, _marker, _raw_association, _scalar
from tests.test_work_package_round6 import _element

ROOT = Path(__file__).resolve().parents[1]


def _sources_linked_to(db_session, *target_element_ids):
    """The elements that are the source of a plateau or gap relationship to these targets."""
    from sqlalchemy import text

    rows = db_session.execute(text(  # tenancy-ok: test fixture
        "SELECT DISTINCT source_id FROM archimate_relationships "
        "WHERE type IN ('realization', 'association') AND target_id = ANY(:t)"),
        {"t": list(target_element_ids)}).fetchall()
    return {r[0] for r in rows}


# -- N7-01: the links move with the element ----------------------------------------


def test_gap_generated_work_package_has_one_linked_element(app, db_session, make_org):
    from flask import g

    from app.services.gap_archimate_service import gap_archimate_service

    org, _user = _org_with_user(db_session, make_org, "n701a")
    gap, plateau = _gap(db_session, org), _plateau(db_session, org)
    with app.test_request_context("/"):
        g.current_org_id = org.id
        old = gap_archimate_service.create_work_package_for_gap(gap)  # wp.gaps.append(gap)
        old.plateaus.append(plateau)
        db_session.flush()
        copy = _copy("work_packages", old.id, org)
        db_session.expire_all()
        assert copy.archimate_element_id == old.archimate_element_id
        # Exactly one element carries the plateau and gap relationships: the copy's own.
        assert _sources_linked_to(
            db_session, gap.archimate_element_id, plateau.archimate_element_id
        ) == {copy.archimate_element_id}
        assert _links(db_session, org, copy) == {"plateau_ids": [plateau.id], "gap_ids": [gap.id]}
        assert sorted(t for t, _k in _relationships_of(copy)) == ["association", "realization"]


def test_cross_layer_impact_lists_work_package_once(app, db_session, make_org, tenant_ctx):
    from flask import g

    from app.modules.intelligence.services.query_service import IntelligenceQueryService
    from app.services.gap_archimate_service import gap_archimate_service

    org, _user = _org_with_user(db_session, make_org, "n701b")
    gap = _gap(db_session, org)
    with app.test_request_context("/"):
        g.current_org_id = org.id
        old = gap_archimate_service.create_work_package_for_gap(gap)
        db_session.flush()
        copy = _copy("work_packages", old.id, org)
        name, gap_element = copy.name, gap.archimate_element_id
        db_session.commit()
    with tenant_ctx(org.id):
        answer = IntelligenceQueryService.cross_layer_impact(
            gap_element, include_derived=False, direction="upstream")
    assert [e["name"] for e in answer["elements"].values()].count(name) == 1, answer["elements"]


def test_removed_gap_link_not_kept_on_left_element(db_session, make_org):
    org, _user = _org_with_user(db_session, make_org, "n701c")
    gap, plateau = _gap(db_session, org), _plateau(db_session, org)
    legacy = _legacy(db_session, org, "Old row")
    legacy.gaps.append(gap)
    legacy.plateaus.append(plateau)
    db_session.flush()
    copy = _copy("work_packages", legacy.id, org)
    left = copy.archimate_element_id
    assert _links(db_session, org, copy) == {"plateau_ids": [plateau.id], "gap_ids": [gap.id]}

    # An old screen gives the row an element of its own.
    taken = _element(db_session, org, "Old screen element", element_type="WorkPackage")
    legacy.archimate_element_id = taken.id
    db_session.flush()
    db_session.expire_all()
    copy = _copy("work_packages", legacy.id, org)
    assert copy.archimate_element_id == taken.id
    assert _links(db_session, org, copy) == {"plateau_ids": [plateau.id], "gap_ids": [gap.id]}
    # The left element no longer carries plateau or gap links.
    assert _scalar(db_session, "SELECT count(*) FROM archimate_relationships "
                   "WHERE source_id = %s" % left) == 0

    # The gap is removed on the old screen: it is gone for good.
    legacy.gaps.remove(gap)
    db_session.flush()
    assert _links(db_session, org, copy) == {"plateau_ids": [plateau.id], "gap_ids": []}
    assert _sources_linked_to(db_session, gap.archimate_element_id) == set()


# -- N7-02: one marker writer ------------------------------------------------------


def test_one_marker_writer():
    source = (ROOT / "app/commands/consolidate_work_packages.py").read_text(encoding="utf-8")
    assert len(re.findall(r"SET association_links_migrated_at", source)) == 1
    assert "def _advance_marker(" in source


# -- N7-03: a source element is taken only from the copy's organisation -----------


def test_other_org_source_element_is_not_taken(db_session, make_org, client, login_as, caplog):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.models import ArchiMateRelationship
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "n703a")
    other = make_org("n703b")
    plateau = _plateau(db_session, org)
    component = ApplicationComponent(name="Billing", organization_id=org.id)
    db_session.add(component)
    db_session.flush()
    legacy = _legacy(db_session, org, "Old row", application_component_id=component.id,
                    start_date=date(2030, 1, 1), target_date=date(2030, 6, 1))
    copy = _copy("work_packages", legacy.id, org)
    svc.update_work_package(copy.id, organization_id=org.id, plateau_id=plateau.id)
    foreign = _element(db_session, other, "Their element")
    mine = copy.archimate_element_id
    foreign_id, component_id, legacy_id, copy_id = foreign.id, component.id, legacy.id, copy.id
    db_session.commit()
    login_as(client, user)

    with caplog.at_level(logging.WARNING):
        resp = client.put(
            "/api/applications/%s/work-packages/%s" % (component_id, legacy_id),
            json={"archimate_element_id": foreign_id, "name": "Renamed on the old screen"})
    assert resp.status_code == 200, resp.get_data(as_text=True)[:400]
    db_session.expire_all()
    copy = svc.get_work_package(copy_id, org.id)
    assert copy.archimate_element_id == mine
    assert copy.name == "Renamed on the old screen"  # the rest of the edit still applies
    assert ArchiMateRelationship.query.filter_by(source_id=foreign_id).count() == 0
    assert _links(db_session, org, copy)["plateau_ids"] == [plateau.id]
    assert any("element not taken" in r.getMessage() and str(foreign_id) in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]


def _old_screen_create(client, app_id, name, plateau_id, **extra):
    resp = client.post("/api/applications/%s/work-packages" % app_id, json=dict(
        name=name, transformation_type="Migrate", start_date="2030-01-01", target_date="2030-06-01",
        plateau_id=plateau_id, **extra))
    assert resp.status_code == 200, resp.get_data(as_text=True)[:400]
    return resp.get_json()["work_package"]["id"]


def test_other_org_element_not_taken_on_create(db_session, make_org, client, login_as, caplog):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.models import ArchiMateRelationship
    from app.services import work_package_service as svc

    org, user = _org_with_user(db_session, make_org, "n703c")
    other = make_org("n703d")
    plateau = _plateau(db_session, org)
    component = ApplicationComponent(name="Billing", organization_id=org.id)
    db_session.add(component)
    db_session.flush()
    foreign_id, component_id, plateau_id = _element(db_session, other, "Their element").id, component.id, plateau.id
    db_session.commit()
    login_as(client, user)

    with caplog.at_level(logging.WARNING):
        legacy_id = _old_screen_create(client, component_id, "Created on the old screen", plateau_id,
                                       archimate_element_id=foreign_id)
    db_session.expire_all()
    copy = _copy("work_packages", legacy_id, org)
    assert copy.archimate_element_id != foreign_id
    assert ArchiMateRelationship.query.filter_by(source_id=foreign_id).count() == 0
    assert _links(db_session, org, copy)["plateau_ids"] == [plateau_id]
    assert any("element not taken" in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records]


def test_shared_application_element_not_taken(db_session, make_org, client, login_as):
    from app.models.application_portfolio import ApplicationComponent

    org, user = _org_with_user(db_session, make_org, "n703e")
    first_plateau, second_plateau = _plateau(db_session, org), _plateau(db_session, org)
    component = ApplicationComponent(name="Billing", organization_id=org.id)
    db_session.add(component)
    db_session.flush()
    _element(db_session, org, "Application element")  # the one the old screen falls back to
    component_id, ids = component.id, (first_plateau.id, second_plateau.id)
    db_session.commit()
    login_as(client, user)

    legacy_ids = [_old_screen_create(client, component_id, "Created %s" % i, p) for i, p in enumerate(ids)]
    db_session.expire_all()
    copies = [_copy("work_packages", i, org) for i in legacy_ids]
    assert copies[0].archimate_element_id != copies[1].archimate_element_id
    for copy, plateau_id in zip(copies, ids):
        assert _links(db_session, org, copy)["plateau_ids"] == [plateau_id]


# -- N7-04: the marker never moves backwards ---------------------------------------


def test_marker_never_moves_backwards(db_session, make_org, monkeypatch):
    from app.commands import consolidate_work_packages as cwp

    org, _user = _org_with_user(db_session, make_org, "n704")
    gap = _gap(db_session, org)
    legacy = _legacy(db_session, org, "Old row")
    copy = _copy("work_packages", legacy.id, org)
    later = datetime(2040, 1, 1, 12, 0, 0)
    _marker(db_session, copy.id, later)

    # The writer, called with an earlier clock.
    cwp._advance_marker(db_session, [copy.id], later - timedelta(days=1))
    assert _marker(db_session, copy.id) == later

    # A link step that took its clock before another step moved the marker.
    _raw_association(db_session, "gap", legacy.id, gap.id, role="primary")
    monkeypatch.setattr(cwp, "_utcnow", lambda: later - timedelta(hours=1))
    cwp.apply_link_changes({copy.id: {cwp._ASSOC_MIGRATE: True}}, replace=False)
    assert _marker(db_session, copy.id) == later

    # And forward still moves it, from a NULL marker too.
    cwp._advance_marker(db_session, [copy.id], later + timedelta(days=1))
    assert _marker(db_session, copy.id) == later + timedelta(days=1)
    _marker(db_session, copy.id, None)
    cwp._advance_marker(db_session, [copy.id], later)
    assert _marker(db_session, copy.id) == later
