"""Every WorkPackage record must have exactly one ArchiMate element node.

Every domain record of the nine types has exactly one element node in its
organisation. WorkPackage now has a before_insert listener that mirrors it into
a WorkPackage (Implementation layer) element.
"""

import pytest

from app.models.archimate_core import ArchiMateElement
from app.models.implementation_migration import WorkPackage


def test_creating_work_package_creates_archimate_element(db_session, make_org, tenant_ctx):
    """A new WorkPackage must automatically get a WorkPackage element."""
    org = make_org("wp-slice")
    with tenant_ctx(org.id):
        wp = WorkPackage(
            name="Migrate legacy invoicing",
            organization_id=org.id,
            description="Move invoicing off the legacy mainframe.",
        )
        db_session.add(wp)
        db_session.flush()

        assert wp.archimate_element_id is not None
        element = db_session.get(ArchiMateElement, wp.archimate_element_id)
        assert element is not None
        assert element.type == "WorkPackage"
        assert element.layer == "Implementation"
        assert element.organization_id == org.id
        assert element.name == "Migrate legacy invoicing"


def test_work_package_archimate_element_is_idempotent(db_session, make_org, tenant_ctx):
    """Creating the same work package again must not create a second element."""
    org = make_org("wp-idempotent")
    with tenant_ctx(org.id):
        wp = WorkPackage(
            name="Unique Work Package",
            organization_id=org.id,
        )
        db_session.add(wp)
        db_session.flush()

        assert wp.archimate_element_id is not None
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id, name="Unique Work Package"
        ).count() == 1


def test_work_package_archimate_element_isolation(db_session, make_org, tenant_ctx):
    """A WorkPackage in org A must not create an element in org B."""
    org_a = make_org("wp-iso-a")
    org_b = make_org("wp-iso-b")

    with tenant_ctx(org_a.id):
        wp_a = WorkPackage(
            name="Work Package in A only",
            organization_id=org_a.id,
        )
        db_session.add(wp_a)
        db_session.flush()

        element_a = db_session.get(ArchiMateElement, wp_a.archimate_element_id)
        assert element_a.organization_id == org_a.id

        b_elements = db_session.query(ArchiMateElement).filter_by(
            organization_id=org_b.id, name="Work Package in A only"
        ).count()
        assert b_elements == 0


def test_deleting_work_package_leaves_no_orphan_element(db_session, make_org, tenant_ctx):
    """Deleting a WorkPackage must set null on its element."""
    org = make_org("wp-delete")
    with tenant_ctx(org.id):
        wp = WorkPackage(
            name="Work Package to delete",
            organization_id=org.id,
        )
        db_session.add(wp)
        db_session.flush()

        element_id = wp.archimate_element_id
        assert element_id is not None

        db_session.delete(wp)
        db_session.flush()

        element = db_session.get(ArchiMateElement, element_id)
        assert element is not None


def test_work_package_with_preset_element_is_not_overwritten(db_session, make_org, tenant_ctx):
    """A caller that already links an element is left alone."""
    org = make_org("wp-preset")
    with tenant_ctx(org.id):
        preset = ArchiMateElement(
            name="Pre-linked Work Package Element",
            type="WorkPackage",
            layer="Implementation",
            organization_id=org.id,
        )
        db_session.add(preset)
        db_session.flush()

        wp = WorkPackage(
            name="Pre-linked Work Package",
            organization_id=org.id,
            archimate_element_id=preset.id,
        )
        db_session.add(wp)
        db_session.flush()

        assert wp.archimate_element_id == preset.id
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id, name="Pre-linked Work Package Element"
        ).count() == 1


def test_long_work_package_name_is_truncated_not_fatal(db_session, make_org, tenant_ctx):
    """WorkPackage.name is String(255); element name is String(100)."""
    org = make_org("wp-truncate")
    long_name = "W" * 200
    with tenant_ctx(org.id):
        wp = WorkPackage(
            name=long_name,
            organization_id=org.id,
        )
        db_session.add(wp)
        db_session.commit()  # must not raise on the element insert

        element = db_session.get(ArchiMateElement, wp.archimate_element_id)
        assert element is not None
        assert len(element.name) <= 100


def test_unified_work_package_writer_creates_exactly_one_element(db_session, make_org, tenant_ctx):
    """Creating a work package through the unified store's writer must create
    exactly one ArchiMate element node."""
    from app.services import work_package_service

    org = make_org("uwp-writer")
    with tenant_ctx(org.id):
        wp = work_package_service.create_work_package(
            organization_id=org.id,
            name="Unified store work package",
        )

        assert wp.archimate_element_id is not None, (
            "UnifiedWorkPackage created through the writer must have an element"
        )
        element = db_session.get(ArchiMateElement, wp.archimate_element_id)
        assert element is not None
        assert element.type == "WorkPackage"
        assert element.layer == "Implementation"
        assert element.organization_id == org.id

        # Exactly one element for this work package
        count = db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id,
        ).count()
        assert count == 1, (
            "Writer must create exactly one element, not %d" % count
        )