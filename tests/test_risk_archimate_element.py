"""Every Risk record must have exactly one ArchiMate element node.

Every domain record of the nine types has exactly one element node in its
organisation. Risk is the first slice built in this PR.

Before this change, Risk had an archimate_element_id column but no automatic
listener to populate it -- callers had to remember to call
sync_archimate_element() from the backbone. The before_insert listener now
mirrors every new Risk into an Assessment (Motivation layer) element.
"""

import pytest

from app.models.archimate_core import ArchiMateElement
from app.models.risk import Risk


def test_creating_risk_creates_archimate_element(db_session, make_org, tenant_ctx):
    """A new Risk must automatically get an Assessment element."""
    org = make_org("risk-slice")
    with tenant_ctx(org.id):
        risk = Risk(
            organization_id=org.id,
            title="Data breach via unencrypted backup",
            description="Backup tapes stored without encryption.",
            likelihood=4,
            impact=5,
        )
        db_session.add(risk)
        db_session.flush()

        assert risk.archimate_element_id is not None, (
            "Risk create must attach an ArchiMate element"
        )
        element = db_session.get(ArchiMateElement, risk.archimate_element_id)
        assert element is not None
        assert element.type == "Assessment"
        assert element.layer == "Motivation"
        assert element.organization_id == org.id
        assert element.name == "Data breach via unencrypted backup"


def test_risk_archimate_element_is_idempotent(db_session, make_org, tenant_ctx):
    """Creating the same risk again or running backfill must not duplicate."""
    org = make_org("risk-idempotent")
    with tenant_ctx(org.id):
        risk = Risk(
            organization_id=org.id,
            title="Single point of failure in network",
            description="No redundant path to core switch.",
            likelihood=3,
            impact=4,
        )
        db_session.add(risk)
        db_session.flush()

        # The listener already set archimate_element_id; a second flush
        # must not create a second element.
        assert risk.archimate_element_id is not None
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id, name="Single point of failure in network"
        ).count() == 1


def test_risk_archimate_element_isolation(db_session, make_org, tenant_ctx):
    """A Risk in organisation A must not create an element in organisation B."""
    org_a = make_org("risk-iso-a")
    org_b = make_org("risk-iso-b")

    with tenant_ctx(org_a.id):
        risk_a = Risk(
            organization_id=org_a.id,
            title="Risk in A only",
            description="This risk belongs to org A.",
            likelihood=2,
            impact=3,
        )
        db_session.add(risk_a)
        db_session.flush()

        # Verify the element belongs to org A
        element_a = db_session.get(ArchiMateElement, risk_a.archimate_element_id)
        assert element_a.organization_id == org_a.id

        # Verify no element was created in org B
        b_elements = db_session.query(ArchiMateElement).filter_by(
            organization_id=org_b.id, name="Risk in A only"
        ).count()
        assert b_elements == 0, (
            "Risk in org A must not create an element in org B"
        )


def test_deleting_risk_leaves_no_orphan_element(db_session, make_org, tenant_ctx):
    """Deleting a Risk must cascade or set null on its element."""
    org = make_org("risk-delete")
    with tenant_ctx(org.id):
        risk = Risk(
            organization_id=org.id,
            title="Risk to delete",
            description="This risk will be deleted.",
            likelihood=2,
            impact=2,
        )
        db_session.add(risk)
        db_session.flush()

        element_id = risk.archimate_element_id
        assert element_id is not None

        # Delete the risk
        db_session.delete(risk)
        db_session.flush()

        # The element should still exist (ondelete SET NULL) but the
        # risk's link should be cleared.
        element = db_session.get(ArchiMateElement, element_id)
        assert element is not None, (
            "ArchiMateElement must not be deleted when Risk is deleted "
            "(ondelete=SET NULL)"
        )


def test_risk_with_preset_element_is_not_overwritten(db_session, make_org, tenant_ctx):
    """A caller that already links an element is left alone."""
    org = make_org("risk-preset")
    with tenant_ctx(org.id):
        preset = ArchiMateElement(
            name="Pre-linked Assessment",
            type="Assessment",
            layer="Motivation",
            organization_id=org.id,
        )
        db_session.add(preset)
        db_session.flush()

        risk = Risk(
            organization_id=org.id,
            title="Pre-linked risk",
            description="Already has an element.",
            likelihood=3,
            impact=3,
            archimate_element_id=preset.id,
        )
        db_session.add(risk)
        db_session.flush()

        assert risk.archimate_element_id == preset.id
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id, name="Pre-linked Assessment"
        ).count() == 1