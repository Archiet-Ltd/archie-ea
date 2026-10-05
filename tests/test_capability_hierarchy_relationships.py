"""A capability's parent_capability_id must be projected into archimate_relationships.

Before this fix, `create_capability_archimate_element` gave every
BusinessCapability an ArchiMateElement but nothing ever wrote the relationship
for a capability's own parent_capability_id -- so a capability with a real,
already-recorded parent still showed up as "no relationships" on the
ArchiMate elements page. These tests pin the write-time sync
(`_sync_capability_hierarchy_relationship`, called from `after_insert`/
`after_update`) so it cannot silently regress back to an isolated element.
"""

import pytest

from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.business_capabilities import BusinessCapability


def _composition_between(db_session, parent_element_id, child_element_id):
    return (
        db_session.query(ArchiMateRelationship)
        .filter_by(
            source_id=parent_element_id,
            target_id=child_element_id,
            type="composition",
        )
        .first()
    )


@pytest.mark.usefixtures("db_session")
def test_child_capability_gets_composition_relationship_to_parent(
    db_session, make_org, tenant_ctx
):
    org = make_org("cap-hierarchy")
    with tenant_ctx(org.id):
        parent = BusinessCapability(name="Order Management", organization_id=org.id, level=1)
        db_session.add(parent)
        db_session.commit()

        child = BusinessCapability(
            name="Order Fulfilment",
            organization_id=org.id,
            level=2,
            parent_capability_id=parent.id,
        )
        db_session.add(child)
        db_session.commit()

        rel = _composition_between(db_session, parent.archimate_element_id, child.archimate_element_id)
        assert rel is not None, (
            "a capability with a parent_capability_id must get a composition "
            "relationship from the parent's element to its own -- this is the "
            "exact class of orphan element the elements page was reporting"
        )
        assert rel.derived_from == "capability-hierarchy"
        assert rel.organization_id == org.id


@pytest.mark.usefixtures("db_session")
def test_capability_with_no_parent_gets_no_hierarchy_relationship(
    db_session, make_org, tenant_ctx
):
    org = make_org("cap-hierarchy")
    with tenant_ctx(org.id):
        cap = BusinessCapability(name="Standalone Capability", organization_id=org.id, level=1)
        db_session.add(cap)
        db_session.commit()

        count = (
            db_session.query(ArchiMateRelationship)
            .filter_by(target_id=cap.archimate_element_id, derived_from="capability-hierarchy")
            .count()
        )
        assert count == 0, "a capability with no parent must not get an invented relationship"


@pytest.mark.usefixtures("db_session")
def test_child_created_before_parent_is_synced_is_not_fatal(
    db_session, make_org, tenant_ctx
):
    """Out-of-order sync must defer, never raise or fabricate a relationship.

    parent_capability_id carries a real FK to business_capability.id, so a
    child can only ever reference a parent ROW that already exists -- but
    that parent row's own archimate_element_id can still be NULL (a raw-SQL
    bulk insert that bypassed the ORM listener, or any write path outside
    this model's own before_insert). Simulate that directly rather than via
    a nonexistent parent id, which the FK itself already rejects.
    """
    org = make_org("cap-hierarchy")
    with tenant_ctx(org.id):
        parent = BusinessCapability(name="Sales", organization_id=org.id, level=1)
        db_session.add(parent)
        db_session.commit()

        db_session.execute(
            ArchiMateRelationship.__table__.delete().where(
                ArchiMateRelationship.target_id == parent.archimate_element_id
            )
        )
        db_session.execute(
            BusinessCapability.__table__.update()
            .where(BusinessCapability.id == parent.id)
            .values(archimate_element_id=None)
        )
        db_session.commit()
        db_session.expire(parent)

        child = BusinessCapability(
            name="Lead Generation",
            organization_id=org.id,
            level=2,
            parent_capability_id=parent.id,
        )
        db_session.add(child)
        db_session.commit()  # must not raise

        count = (
            db_session.query(ArchiMateRelationship)
            .filter_by(target_id=child.archimate_element_id, derived_from="capability-hierarchy")
            .count()
        )
        assert count == 0


@pytest.mark.usefixtures("db_session")
def test_reparenting_replaces_the_stale_relationship(db_session, make_org, tenant_ctx):
    org = make_org("cap-hierarchy")
    with tenant_ctx(org.id):
        parent_a = BusinessCapability(name="Sales", organization_id=org.id, level=1)
        parent_b = BusinessCapability(name="Marketing", organization_id=org.id, level=1)
        db_session.add_all([parent_a, parent_b])
        db_session.commit()

        child = BusinessCapability(
            name="Lead Generation",
            organization_id=org.id,
            level=2,
            parent_capability_id=parent_a.id,
        )
        db_session.add(child)
        db_session.commit()
        assert _composition_between(db_session, parent_a.archimate_element_id, child.archimate_element_id)

        child.parent_capability_id = parent_b.id
        db_session.commit()

        assert _composition_between(db_session, parent_b.archimate_element_id, child.archimate_element_id)
        assert _composition_between(db_session, parent_a.archimate_element_id, child.archimate_element_id) is None, (
            "re-parenting must remove the stale relationship to the old parent, "
            "not leave two composition rows pointing at the same child"
        )
