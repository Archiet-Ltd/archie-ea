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


def _mixed_estate(db_session, org, tag):
    """Vendor A names: 20000 on B's live system, 9000 on A's own decommissioned system, 300 on no system,
    an expired 5000 on A's live system. Vendor B has 1000 on its live system. A's live system records no cost."""
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    a = VendorOrganization(name=f"Alpha {tag}")
    b = VendorOrganization(name=f"Bravo {tag}")
    db_session.add_all([a, b])
    db_session.flush()
    live_a = ApplicationComponent(name=f"Alpha live {tag}", organization_id=org.id,
                                  lifecycle_status="operational", vendor_organization_id=a.id)
    dead_a = ApplicationComponent(name=f"Alpha old {tag}", organization_id=org.id,
                                  lifecycle_status="5. Decommissioned", vendor_organization_id=a.id)
    live_b = ApplicationComponent(name=f"Bravo live {tag}", organization_id=org.id,
                                  lifecycle_status="operational", vendor_organization_id=b.id)
    db_session.add_all([live_a, dead_a, live_b])
    db_session.flush()
    soon = date.today() + timedelta(days=40)
    rows = [("cross", a, live_b, 20000.0, "active", date.today() + timedelta(days=300)),
            ("dead", a, dead_a, 9000.0, "active", date.today() + timedelta(days=10)),
            ("loose", a, None, 300.0, "active", date.today() + timedelta(days=200)),
            ("expired", a, live_a, 5000.0, "expired", date.today() - timedelta(days=5)),
            ("own", b, live_b, 1000.0, "active", soon)]
    for name, vendor, system, cost, status, end in rows:
        db_session.add(VendorContract(contract_name=f"{name} {tag}", organization_id=org.id, vendor_id=vendor.id,
                                      application_id=system.id if system else None, annual_cost=cost,
                                      status=status, end_date=end, start_date=date.today() - timedelta(days=90)))
    db_session.flush()
    return a, b


def _money(text):
    return {float(m.replace(",", "")) for m in re.findall(r"£\s?([\d,]+(?:\.\d+)?)", text)}


def test_every_vendor_figure_agrees_across_vendors_vendor_page_spend_and_renewal(
        app, db_session, make_org, tenant_ctx):
    from app.modules.procurement.services import get_spend_summary
    from app.services import vendor_register

    mine, theirs = make_org("vf-m"), make_org("vf-n")
    tag = uuid.uuid4().hex[:6]
    a, b = _mixed_estate(db_session, mine, "m" + tag)
    _mixed_estate(db_session, theirs, "t" + tag)
    with tenant_ctx(mine.id):
        rows = {r["id"]: r for r in vendor_register.register(mine.id)}
        summary = get_spend_summary(mine.id)
    assert rows[a.id]["annual_cost"] == 300.0
    assert rows[b.id]["annual_cost"] == 21000.0
    assert rows[a.id]["next_renewal"] == (date.today() + timedelta(days=200)).isoformat()
    assert summary["total_contracts"] == 3
    assert summary["total_annual_cost"] == 21300.0
    assert [(n, v["annual_cost"]) for n, v in summary["top_vendors"]] == [
        (f"Bravo m{tag}", 21000.0), (f"Alpha m{tag}", 300.0)]

    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    page = _answer(client.get(f"/applications/vendors/{a.id}").get_data(as_text=True))
    assert "300.00 a year" in page and "Systems supplied" in page
    headline = re.search(r"of your systems?,? (£[\d,.]+) a year", page)
    assert headline and _money(headline.group(1)) == {300.0}
    # The contracts the cost leaves out are listed, each with the reason, and none states a figure
    assert "Not counted" in page
    for left_out in ("dead", "expired", "cross"):
        assert f"{left_out} m{tag}" in page
    assert "9,000" not in page and "20,000" not in page and "5,000" not in page
    _forget_cached_user()
    b_page = _answer(client.get(f"/applications/vendors/{b.id}").get_data(as_text=True))
    assert re.search(r"1 of your systems?,? £21,000\.00 a year", b_page)
    assert f"cross m{tag}" in b_page and "20,000.00 a year" in b_page
    _forget_cached_user()
    vendors = _answer(client.get("/applications/vendors").get_data(as_text=True))
    assert "21,000.00" in vendors and "777" not in vendors
    _forget_cached_user()
    spend = _answer(client.get("/procurement/spend").get_data(as_text=True))
    assert "£21k" in spend or "21,300" in spend
    assert "9,000" not in spend and "30,300" not in spend


def test_no_module_other_than_the_register_adds_up_contract_annual_cost_per_vendor():
    """Audited exceptions add contracts up by something else: extracted contracts, one system, a category."""
    import pathlib

    audited = {"clause_extraction_service.py", "application_cost_accessor.py", "architect_persona_charters.py"}
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "vendor_register.py" or path.name in audited:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if re.search(r"(\bsum|total_of_stated|func\.sum)\(.*(annual_cost|VendorContract)", line) and \
                    "contract" in line.lower():
                offenders.append(f"{path.relative_to(root)}:{number}")
    assert offenders == []


def test_spend_and_the_vendor_page_read_the_register_instead_of_querying_contracts_themselves():
    import inspect

    from app.modules.applications.routes import vendor_display_routes
    from app.modules.procurement.services import get_spend_summary

    spend = inspect.getsource(get_spend_summary)
    assert "VendorContract" not in spend and "contract_ledger" in spend
    page = inspect.getsource(vendor_display_routes.vendor_detail)
    assert "VendorContract" not in page and "contract_ledger" in page
