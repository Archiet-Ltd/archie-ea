"""Deleting an organisation refuses when any row outside it still refers to a row inside it.

One rule, read from the database's own foreign keys: before anything is written, every row that
is not the deleted organisation's but refers to one of its rows is found, whatever the key's ON
DELETE action (clear, cascade, refuse). If there is one, the deletion is refused with a plain
sentence naming the organisations, and nothing changes. Two kinds of reference are allowed and
recorded instead: a pointer to the organisation row itself, and a person who belongs elsewhere.
Every test uses two real organisations and compares the whole database before and after.
"""

import uuid

import pytest
from sqlalchemy import text

from tests.test_org_deletion_writes_only_inside import _cleanup, _snapshot, _two_organisations


def _element_in(db, org_id, name):
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(name=name, type="ApplicationComponent", layer="Application", organization_id=org_id)
    db.session.add(element)
    db.session.flush()
    return element


def _refused(app, ids, expect_in_message, before_rows=None):
    """Delete A, expect the plain refusal, and check the database is exactly as it was."""
    from app import db
    from app.models.organization import Organization
    from app.services.org_deletion_service import OrganizationDeletionRefused, OrganizationDeletionService

    with app.app_context():
        with pytest.raises(OrganizationDeletionRefused) as refused:
            OrganizationDeletionService.delete_organization(ids["a"])
        message = str(refused.value)
        db.session.remove()
    assert message.startswith("Org A %s cannot be deleted: " % ids["marker"]), message
    assert expect_in_message in message, message
    assert "Remove or reassign those references first. Nothing was deleted." in message, message
    for database_text in ("foreign key", "violates", "constraint", "DETAIL"):
        assert database_text not in message, message
    with app.app_context():
        assert db.session.get(Organization, ids["a"]) is not None
        assert _snapshot(db) == ids["before"], "a refused deletion changed the database"
        db.session.remove()
    return message


def test_another_organisations_application_pointing_at_an_element_of_the_deleted_one_refuses_the_deletion(app):
    from app import db
    from app.models.application_portfolio import ApplicationComponent

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a, b, _, _ = _two_organisations(db, marker)
        element = _element_in(db, a.id, "A element %s" % marker)
        application = ApplicationComponent(name="B app %s" % marker, organization_id=b.id,
                                           archimate_element_id=element.id)
        db.session.add(application)
        db.session.commit()
        ids = {"a": a.id, "b": b.id, "marker": marker, "app": application.id, "element": element.id,
               "before": _snapshot(db)}
        db.session.remove()
    try:
        _refused(app, ids, "Org B %s: 1 application component" % marker)
        with app.app_context():
            row = db.session.execute(text(
                "SELECT organization_id, archimate_element_id FROM application_components WHERE id = :i"),
                {"i": ids["app"]}).one()
            assert tuple(row) == (ids["b"], ids["element"]), "the database cleared B's pointer"
            db.session.remove()
    finally:
        with app.app_context():
            db.session.execute(text("DELETE FROM application_components WHERE id = :i"), {"i": ids["app"]})
            db.session.commit()
            db.session.remove()
        _cleanup(app, ids["a"], ids["b"])


def test_a_platform_template_pointing_at_an_element_of_the_deleted_one_refuses_the_deletion(app):
    from app import db
    from app.models.vendor.vendor_organization import VendorArchiMateTemplate

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a, b, _, _ = _two_organisations(db, marker)
        element = _element_in(db, a.id, "A element %s" % marker)
        template = VendorArchiMateTemplate(vendor_key="SAP", element_id=element.id,
                                           element_name="Template %s" % marker)
        db.session.add(template)
        db.session.commit()
        ids = {"a": a.id, "b": b.id, "marker": marker, "template": template.id, "before": _snapshot(db)}
        db.session.remove()
    try:
        _refused(app, ids, "Shared platform records: 1 vendor archimate template")
    finally:
        with app.app_context():
            db.session.execute(text("DELETE FROM vendor_archimate_templates WHERE id = :i"), {"i": ids["template"]})
            db.session.commit()
            db.session.remove()
        _cleanup(app, ids["a"], ids["b"])


def test_another_organisations_relationship_targeting_an_element_of_the_deleted_one_is_refused_in_plain_words(app):
    from app import db
    from app.models.archimate_core import ArchiMateRelationship

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a, b, _, b_element = _two_organisations(db, marker)
        element = _element_in(db, a.id, "A element %s" % marker)
        relationship = ArchiMateRelationship(type="Flow", source_id=b_element.id, target_id=element.id,
                                             organization_id=b.id)
        db.session.add(relationship)
        db.session.commit()
        ids = {"a": a.id, "b": b.id, "marker": marker, "rel": relationship.id, "before": _snapshot(db)}
        db.session.remove()
    try:
        _refused(app, ids, "Org B %s: 1 archimate relationship" % marker)
    finally:
        with app.app_context():
            db.session.execute(text("DELETE FROM archimate_relationships WHERE id = :i"), {"i": ids["rel"]})
            db.session.commit()
            db.session.remove()
        _cleanup(app, ids["a"], ids["b"])


def test_a_sync_log_of_the_deleted_organisations_connector_goes_with_it_and_another_organisations_stays(app):
    from app import db
    from app.models.organization import Organization
    from app.services.org_deletion_service import OrganizationDeletionService

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        a, b, _, _ = _two_organisations(db, marker)
        logs = {}
        for key, org in (("a", a), ("b", b)):
            connector_id = str(uuid.uuid4())
            logs[key] = str(uuid.uuid4())
            db.session.execute(text(
                "INSERT INTO connector_configs (id, organization_id, connector_type, name, config) "
                "VALUES (:c, :o, 'github', :n, '{}')"), {"c": connector_id, "o": org.id, "n": "GitHub %s" % key})
            db.session.execute(text(
                "INSERT INTO sync_logs (id, connector_id, sync_type, status) VALUES (:l, :c, 'manual', 'success')"),
                {"l": logs[key], "c": connector_id})
            logs[key + "_connector"] = connector_id
        db.session.commit()
        ids = {"a": a.id, "b": b.id}
        db.session.remove()
    try:
        with app.app_context():
            OrganizationDeletionService.delete_organization(ids["a"])
            db.session.remove()
        with app.app_context():
            assert db.session.get(Organization, ids["a"]) is None
            remaining = set(db.session.execute(text(
                "SELECT id FROM sync_logs WHERE id IN (:a, :b)"), {"a": logs["a"], "b": logs["b"]}).scalars())
            assert remaining == {logs["b"]}, remaining
            kept = db.session.execute(text("SELECT connector_id FROM sync_logs WHERE id = :l"),
                                      {"l": logs["b"]}).scalar()
            assert kept == logs["b_connector"], "B's sync log lost its connector"
            db.session.remove()
    finally:
        with app.app_context():
            db.session.execute(text("DELETE FROM sync_logs WHERE id IN (:a, :b)"), {"a": logs["a"], "b": logs["b"]})
            db.session.commit()
            db.session.remove()
        _cleanup(app, ids["a"], ids["b"])
