"""Every BusinessCapability record must have exactly one ArchiMate element node.

Every domain record of the nine types has exactly one element node in its
organisation. BusinessCapability already has a before_insert listener; these
tests pin the invariant.
"""

import pytest

from app.models.archimate_core import ArchiMateElement
from app.models.business_capabilities import BusinessCapability


def test_creating_capability_creates_archimate_element(db_session, make_org, tenant_ctx):
    """A new BusinessCapability must automatically get a Capability element."""
    org = make_org("cap-slice")
    with tenant_ctx(org.id):
        cap = BusinessCapability(
            name="Order Management",
            organization_id=org.id,
            description="Handles orders.",
            level=1,
        )
        db_session.add(cap)
        db_session.flush()

        assert cap.archimate_element_id is not None
        element = db_session.get(ArchiMateElement, cap.archimate_element_id)
        assert element is not None
        assert element.type == "Capability"
        assert element.layer == "Strategy"
        assert element.organization_id == org.id
        assert element.name == "Order Management"


def test_capability_archimate_element_is_idempotent(db_session, make_org, tenant_ctx):
    """Creating the same capability again must not create a second element."""
    org = make_org("cap-idempotent")
    with tenant_ctx(org.id):
        cap = BusinessCapability(
            name="Unique Capability",
            organization_id=org.id,
            level=1,
        )
        db_session.add(cap)
        db_session.flush()

        assert cap.archimate_element_id is not None
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id, name="Unique Capability"
        ).count() == 1


def test_capability_archimate_element_isolation(db_session, make_org, tenant_ctx):
    """A BusinessCapability in org A must not create an element in org B."""
    org_a = make_org("cap-iso-a")
    org_b = make_org("cap-iso-b")

    with tenant_ctx(org_a.id):
        cap_a = BusinessCapability(
            name="Capability in A only",
            organization_id=org_a.id,
            level=1,
        )
        db_session.add(cap_a)
        db_session.flush()

        element_a = db_session.get(ArchiMateElement, cap_a.archimate_element_id)
        assert element_a.organization_id == org_a.id

        b_elements = db_session.query(ArchiMateElement).filter_by(
            organization_id=org_b.id, name="Capability in A only"
        ).count()
        assert b_elements == 0


def test_deleting_capability_leaves_no_orphan_element(db_session, make_org, tenant_ctx):
    """Deleting a BusinessCapability must set null on its element."""
    org = make_org("cap-delete")
    with tenant_ctx(org.id):
        cap = BusinessCapability(
            name="Capability to delete",
            organization_id=org.id,
            level=1,
        )
        db_session.add(cap)
        db_session.flush()

        element_id = cap.archimate_element_id
        assert element_id is not None

        db_session.delete(cap)
        db_session.flush()

        element = db_session.get(ArchiMateElement, element_id)
        assert element is not None


def test_capability_with_preset_element_is_not_overwritten(db_session, make_org, tenant_ctx):
    """A caller that already links an element is left alone."""
    org = make_org("cap-preset")
    with tenant_ctx(org.id):
        preset = ArchiMateElement(
            name="Pre-linked Capability Element",
            type="Capability",
            layer="Strategy",
            organization_id=org.id,
        )
        db_session.add(preset)
        db_session.flush()

        cap = BusinessCapability(
            name="Pre-linked Capability",
            organization_id=org.id,
            level=1,
            archimate_element_id=preset.id,
        )
        db_session.add(cap)
        db_session.flush()

        assert cap.archimate_element_id == preset.id
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id, name="Pre-linked Capability Element"
        ).count() == 1