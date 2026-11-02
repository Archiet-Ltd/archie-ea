"""A vendor row, the Data quality banner and the contract figures state one answer.

A vendor whose systems record no cost but whose contracts state one shows that cost, and says where it
comes from. The banner on the applications list states the vendor coverage the Vendors page states. The
decommissioned count leaves out copies. The run cost's basis counts the contracts that state a cost.
Another organisation's rows never count.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

import pytest

from tests.test_vendor_pages_agree import _admin_client, _answer, _forget_cached_user

pytestmark = pytest.mark.usefixtures("db_session")


def _estate(db_session, org, tag, contract_cost=48000.0):
    """Two systems of one vendor (no cost on either), one without a vendor, one decommissioned, one copy."""
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization(name=f"Vendor {tag}")
    db_session.add(vendor)
    db_session.flush()
    apps = []
    for i, (vendor_id, status, name) in enumerate([
            (vendor.id, "operational", f"Sys {tag} 0"), (vendor.id, "operational", f"Sys {tag} 1"),
            (None, "operational", f"Sys {tag} 2"), (None, "5. Decommissioned", f"Sys {tag} 3"),
            (None, "5. Decommissioned", f"(Duplicate) Sys {tag} 4")]):
        a = ApplicationComponent(name=name, organization_id=org.id, lifecycle_status=status,
                                 vendor_organization_id=vendor_id)
        db_session.add(a)
        apps.append(a)
    db_session.flush()
    for a, cost in ((apps[0], contract_cost * 0.75), (apps[1], contract_cost * 0.25), (apps[1], None)):
        db_session.add(VendorContract(contract_name=f"Deal {tag} {uuid.uuid4().hex[:4]}", organization_id=org.id,
                                      vendor_id=vendor.id, application_id=a.id, annual_cost=cost,
                                      status="active", start_date=date.today() - timedelta(days=90)))
    db_session.flush()
    return vendor, apps


def test_vendor_row_cost_is_its_contracts_cost_and_names_its_source(app, db_session, make_org, tenant_ctx):
    from app.services import vendor_register

    mine, theirs = make_org("vf-a"), make_org("vf-b")
    tag = uuid.uuid4().hex[:6]
    vendor, _ = _estate(db_session, mine, "m" + tag)
    _estate(db_session, theirs, "t" + tag, contract_cost=777000.0)
    with tenant_ctx(mine.id):
        row = next(r for r in vendor_register.register(mine.id) if r["id"] == vendor.id)
    assert row["annual_cost"] == 48000.0
    assert row["annual_cost_basis"] == "from active contracts"

    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    text = _answer(client.get("/applications/vendors").get_data(as_text=True))
    assert "No cost recorded" not in text
    assert "777" not in text


def test_banner_and_vendors_page_state_the_same_coverage(app, db_session, make_org):
    mine, theirs = make_org("vf-c"), make_org("vf-d")
    tag = uuid.uuid4().hex[:6]
    _estate(db_session, mine, "m" + tag)
    _estate(db_session, theirs, "t" + tag)
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    vendors = _answer(client.get("/applications/vendors").get_data(as_text=True))
    supplied, total = re.search(r"suppl(?:y|ies) (\d+) of your (\d+) systems?", vendors).groups()
    _forget_cached_user()
    listing = _answer(client.get("/applications/").get_data(as_text=True))
    banner = re.search(r"(\d+) of (\d+) have no vendor", listing)
    assert banner, listing[:800]
    assert (int(banner.group(2)), int(banner.group(1))) == (int(total), int(total) - int(supplied))
    assert (supplied, total) == ("2", "3")


def test_decommissioned_count_leaves_out_copies(db_session, make_org, tenant_ctx):
    from app.services import vendor_register

    mine = make_org("vf-e")
    _estate(db_session, mine, "m" + uuid.uuid4().hex[:6])
    with tenant_ctx(mine.id):
        coverage = vendor_register.system_coverage(mine.id)
    assert coverage["total_systems"] == 3
    assert coverage["decommissioned"] == 1


def test_run_cost_basis_counts_the_contracts_that_state_a_cost(db_session, make_org, tenant_ctx):
    from app.modules.dashboard.services.overview_figures import _name_the_basis, _run_cost

    mine, theirs = make_org("vf-f"), make_org("vf-g")
    _estate(db_session, mine, "m" + uuid.uuid4().hex[:6])
    _estate(db_session, theirs, "t" + uuid.uuid4().hex[:6])
    with tenant_ctx(mine.id):
        run_cost = _run_cost(mine.id, __import__("datetime").datetime.utcnow())
    _name_the_basis(run_cost, None)
    assert run_cost["total"] == 48000.0
    assert run_cost["basis"] == "from 2 active contracts"


def test_a_stated_contract_cost_fills_the_currency_too(db_session, make_org):
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.services.application_cost_accessor import state_contract

    mine = make_org("vf-h")
    system = ApplicationComponent(name="Cur " + uuid.uuid4().hex[:5], organization_id=mine.id,
                                  lifecycle_status="operational")
    db_session.add(system)
    db_session.flush()
    open_contract = VendorContract(contract_name="Open", organization_id=mine.id, application_id=system.id,
                                   annual_cost=None, status="active",
                                   start_date=date.today() - timedelta(days=5))
    db_session.add(open_contract)
    db_session.flush()
    open_contract.currency = None  # a contract recorded before currency was kept
    db_session.flush()
    contract = state_contract(system, "Anyone", 9000)
    assert contract.annual_cost == 9000.0
    assert contract.currency
