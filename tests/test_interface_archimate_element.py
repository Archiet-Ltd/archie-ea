"""Every ApplicationInterface record must have exactly one ArchiMate element node.

R1-B18 PR 2: every domain record of the nine types has exactly one element
node in its organisation. ApplicationInterface already has a before_insert
listener; these tests pin the invariant.
"""

import pytest

from app.models.archimate_core import ArchiMateElement
from app.models.application_layer import ApplicationInterface


def test_creating_interface_creates_archimate_element(db_session, make_org, tenant_ctx):
    """A new ApplicationInterface must automatically get an element."""
    org = make_org("interface-slice")
    with tenant_ctx(org.id):
        interface = ApplicationInterface(
            name="SAP Order API",
            organization_id=org.id,
            description="REST API for order management.",
            interface_type="REST",
        )
        db_session.add(interface)
        db_session.flush()

        assert interface.archimate_element_id is not None
        element = db_session.get(ArchiMateElement, interface.archimate_element_id)
        assert element is not None
        assert element.type == "ApplicationInterface"
        assert element.layer == "Application"
        assert element.organization_id == org.id
        assert element.name == "SAP Order API"


def test_interface_archimate_element_is_idempotent(db_session, make_org, tenant_ctx):
    """Creating the same interface again must not create a second element."""
    org = make_org("interface-idempotent")
    with tenant_ctx(org.id):
        interface = ApplicationInterface(
            name="Unique Interface",
            organization_id=org.id,
            interface_type="REST",
        )
        db_session.add(interface)
        db_session.flush()

        assert interface.archimate_element_id is not None
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id, name="Unique Interface"
        ).count() == 1


def test_interface_archimate_element_isolation(db_session, make_org, tenant_ctx):
    """An ApplicationInterface in org A must not create an element in org B."""
    org_a = make_org("interface-iso-a")
    org_b = make_org("interface-iso-b")

    with tenant_ctx(org_a.id):
        interface_a = ApplicationInterface(
            name="Interface in A only",
            organization_id=org_a.id,
            interface_type="REST",
        )
        db_session.add(interface_a)
        db_session.flush()

        element_a = db_session.get(ArchiMateElement, interface_a.archimate_element_id)
        assert element_a.organization_id == org_a.id

        b_elements = db_session.query(ArchiMateElement).filter_by(
            organization_id=org_b.id, name="Interface in A only"
        ).count()
        assert b_elements == 0


def test_deleting_interface_leaves_no_orphan_element(db_session, make_org, tenant_ctx):
    """Deleting an ApplicationInterface must set null on its element."""
    org = make_org("interface-delete")
    with tenant_ctx(org.id):
        interface = ApplicationInterface(
            name="Interface to delete",
            organization_id=org.id,
            interface_type="REST",
        )
        db_session.add(interface)
        db_session.flush()

        element_id = interface.archimate_element_id
        assert element_id is not None

        db_session.delete(interface)
        db_session.flush()

        element = db_session.get(ArchiMateElement, element_id)
        assert element is not None


def test_interface_with_preset_element_is_not_overwritten(db_session, make_org, tenant_ctx):
    """A caller that already links an element is left alone."""
    org = make_org("interface-preset")
    with tenant_ctx(org.id):
        preset = ArchiMateElement(
            name="Pre-linked Interface Element",
            type="ApplicationInterface",
            layer="Application",
            organization_id=org.id,
        )
        db_session.add(preset)
        db_session.flush()

        interface = ApplicationInterface(
            name="Pre-linked Interface",
            organization_id=org.id,
            interface_type="REST",
            archimate_element_id=preset.id,
        )
        db_session.add(interface)
        db_session.flush()

        assert interface.archimate_element_id == preset.id
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id, name="Pre-linked Interface Element"
        ).count() == 1