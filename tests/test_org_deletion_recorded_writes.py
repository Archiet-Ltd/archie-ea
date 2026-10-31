"""Every write to a row outside a deleted organisation is ruled and recorded.

Deleting organisation A changes rows outside A in exactly two ways, each recorded in the platform
record of the deletion: a pointer to A's organisation row is cleared, and a detached person's home
moves (with the role change that move requires). These tests use two real organisations and
compare the whole database before and after.
"""

import uuid

from sqlalchemy import text

from tests.test_org_deletion_writes_only_inside import _cleanup, _outside_changes, _snapshot, _two_organisations


def _platform_record(db, org_id):
    return db.session.execute(text(
        "SELECT new_value FROM soc2_audit_log WHERE organization_id IS NULL AND table_name = 'organizations' "
        "AND record_id = :a"), {"a": org_id}).scalar()


def _vendor(db, marker, named_by):
    return db.session.execute(text(
        "INSERT INTO vendor_organizations (name, code, seed_source_id, is_seed_data, seeded_by, "
        "named_by_organization_id) VALUES (:n, :c, 'test', false, 'test', :o) RETURNING id"),
        {"n": "Vendor %s" % marker, "c": "V%s" % marker, "o": named_by}).scalar()


def _application(db, name, org_id, vendor_id):
    from app.models.application_portfolio import ApplicationComponent

    application = ApplicationComponent(name=name, organization_id=org_id, vendor_organization_id=vendor_id)
    db.session.add(application)
    db.session.flush()
    return application.id


def _without(changes, table, row_id):
    return [c for c in changes if not (c[1] == table and c[2]["id"] == row_id)]


def test_a_vendor_named_by_the_organisation_and_used_by_another_keeps_its_row_with_the_pointer_cleared(app):
    from app import db
    from app.services.org_deletion_service import OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a, b, _, _ = _two_organisations(db, marker)
        vendor = _vendor(db, marker, a.id)
        application = _application(db, "B app %s" % marker, b.id, vendor)
        db.session.commit()
        ids = {"a": a.id, "b": b.id}
        before = _snapshot(db)
        db.session.remove()

    try:
        with app.app_context():
            OrganizationDeletionService.delete_organization(ids["a"])
            db.session.remove()
        with app.app_context():
            after = _snapshot(db)
            row = db.session.execute(text(
                "SELECT named_by_organization_id FROM vendor_organizations WHERE id = :id"), {"id": vendor}).one()
            assert row[0] is None, "the vendor row must stay, with its pointer to the deleted organisation cleared"
            # The one change outside the deleted organisation is the vendor's pointer, and it is recorded.
            changes = _outside_changes(before, after, ids["a"], moved_people=())
            assert _without(changes, "vendor_organizations", vendor) == [], changes
            record = _platform_record(db, ids["a"])
            assert {"table": "vendor_organizations", "id": vendor, "column": "named_by_organization_id",
                    "old_value": ids["a"]} in record["pointers_cleared"], record
            db.session.remove()
    finally:
        with app.app_context():
            db.session.execute(text("DELETE FROM application_components WHERE id = :id"), {"id": application})
            db.session.execute(text("DELETE FROM vendor_organizations WHERE id = :id"), {"id": vendor})
            db.session.commit()
            db.session.remove()
        _cleanup(app, ids["a"], ids["b"])


def test_a_vendor_named_by_the_organisation_and_used_only_by_it_is_deleted_with_it(app):
    from app import db
    from app.services.org_deletion_service import OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a, b, _, _ = _two_organisations(db, marker)
        vendor = _vendor(db, marker, a.id)
        _application(db, "A app %s" % marker, a.id, vendor)
        db.session.commit()
        ids = {"a": a.id, "b": b.id}
        before = _snapshot(db)
        db.session.remove()

    try:
        with app.app_context():
            OrganizationDeletionService.delete_organization(ids["a"])
            db.session.remove()
        with app.app_context():
            after = _snapshot(db)
            assert db.session.execute(text("SELECT 1 FROM vendor_organizations WHERE id = :id"),
                                      {"id": vendor}).first() is None
            record = _platform_record(db, ids["a"])
            assert record["row_counts"].get("vendor_organizations") == 1, record
            assert record["pointers_cleared"] == [], record
            changes = _outside_changes(before, after, ids["a"], moved_people=())
            assert _without(changes, "vendor_organizations", vendor) == [], changes
            db.session.remove()
    finally:
        with app.app_context():
            db.session.execute(text("DELETE FROM vendor_organizations WHERE id = :id"), {"id": vendor})
            db.session.commit()
            db.session.remove()
        _cleanup(app, ids["a"], ids["b"])


