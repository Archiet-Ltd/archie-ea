"""The spend and renewal summaries count one organisation's contracts and no other's."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _contract(db_session, org, vendor_name, annual_cost, days_left, contract_type="saas"):
    from app.models.application_portfolio import VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization(name=vendor_name, named_by_organization_id=org.id)
    db_session.add(vendor)
    db_session.flush()
    contract = VendorContract(
        contract_name=f"{vendor_name} contract", organization_id=org.id, vendor_id=vendor.id,
        contract_type=contract_type, contract_value=annual_cost * 3, annual_cost=annual_cost, status="active",
        start_date=date.today() - timedelta(days=100), end_date=date.today() + timedelta(days=days_left))
    db_session.add(contract)
    db_session.flush()
    return contract


@pytest.fixture
def two_orgs(db_session, make_org):
    tag = uuid.uuid4().hex[:6]
    first, second = make_org("first"), make_org("second")
    _contract(db_session, first, f"Datadog {tag}", 2000.0, 25)
    _contract(db_session, first, f"Salesforce {tag}", 12000.0, 200, contract_type="licence")
    _contract(db_session, second, f"Globex {tag}", 5000.0, 40)
    _contract(db_session, second, f"Initech {tag}", 999.0, 400)
    return first, second, tag


def test_spend_summary_counts_one_organisations_contracts(app, two_orgs):
    """Outside a request there is no tenant fence, so the organisation in the signature is the only scope."""
    from flask import has_request_context

    from app.modules.procurement.services import get_spend_summary

    first, second, tag = two_orgs
    assert not has_request_context()
    mine = get_spend_summary(first.id)
    assert mine["total_contracts"] == 2
    assert mine["total_annual_cost"] == 14000.0
    assert mine["total_value"] == 42000.0
    assert mine["contracts_without_annual_cost"] == 0
    assert [name for name, _ in mine["top_vendors"]] == [f"Salesforce {tag}", f"Datadog {tag}"]
    assert mine["by_type"] == {"saas": {"count": 1, "annual_cost": 2000.0},
                               "licence": {"count": 1, "annual_cost": 12000.0}}

    theirs = get_spend_summary(second.id)
    assert theirs["total_contracts"] == 2
    assert theirs["total_annual_cost"] == 5999.0
    assert [name for name, _ in theirs["top_vendors"]] == [f"Globex {tag}", f"Initech {tag}"]


def test_renewal_summary_counts_one_organisations_contracts(app, two_orgs):
    from app.modules.procurement.services import get_renewal_summary

    first, second, _ = two_orgs
    mine = get_renewal_summary(first.id)
    assert mine["total"] == 2
    assert mine["critical"] == 1 and mine["ok"] == 1
    theirs = get_renewal_summary(second.id)
    assert theirs["total"] == 2
    assert theirs["warning"] == 1 and theirs["ok"] == 1


def test_summaries_take_the_organisation_and_no_default(app):
    """A caller that forgets the organisation gets an error, never every organisation's money."""
    from app.modules.procurement.services import get_renewal_summary, get_spend_summary

    with pytest.raises(TypeError):
        get_spend_summary()
    with pytest.raises(TypeError):
        get_renewal_summary()
