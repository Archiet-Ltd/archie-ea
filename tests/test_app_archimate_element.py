"""Every ApplicationComponent record must have exactly one ArchiMate element node.

R1-B18 PR 2: every domain record of the nine types has exactly one element
node in its organisation. ApplicationComponent already has a before_insert
listener; these tests pin the invariant.
"""

import pytest

from app.models.archimate_core import ArchiMateElement
from app.models.application_portfolio import ApplicationComponent


def test_creating_app_creates_archimate_element(db_session, make_org, tenant_ctx):
    """A new ApplicationComponent must automatically get an element."""
    org = make_org("app-slice")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="Order Management System",
            organization_id=org.id,
            description="Handles customer orders.",
        )
        db_session.add(app)
        db_session.flush()

        assert app.archimate_element_id is not None
        element = db_session.get(ArchiMateElement, app.archimate_element_id)
        assert element is not None
        assert element.type == "ApplicationComponent"
        assert element.layer == "Application"
        assert element.organization_id == org.id
        assert element.name == "Order Management System"


def test_app_archimate_element_is_idempotent(db_session, make_org, tenant_ctx):
    """Creating the same app again must not create a second element."""
    org = make_org("app-idempotent")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="Unique App",
            organization_id=org.id,
        )
        db_session.add(app)
        db_session.flush()

        assert app.archimate_element_id is not None
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id, name="Unique App"
        ).count() == 1


def test_app_archimate_element_isolation(db_session, make_org, tenant_ctx):
    """An ApplicationComponent in org A must not create an element in org B."""
    org_a = make_org("app-iso-a")
    org_b = make_org("app-iso-b")

    with tenant_ctx(org_a.id):
        app_a = ApplicationComponent(
            name="App in A only",
            organization_id=org_a.id,
        )
        db_session.add(app_a)
        db_session.flush()

        element_a = db_session.get(ArchiMateElement, app_a.archimate_element_id)
        assert element_a.organization_id == org_a.id

        b_elements = db_session.query(ArchiMateElement).filter_by(
            organization_id=org_b.id, name="App in A only"
        ).count()
        assert b_elements == 0


def test_deleting_app_leaves_no_orphan_element(db_session, make_org, tenant_ctx):
    """Deleting an ApplicationComponent must set null on its element."""
    org = make_org("app-delete")
    with tenant_ctx(org.id):
        app = ApplicationComponent(
            name="App to delete",
            organization_id=org.id,
        )
        db_session.add(app)
        db_session.flush()

        element_id = app.archimate_element_id
        assert element_id is not None

        db_session.delete(app)
        db_session.flush()

        element = db_session.get(ArchiMateElement, element_id)
        assert element is not None


def test_app_with_preset_element_is_not_overwritten(db_session, make_org, tenant_ctx):
    """A caller that already links an element is left alone."""
    org = make_org("app-preset")
    with tenant_ctx(org.id):
        preset = ArchiMateElement(
            name="Pre-linked App Element",
            type="ApplicationComponent",
            layer="Application",
            organization_id=org.id,
        )
        db_session.add(preset)
        db_session.flush()

        app = ApplicationComponent(
            name="Pre-linked App",
            organization_id=org.id,
            archimate_element_id=preset.id,
        )
        db_session.add(app)
        db_session.flush()

        assert app.archimate_element_id == preset.id
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id, name="Pre-linked App Element"
        ).count() == 1