def test_another_organisations_row_that_names_the_deleted_one_keeps_its_row_with_the_pointer_cleared(app):
    from app import db
    from app.services.org_deletion_service import OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a, b, _, _ = _two_organisations(db, marker)
        capability = db.session.execute(text(
            "INSERT INTO unified_capabilities (name, level, version, organization_id, source_org_id) "
            "VALUES (:n, 1, 1, :b, :a) RETURNING id"),
            {"n": "B capability %s" % marker, "a": a.id, "b": b.id}).scalar()
        db.session.commit()
        ids = {"a": a.id, "b": b.id}
        before = _snapshot(db)
        db.session.remove()

    try:
        with app.app_context():
            OrganizationDeletionService.delete_organization(ids["a"])
            db.session.remove()
        with app.app_context():
            after = _snapshot(db)
            row = db.session.execute(text(
                "SELECT organization_id, source_org_id FROM unified_capabilities WHERE id = :id"),
                {"id": capability}).one()
            assert tuple(row) == (ids["b"], None)
            changes = _outside_changes(before, after, ids["a"], moved_people=())
            assert _without(changes, "unified_capabilities", capability) == [], changes
            record = _platform_record(db, ids["a"])
            assert {"table": "unified_capabilities", "id": capability, "column": "source_org_id",
                    "old_value": ids["a"]} in record["pointers_cleared"], record
            db.session.remove()
    finally:
        with app.app_context():
            db.session.execute(text("DELETE FROM unified_capabilities WHERE id = :id"), {"id": capability})
            db.session.commit()
            db.session.remove()
        _cleanup(app, ids["a"], ids["b"])


def _administrator_detached(app, role_in_b):
    """Delete an organisation whose administrator also holds ``role_in_b`` in a second one."""
    from app import db
    from app.models.org_role import OrgRole
    from app.models.user import Role, User
    from app.services.org_deletion_service import OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    made_roles = []
    with app.app_context():
        if Role.query.filter_by(name="Administrator").first() is None or not Role.query.filter_by(default=True).first():
            have = {r.name for r in Role.query.all()}
            Role.insert_roles()
            db.session.commit()
            made_roles = [r.id for r in Role.query.all() if r.name not in have]
        a, b, sole, _ = _two_organisations(db, marker)
        sole.grant_org_admin()
        db.session.add(OrgRole(organization_id=b.id, user_id=sole.id, role=role_in_b))
        db.session.commit()
        ids = {"a": a.id, "b": b.id, "sole": sole.id}
        assert sole.is_admin()
        before = _snapshot(db)
        db.session.remove()
    try:
        with app.app_context():
            message = OrganizationDeletionService.delete_organization(ids["a"])
            db.session.remove()
        with app.app_context():
            after = _snapshot(db)
            person = db.session.get(User, ids["sole"])
            result = {
                "message": message,
                "person": person,
                "is_admin": person.is_admin(),
                "is_org_admin": person.is_org_admin,
                "changes": _outside_changes(before, after, ids["a"], moved_people=(ids["sole"],)),
                "record": _platform_record(db, ids["a"]),
                "ids": ids,
                "home": person.organization_id,
            }
            db.session.expunge(person)
            db.session.remove()
        return result
    finally:
        _cleanup(app, ids["a"], ids["b"])
        with app.app_context():
            for role_id in made_roles:
                db.session.execute(text("DELETE FROM roles WHERE id = :id"), {"id": role_id})
            db.session.commit()
            db.session.remove()


def test_an_administrator_detached_to_a_viewer_role_is_not_an_administrator_there(app):
    result = _administrator_detached(app, "viewer")
    ids = result["ids"]
    assert result["home"] == ids["b"]
    assert result["is_admin"] is False and result["is_org_admin"] is False
    assert result["changes"] == []
    rights = result["record"]["administrators_detached"][str(ids["sole"])]
    assert rights["before"]["organization_id"] == ids["a"] and rights["before"]["is_org_admin"] is True
    assert rights["after"]["organization_id"] == ids["b"] and rights["after"]["is_org_admin"] is False
    assert rights["before"]["role_id"] != rights["after"]["role_id"]


def test_an_administrator_detached_to_an_org_admin_role_keeps_exactly_that_role(app):
    result = _administrator_detached(app, "org_admin")
    ids = result["ids"]
    assert result["home"] == ids["b"]
    assert result["is_org_admin"] is True
    rights = result["record"]["administrators_detached"][str(ids["sole"])]
    assert rights["after"]["organization_id"] == ids["b"] and rights["after"]["is_org_admin"] is True
    assert rights["before"]["role_id"] == rights["after"]["role_id"]
