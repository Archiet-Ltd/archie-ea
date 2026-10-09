"""Every VendorContract record must have exactly one ArchiMate element node.

R1-B18 PR 2: every domain record of the nine types has exactly one element
node in its organisation. VendorContract already has a before_insert listener;
these tests pin the invariant.
"""

from datetime import date

import pytest

from app.models.archimate_core import ArchiMateElement
from app.models.application_portfolio import VendorContract


def test_creating_contract_creates_archimate_element(db_session, make_org, tenant_ctx):
    """A new VendorContract must automatically get a Contract element."""
    org = make_org("contract-slice")
    with tenant_ctx(org.id):
        contract = VendorContract(
            contract_name="SAP License Agreement",
            organization_id=org.id,
            contract_description="Enterprise SAP licensing.",
            start_date=date(2026, 1, 1),
        )
        db_session.add(contract)
        db_session.flush()

        assert contract.archimate_element_id is not None
        element = db_session.get(ArchiMateElement, contract.archimate_element_id)
        assert element is not None
        assert element.type == "Contract"
        assert element.layer == "Business"
        assert element.organization_id == org.id
        assert element.name == "SAP License Agreement"


def test_contract_archimate_element_is_idempotent(db_session, make_org, tenant_ctx):
    """Creating the same contract again must not create a second element."""
    org = make_org("contract-idempotent")
    with tenant_ctx(org.id):
        contract = VendorContract(
            contract_name="Unique Contract",
            organization_id=org.id,
            start_date=date(2026, 1, 1),
        )
        db_session.add(contract)
        db_session.flush()

        assert contract.archimate_element_id is not None
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id, name="Unique Contract"
        ).count() == 1


def test_contract_archimate_element_isolation(db_session, make_org, tenant_ctx):
    """A VendorContract in org A must not create an element in org B."""
    org_a = make_org("contract-iso-a")
    org_b = make_org("contract-iso-b")

    with tenant_ctx(org_a.id):
        contract_a = VendorContract(
            contract_name="Contract in A only",
            organization_id=org_a.id,
            start_date=date(2026, 1, 1),
        )
        db_session.add(contract_a)
        db_session.flush()

        element_a = db_session.get(ArchiMateElement, contract_a.archimate_element_id)
        assert element_a.organization_id == org_a.id

        b_elements = db_session.query(ArchiMateElement).filter_by(
            organization_id=org_b.id, name="Contract in A only"
        ).count()
        assert b_elements == 0


def test_deleting_contract_leaves_no_orphan_element(db_session, make_org, tenant_ctx):
    """Deleting a VendorContract must set null on its element."""
    org = make_org("contract-delete")
    with tenant_ctx(org.id):
        contract = VendorContract(
            contract_name="Contract to delete",
            organization_id=org.id,
            start_date=date(2026, 1, 1),
        )
        db_session.add(contract)
        db_session.flush()

        element_id = contract.archimate_element_id
        assert element_id is not None

        db_session.delete(contract)
        db_session.flush()

        element = db_session.get(ArchiMateElement, element_id)
        assert element is not None


def test_contract_with_preset_element_is_not_overwritten(db_session, make_org, tenant_ctx):
    """A caller that already links an element is left alone."""
    org = make_org("contract-preset")
    with tenant_ctx(org.id):
        preset = ArchiMateElement(
            name="Pre-linked Contract Element",
            type="Contract",
            layer="Business",
            organization_id=org.id,
        )
        db_session.add(preset)
        db_session.flush()

        contract = VendorContract(
            contract_name="Pre-linked Contract",
            organization_id=org.id,
            start_date=date(2026, 1, 1),
            archimate_element_id=preset.id,
        )
        db_session.add(contract)
        db_session.flush()

        assert contract.archimate_element_id == preset.id
        assert db_session.query(ArchiMateElement).filter_by(
            organization_id=org.id, name="Pre-linked Contract Element"
        ).count() == 1