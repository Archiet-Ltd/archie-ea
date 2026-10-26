"""Every list action on the architecture repository reaches the element that was clicked.

A mirrored work package is listed by its ArchiMate element id, while the work
packages table keeps its own primary-key sequence. The two sequences overlap.
Before this fix the detail page, the edit save, delete and bulk delete looked
the incoming id up in the work packages table first, so a click on one work
package could open, overwrite or delete a different work package that merely
shared the number. Each test below builds that collision on purpose.
"""
import uuid

import pytest

from app import db



def _make_architect(db_session, org_id, label):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="%s-%s@example.com" % (label, uuid.uuid4().hex[:8]),
        first_name="Id",
        last_name="Architect",
        organization_id=org_id,
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    user.role = Role.query.filter_by(name="Architect").one()
    db_session.add(user)
    db_session.commit()
    return user


def _collision(db_session, org_id):
    """A target work package whose ArchiMate element id equals a decoy work
    package's own primary key. Returns (target_ae_id, target_wp_id, decoy_wp_id,
    decoy_ae_id)."""
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import WorkPackage

    target_ae = None
    for _ in range(10):
        ae = ArchiMateElement(
            name="Target work package", type="WorkPackage", layer="Implementation",
            organization_id=org_id,
        )
        db_session.add(ae)
        db_session.flush()
        if db_session.get(WorkPackage, ae.id) is None:
            target_ae = ae
            break
    assert target_ae is not None, "could not find a free work package id to collide with"
    target_wp = WorkPackage(
        name="Target work package", organization_id=org_id, element_type="WorkPackage",
        archimate_element_id=target_ae.id,
    )
    db_session.add(target_wp)
    db_session.flush()

    decoy_ae = ArchiMateElement(
        name="Decoy work package", type="WorkPackage", layer="Implementation",
        organization_id=org_id,
    )
    db_session.add(decoy_ae)
    db_session.flush()
    decoy_wp = WorkPackage(
        id=target_ae.id, name="Decoy work package", organization_id=org_id,
        element_type="WorkPackage", archimate_element_id=decoy_ae.id,
    )
    db_session.add(decoy_wp)
    db_session.flush()
    db_session.execute(db.text(
        "SELECT setval(pg_get_serial_sequence('work_packages', 'id'), "
        "(SELECT MAX(id) FROM work_packages))"
    ))
    db_session.commit()
    assert target_wp.id != target_ae.id, "setup: the target must not be its own twin"
    return target_ae.id, target_wp.id, decoy_wp.id, decoy_ae.id


def _names(org_id):
    """Read the table directly, so no signed-in tenant filter narrows the answer."""
    db.session.expire_all()
    rows = db.session.execute(
        db.text("SELECT name FROM work_packages WHERE organization_id = :o ORDER BY name"),
        {"o": org_id},
    ).fetchall()
    return [r[0] for r in rows]


BASE = "/architecture/implementation/WorkPackage/%d"


def _signed_in(app, db_session, make_org, login_as, label):
    org = make_org(label)
    user = _make_architect(db_session, org.id, label)
    c = app.test_client()
    login_as(c, user)
    return org, c


def test_the_detail_link_opens_the_clicked_work_package(app, db_session, make_org, login_as):
    org, c = _signed_in(app, db_session, make_org, login_as, "ids-detail")
    ae_id, wp_id, decoy_id, _ = _collision(db_session, org.id)

    body = c.get(BASE % ae_id + "?ae=1").get_data(as_text=True)
    assert "Target work package" in body
    assert "Decoy work package" not in body, "the list's link opened the number twin"

    # A caller holding the work package's own id still reaches it.
    body = c.get(BASE % decoy_id).get_data(as_text=True)
    assert "Decoy work package" in body


def test_saving_an_edit_changes_only_the_clicked_work_package(app, db_session, make_org, login_as):
    org, c = _signed_in(app, db_session, make_org, login_as, "ids-edit")
    ae_id, _, _, _ = _collision(db_session, org.id)

    resp = c.post(BASE % ae_id + "/edit?ae=1", json={"name": "Renamed target", "description": ""})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["success"] is True
    assert _names(org.id) == ["Decoy work package", "Renamed target"], (
        "the save overwrote a different work package"
    )


def test_delete_removes_only_the_clicked_work_package(app, db_session, make_org, login_as):
    from app.models.archimate_core import ArchiMateElement

    org, c = _signed_in(app, db_session, make_org, login_as, "ids-delete")
    ae_id, _, _, decoy_ae_id = _collision(db_session, org.id)

    resp = c.post(BASE % ae_id + "/delete?ae=1", json={})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _names(org.id) == ["Decoy work package"], "delete removed the number twin"
    assert db.session.get(ArchiMateElement, ae_id) is None, "the clicked element survived"
    assert db.session.get(ArchiMateElement, decoy_ae_id) is not None


def test_bulk_delete_removes_only_the_selected_work_packages(app, db_session, make_org, login_as):
    org, c = _signed_in(app, db_session, make_org, login_as, "ids-bulk")
    ae_id, _, _, _ = _collision(db_session, org.id)

    resp = c.post("/architecture/implementation/WorkPackage/bulk-delete", json={"ae_ids": [ae_id]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["deleted_count"] == 1
    assert _names(org.id) == ["Decoy work package"], "bulk delete removed the number twin"


def test_another_organisation_cannot_reach_the_element_by_either_id(app, db_session, make_org, login_as):
    org_a = make_org("ids-iso-a")
    ae_id, wp_id, _, _ = _collision(db_session, org_a.id)
    _, c = _signed_in(app, db_session, make_org, login_as, "ids-iso-b")

    assert c.get(BASE % ae_id + "?ae=1").status_code == 404
    assert c.get(BASE % wp_id).status_code == 404
    c.post(BASE % ae_id + "/delete?ae=1", json={})
    c.post("/architecture/implementation/WorkPackage/bulk-delete", json={"ae_ids": [ae_id], "ids": [wp_id]})
    assert len(_names(org_a.id)) == 2, "another organisation deleted organisation A's work packages"


def test_an_edit_with_a_blank_date_saves(app, db_session, make_org, login_as):
    """The edit form posts every field; an untouched target date arrives blank
    and must be stored as no date, not fail the save."""
    org, c = _signed_in(app, db_session, make_org, login_as, "ids-blank-date")
    ae_id, _, _, _ = _collision(db_session, org.id)

    resp = c.post(
        BASE % ae_id + "/edit?ae=1",
        json={"name": "Target with no date", "description": "", "target_date": ""},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert "Target with no date" in _names(org.id)
