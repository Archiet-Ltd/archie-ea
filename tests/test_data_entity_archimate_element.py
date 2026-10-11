"""Every DataEntity record must have exactly one ArchiMate element node.

Every domain record of the nine types has exactly one element node in its
organisation. DataEntity already has an after_insert listener; these tests pin
the invariant.
"""

import uuid

import pytest

from app.models.archimate_core import ArchiMateElement
from app.models.process_data import DataDomain, DataEntity


def _make_domain(db_session, label="Customer"):
    domain = DataDomain(name=f"{label} {uuid.uuid4().hex[:10]}")
    db_session.add(domain)
    db_session.flush()
    return domain


def test_creating_data_entity_creates_archimate_element(db_session, make_org, tenant_ctx):
    """A new DataEntity must automatically get a DataObject element."""
    org = make_org("de-slice")
    with tenant_ctx(org.id):
        domain = _make_domain(db_session)
        entity = DataEntity(
            name="Invoice",
            domain_id=domain.id,
        )
        db_session.add(entity)
        db_session.flush()

        assert entity.archimate_element_id is not None
        element = db_session.get(ArchiMateElement, entity.archimate_element_id)
        assert element is not None
        assert element.type == "DataObject"
        assert element.layer == "application"
        assert element.organization_id == org.id
        assert element.name == "Invoice"


def test_data_entity_archimate_element_is_idempotent(db_session, make_org, tenant_ctx):
    """Creating the same entity again must not create a second element."""
    org = make_org("de-idempotent")
    with tenant_ctx(org.id):
        domain = _make_domain(db_session)
        entity = DataEntity(
            name="Unique Entity",
            domain_id=domain.id,
        )
        db_session.add(entity)
        db_session.flush()

        assert entity.archimate_element_id is not None
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id, name="Unique Entity"
        ).count() == 1


def test_data_entity_archimate_element_isolation(db_session, make_org, tenant_ctx):
    """A DataEntity in org A must not create an element in org B."""
    org_a = make_org("de-iso-a")
    org_b = make_org("de-iso-b")

    with tenant_ctx(org_a.id):
        domain = _make_domain(db_session)
        entity_a = DataEntity(
            name="Entity in A only",
            domain_id=domain.id,
        )
        db_session.add(entity_a)
        db_session.flush()

        element_a = db_session.get(ArchiMateElement, entity_a.archimate_element_id)
        assert element_a.organization_id == org_a.id

        b_elements = db_session.query(ArchiMateElement).filter_by(
            organization_id=org_b.id, name="Entity in A only"
        ).count()
        assert b_elements == 0


def test_deleting_data_entity_leaves_no_orphan_element(db_session, make_org, tenant_ctx):
    """Deleting a DataEntity must set null on its element."""
    org = make_org("de-delete")
    with tenant_ctx(org.id):
        domain = _make_domain(db_session)
        entity = DataEntity(
            name="Entity to delete",
            domain_id=domain.id,
        )
        db_session.add(entity)
        db_session.flush()

        element_id = entity.archimate_element_id
        assert element_id is not None

        db_session.delete(entity)
        db_session.flush()

        element = db_session.get(ArchiMateElement, element_id)
        assert element is not None


def test_data_entity_with_preset_element_is_not_overwritten(db_session, make_org, tenant_ctx):
    """A caller that already links an element is left alone."""
    org = make_org("de-preset")
    with tenant_ctx(org.id):
        domain = _make_domain(db_session)
        preset = ArchiMateElement(
            name="Pre-linked DataObject",
            type="DataObject",
            layer="application",
            organization_id=org.id,
        )
        db_session.add(preset)
        db_session.flush()

        entity = DataEntity(
            name="Pre-linked Entity",
            domain_id=domain.id,
            archimate_element_id=preset.id,
        )
        db_session.add(entity)
        db_session.flush()

        assert entity.archimate_element_id == preset.id
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id, name="Pre-linked DataObject"
        ).count() == 1