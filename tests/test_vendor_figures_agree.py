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
    """Audited exceptions add contracts up by something else: extracted contracts, one system.

    Reads each file as a syntax tree, so a sum split over several lines is seen as one call.
    """
    import ast
    import pathlib

    audited = set()
    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "vendor_register.py" or path.name in audited:
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name not in ("sum", "total_of_stated"):
                continue
            segment = ast.get_source_segment(source, node) or ""
            if re.search(r"annual_cost|VendorContract", segment) and "contract" in segment.lower():
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert offenders == []


def test_the_multi_line_sum_check_sees_a_sum_split_over_lines():
    import ast

    source = "total = sum(\n    c.annual_cost\n    for c in contracts\n)\n"
    call = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call))
    segment = ast.get_source_segment(source, call)
    assert "annual_cost" in segment and "contract" in segment.lower()
    assert not any("annual_cost" in line and "sum(" in line for line in source.splitlines())


def test_spend_and_the_vendor_page_read_the_register_instead_of_querying_contracts_themselves():
    import inspect

    from app.modules.applications.routes import vendor_display_routes
    from app.modules.procurement.services import get_spend_summary

    spend = inspect.getsource(get_spend_summary)
    assert "VendorContract" not in spend and "contract_ledger" in spend
    page = inspect.getsource(vendor_display_routes.vendor_detail)
    assert "VendorContract" not in page and "contract_ledger" in page


def _cost_estate(db_session, org, tag):
    """C records 50000 on its live system and carries a 1000 contract there. D has a 4000 contract on a live
    system that names no vendor and a 9000 contract on a decommissioned system. B has 12000 on its live system."""
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    b, c, d = (VendorOrganization(name=f"{n} {tag}") for n in ("Beacon", "Cobalt", "Delta"))
    db_session.add_all([b, c, d])
    db_session.flush()

    def system(name, vendor, status="operational", cost=None):
        a = ApplicationComponent(name=f"{name} {tag}", organization_id=org.id, lifecycle_status=status,
                                 vendor_organization_id=vendor.id if vendor else None,
                                 total_cost_of_ownership=cost)
        db_session.add(a)
        db_session.flush()
        return a

    cobalt, beacon = system("Cobalt WMS", c, cost=50000), system("Beacon CRM", b)
    orphan, dead = system("Orphan", None), system("Delta old", d, status="5. Decommissioned")
    for name, vendor, on, cost in (("c", c, cobalt, 1000.0), ("b", b, beacon, 12000.0),
                                   ("orphan", d, orphan, 4000.0), ("dead", d, dead, 9000.0)):
        db_session.add(VendorContract(contract_name=f"{name} {tag}", organization_id=org.id, vendor_id=vendor.id,
                                      application_id=on.id, annual_cost=cost, status="active",
                                      start_date=date.today() - timedelta(days=90),
                                      end_date=date.today() + timedelta(days=400)))
    db_session.flush()
    return b, c, d


def test_totals_shares_and_the_overview_follow_the_vendors_rows(app, db_session, make_org, tenant_ctx):
    from datetime import datetime

    from app.modules.dashboard.services.overview_figures import _run_cost
    from app.modules.procurement.services import get_spend_summary
    from app.services import vendor_register

    mine, theirs = make_org("vf-p"), make_org("vf-q")
    tag = uuid.uuid4().hex[:6]
    _cost_estate(db_session, mine, "m" + tag)
    _cost_estate(db_session, theirs, "t" + tag)
    with tenant_ctx(mine.id):
        rows = vendor_register.register(mine.id)
        summary = get_spend_summary(mine.id)
        run_cost = _run_cost(mine.id, datetime.utcnow())
    figures = {r["name"]: r["annual_cost"] for r in rows}
    assert figures == {f"Beacon m{tag}": 12000.0, f"Cobalt m{tag}": 50000.0, f"Delta m{tag}": None}
    # the total is the rows' sum, plus the contracts that name no vendor, which the page states apart
    assert summary["total_annual_cost"] == 66000.0 == run_cost["total"]
    assert summary["unassigned_annual_cost"] == 4000.0 and summary["unassigned_contracts"] == 1
    assert [(n, v["annual_cost"]) for n, v in summary["top_vendors"]] == [
        (f"Cobalt m{tag}", 50000.0), (f"Beacon m{tag}", 12000.0)]
    assert all(v["annual_cost"] <= summary["total_annual_cost"] for _, v in summary["top_vendors"])
    assert run_cost["top"] == {"name": f"Cobalt m{tag}", "percent": 76}
    # the month series and the stated count read the counted contracts: the decommissioned 9000 counts nowhere
    assert run_cost["stated_contracts"] == 3  # Cobalt's 1000 is active, though its system records its own cost
    # the monthly figure reads "Active contracts, per month": every active contract (Beacon's 12000, the
    # 4000 that name no vendor and Cobalt's 1000), not the 50000 a system records for itself
    assert run_cost["this_month"] == round(17000.0 / 12)
    assert run_cost["change"] is None

    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    vendors = _answer(client.get("/applications/vendors").get_data(as_text=True))
    assert "£66,000.00, including £4,000.00 on 1 active contract that names no vendor" in vendors
    _forget_cached_user()
    spend = _answer(client.get("/procurement/spend").get_data(as_text=True))
    assert "£66k" in spend and "76%" in spend
    assert not [p for p in re.findall(r"(\d+)%", spend) if int(p) > 100]
    assert "£4k on 1 active contract that names no vendor" in spend


def test_a_contract_on_a_system_with_no_vendor_says_so(db_session, make_org, tenant_ctx):
    from app.services import vendor_register

    mine = make_org("vf-r")
    tag = uuid.uuid4().hex[:6]
    _, _, d = _cost_estate(db_session, mine, tag)
    with tenant_ctx(mine.id):
        reasons = {c.contract_name: why for c, why in vendor_register.contracts_with_standing(d.id, mine.id)}
    assert reasons[f"orphan {tag}"] == "Not counted for this vendor: the system it sits on names no vendor"
    assert "decommissioned" in reasons[f"dead {tag}"]


def test_the_assistants_procurement_context_reads_the_counted_contracts(app, db_session, make_org, tenant_ctx):
    from flask import g

    from app.modules.ai_chat.services.architect_persona_charters import _procurement_context

    mine, theirs = make_org("vf-s"), make_org("vf-t")
    tag = uuid.uuid4().hex[:6]
    _cost_estate(db_session, mine, "m" + tag)
    _cost_estate(db_session, theirs, "t" + tag)
    with app.test_request_context("/"):
        g.current_org_id = mine.id
        text = _procurement_context()
    assert "uncategorised: 17,000" in text, text


def _lapsed_estate(db_session, org, tag):
    """Vendor L records 20000 on its live system, which also carries a 500 contract. It has a 3000 contract
    that names it and no system, still marked active though it ended 10 days ago (renewal date 20 days ago),
    and a 6000 contract with 200 days to run."""
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization(name=f"Lapse {tag}")
    db_session.add(vendor)
    db_session.flush()
    system = ApplicationComponent(name=f"Lapse sys {tag}", organization_id=org.id, lifecycle_status="operational",
                                  vendor_organization_id=vendor.id, total_cost_of_ownership=20000)
    db_session.add(system)
    db_session.flush()
    today = date.today()
    for name, on, cost, end, renewal in (
            ("on-system", system.id, 500.0, today + timedelta(days=300), None),
            ("lapsed", None, 3000.0, today - timedelta(days=10), today - timedelta(days=20)),
            ("running", None, 6000.0, today + timedelta(days=200), today + timedelta(days=30))):
        db_session.add(VendorContract(contract_name=f"{name} {tag}", organization_id=org.id, vendor_id=vendor.id,
                                      application_id=on, annual_cost=cost, status="active",
                                      start_date=today - timedelta(days=400), end_date=end, renewal_date=renewal))
    db_session.flush()
    return vendor


def test_a_contract_whose_end_date_has_passed_counts_nowhere_even_when_marked_active(
        app, db_session, make_org, tenant_ctx):
    from datetime import datetime

    from app.modules.dashboard.services.overview_figures import _run_cost
    from app.modules.procurement.services import get_spend_summary
    from app.services import vendor_register

    mine, theirs = make_org("vf-u"), make_org("vf-v")
    tag = uuid.uuid4().hex[:6]
    vendor = _lapsed_estate(db_session, mine, "m" + tag)
    _lapsed_estate(db_session, theirs, "t" + tag)
    with tenant_ctx(mine.id):
        row = next(r for r in vendor_register.register(mine.id) if r["id"] == vendor.id)
        summary = get_spend_summary(mine.id)
        run_cost = _run_cost(mine.id, datetime.utcnow())
        reasons = {c.contract_name: why for c, why in vendor_register.contracts_with_standing(vendor.id, mine.id)}
    assert row["annual_cost"] == 26000.0
    assert row["next_renewal"] == (date.today() + timedelta(days=30)).isoformat()  # its renewal date, as the renewals page says
    assert summary["total_annual_cost"] == 26000.0
    assert summary["total_contracts"] == 2
    assert run_cost["stated_contracts"] == 2
    assert reasons[f"lapsed m{tag}"].startswith("Not counted: the contract ended on ")
    assert reasons[f"running m{tag}"] is None


def test_the_overview_monthly_cost_is_the_annual_figure_over_twelve(app, db_session, make_org, tenant_ctx):
    from datetime import datetime

    from app.modules.dashboard.services.overview_figures import _name_the_basis, _run_cost

    mine, theirs = make_org("vf-w"), make_org("vf-x")
    tag = uuid.uuid4().hex[:6]
    _lapsed_estate(db_session, mine, "m" + tag)
    _lapsed_estate(db_session, theirs, "t" + tag)
    with tenant_ctx(mine.id):
        run_cost = _run_cost(mine.id, datetime.utcnow())
    _name_the_basis(run_cost, None)
    assert run_cost["total"] == 26000.0
    # the active contracts: the 6000 that runs and the 500 on a system that records its own cost; the lapsed
    # 3000 is not active
    assert run_cost["this_month"] == round(6500.0 / 12)
    # no month-by-month history exists on this basis, so no trend is claimed
    assert run_cost["change"] is None
    # the basis names both active contracts; the system's own cost stands in for the 500 in the total
    assert run_cost["basis"] == "from the costs recorded on systems and 2 active contracts"


def test_a_contract_on_a_system_that_records_its_own_cost_says_it_is_not_added(db_session, make_org, tenant_ctx):
    from app.services import vendor_register

    mine = make_org("vf-y")
    tag = uuid.uuid4().hex[:6]
    vendor = _lapsed_estate(db_session, mine, tag)
    with tenant_ctx(mine.id):
        reasons = {c.contract_name: why for c, why in vendor_register.contracts_with_standing(vendor.id, mine.id)}
    assert reasons[f"on-system {tag}"] == "Not added: the system it sits on records its own cost"


def test_spend_and_the_assistant_say_what_their_figures_are(app, db_session, make_org):
    from flask import g

    from app.modules.ai_chat.services.architect_persona_charters import _procurement_context

    mine, theirs = make_org("vf-z"), make_org("vf-za")
    tag = uuid.uuid4().hex[:6]
    _lapsed_estate(db_session, mine, "m" + tag)
    _lapsed_estate(db_session, theirs, "t" + tag)
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    spend = _answer(client.get("/procurement/spend").get_data(as_text=True))
    assert "£26k" in spend
    assert "costs recorded on systems plus the annual cost of active contracts" in spend
    assert "Contract cost by type" in spend and "costs recorded on systems are not in it" in spend
    with app.test_request_context("/"):
        g.current_org_id = mine.id
        text = _procurement_context()
    assert "Annual contract spend by category (contracts only, not the costs recorded on systems)" in text
    assert "Renewals within 90 days (1)" in text and "running m" + tag in text and "lapsed" not in text


def test_the_vendor_page_names_its_contract_value_as_dollars(app, db_session, make_org):
    from app.models.vendor.vendor_organization import VendorOrganization

    mine = make_org("vf-zb")
    vendor = VendorOrganization(name="Dollar " + uuid.uuid4().hex[:5], contract_value_annual=12000)
    db_session.add(vendor)
    db_session.flush()
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    page = client.get(f"/applications/vendors/{vendor.id}")
    text = _answer(page.get_data(as_text=True))
    assert "Annual Contract Value (USD)" in text and "$12,000" in text


def test_the_overview_monthly_cost_is_empty_when_no_active_contract_states_a_cost(
        db_session, make_org, tenant_ctx):
    from datetime import datetime

    from app.models.application_portfolio import ApplicationComponent
    from app.modules.dashboard.services.overview_figures import _run_cost

    mine = make_org("vf-mz")
    tag = uuid.uuid4().hex[:6]
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization(name=f"Solo {tag}")
    db_session.add(vendor)
    db_session.flush()
    db_session.add(ApplicationComponent(name=f"Ledger {tag}", organization_id=mine.id,
                                        lifecycle_status="operational", total_cost_of_ownership=20000,
                                        vendor_organization_id=vendor.id))
    db_session.flush()
    with tenant_ctx(mine.id):
        run_cost = _run_cost(mine.id, datetime.utcnow())
    # the annual figure keeps the system's own cost; the monthly figure is the sum over active contracts, which is none
    assert run_cost["total"] == 20000.0
    assert run_cost["this_month"] == 0
    assert run_cost["series"] == [{"month": datetime.utcnow().strftime("%b"), "value": 0}]
    assert run_cost["this_month_short"] == "\u00a30"


def test_the_spend_suggestions_context_counts_the_ledger_contracts_only_and_names_each_figure(
        db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import VendorContract
    from app.modules.procurement.procurement_ai_service import _build_spend_context, _build_spend_prompt

    mine, theirs = make_org("vf-sa"), make_org("vf-sb")
    tag = uuid.uuid4().hex[:6]
    vendors = {}
    for org, t in ((mine, "m" + tag), (theirs, "t" + tag)):
        vendors[org.id] = _lapsed_estate(db_session, org, t)
        # a contract that is no longer active and a 9000 one that has lapsed are not spend
        db_session.add(VendorContract(contract_name=f"closed {t}", organization_id=org.id,
                                      vendor_id=vendors[org.id].id, annual_cost=9000.0, status="terminated", start_date=date.today() - timedelta(days=50),
                                      end_date=date.today() + timedelta(days=100)))
        for c in db_session.query(VendorContract).filter_by(organization_id=org.id):
            c.contract_category = "Support"
        db_session.flush()
    with tenant_ctx(mine.id):
        context = _build_spend_context(mine.id)
    # the 3000 that lapsed 10 days ago and the 9000 that is closed are out; the 500 and the 6000 are in
    assert context["annual_cost_of_active_contracts_by_category"] == {"Support": 6500.0}
    assert context["total_annual_cost_of_systems_plus_active_contracts"] == 26000.0
    assert "spend_by_category" not in context and "spend_by_type" not in context
    assert "real, verified spend" not in _build_spend_prompt(context)


def _gbp_org_with_contracts(db_session, org, tag, rows):
    """A vendor with unattached active contracts: rows are (annual cost, currency)."""
    from app.models.application_portfolio import VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization(name=f"Cur {tag}")
    db_session.add(vendor)
    db_session.flush()
    made = []
    for i, (cost, currency) in enumerate(rows):
        c = VendorContract(contract_name=f"Cur {tag} {i}", organization_id=org.id, vendor_id=vendor.id,
                           annual_cost=cost, currency=currency, status="active",
                           start_date=date.today() - timedelta(days=30),
                           end_date=date.today() + timedelta(days=300))
        db_session.add(c)
        made.append(c)
    db_session.flush()
    return vendor, made


def test_the_ledger_adds_only_contracts_in_the_organisation_currency_and_lists_the_rest_apart(
        db_session, make_org, tenant_ctx):
    from app.services import vendor_register

    mine, theirs = make_org("vf-ca"), make_org("vf-cb")
    tag = uuid.uuid4().hex[:6]
    vendor, made = _gbp_org_with_contracts(
        db_session, mine, "m" + tag, [(6000.0, "GBP"), (2000.0, None), (4000.0, "EUR"), (1000.0, "EUR")])
    _gbp_org_with_contracts(db_session, theirs, "t" + tag, [(90000.0, "USD")])
    with tenant_ctx(mine.id):
        ledger = vendor_register.contract_ledger(mine.id)
        rows = vendor_register.register(mine.id)
        total = vendor_register.total_annual_cost(rows, ledger)
        other = vendor_register.other_currency_totals(ledger)
    assert [c.annual_cost for c in ledger["counted"]] == [6000.0, 2000.0]
    assert total == 8000.0
    assert other == {"EUR": {"total": 5000.0, "contracts": 2}}
    assert "EUR" in ledger["left_out"][made[2].id] and "GBP" in ledger["left_out"][made[2].id]


def test_a_contract_is_shown_in_the_currency_it_was_recorded_in(app, db_session, make_org):
    mine, theirs = make_org("vf-cc"), make_org("vf-cd")
    tag = uuid.uuid4().hex[:6]
    _, made = _gbp_org_with_contracts(db_session, mine, "m" + tag, [(4000.0, "EUR"), (6000.0, "GBP")])
    _gbp_org_with_contracts(db_session, theirs, "t" + tag, [(777.0, "USD")])
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    detail = _answer(client.get(f"/procurement/contracts/{made[0].id}").get_data(as_text=True))
    assert "\u20ac4,000" in detail and "\u00a34,000" not in detail
    _forget_cached_user()
    pound = _answer(client.get(f"/procurement/contracts/{made[1].id}").get_data(as_text=True))
    assert "\u00a36,000" in pound


def test_the_vendors_page_states_the_other_currency_apart_from_the_total(app, db_session, make_org):
    mine, theirs = make_org("vf-ce"), make_org("vf-cf")
    tag = uuid.uuid4().hex[:6]
    _gbp_org_with_contracts(db_session, mine, "m" + tag, [(6000.0, "GBP"), (4000.0, "EUR")])
    _gbp_org_with_contracts(db_session, theirs, "t" + tag, [(555000.0, "USD")])
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    text = _answer(client.get("/applications/vendors").get_data(as_text=True))
    assert "\u00a36,000" in text
    assert "\u00a310,000" not in text
    assert "\u20ac4,000" in text and "not added" in text.lower()
    assert "555,000" not in text and not re.search(r"\$\s?\d", text)


def test_the_contract_form_offers_the_organisation_currency_from_a_list(app, db_session, make_org):
    from app.models.user import User

    mine = make_org("vf-cg")
    client = _admin_client(app, db_session, mine)
    db_session.query(User).filter_by(organization_id=mine.id).update({"enterprise_role": "procurement"})
    db_session.flush()
    _forget_cached_user()
    response = client.get("/procurement/contracts/new")
    assert response.status_code == 200, response.status_code
    html = response.get_data(as_text=True)
    select = re.search(r'<select[^>]*name="currency".*?</select>', html, re.S)
    assert select, "currency is free text"
    assert re.search(r'<option[^>]*value="GBP"[^>]*selected', select.group(0))
    assert 'value="EUR"' in select.group(0)
    _forget_cached_user()
    posted = client.post("/procurement/contracts/new", data={"contract_name": "Odd", "currency": "ZZZ"})
    assert posted.status_code in (200, 400)
    assert "ZZZ" not in client.get("/procurement/contracts").get_data(as_text=True)


def test_a_contract_saves_the_chosen_currency_and_the_organisation_currency_when_none_is_given(
        app, db_session, make_org):
    from app.models.application_portfolio import VendorContract
    from app.models.user import User

    mine = make_org("vf-ch")
    client = _admin_client(app, db_session, mine)
    db_session.query(User).filter_by(organization_id=mine.id).update({"enterprise_role": "procurement"})
    db_session.flush()
    tag = uuid.uuid4().hex[:6]
    for name, extra, expected in ((f"Euro {tag}", {"currency": "eur"}, "EUR"), (f"Plain {tag}", {}, "GBP")):
        _forget_cached_user()
        posted = client.post("/procurement/contracts/new",
                             data={"contract_name": name, "annual_cost": "100", **extra})
        assert posted.status_code == 302, posted.status_code
        saved = db_session.query(VendorContract).filter_by(organization_id=mine.id, contract_name=name).one()
        assert saved.currency == expected


def _euro_only_system(db_session, org, tag, amount=1200.0, currency="EUR"):
    """A system that records no cost of its own, with one active contract stated in ``currency``."""
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    vendor = VendorOrganization(name=f"Eu {tag}")
    db_session.add(vendor)
    db_session.flush()
    system = ApplicationComponent(name=f"Eu system {tag}", organization_id=org.id, lifecycle_status="operational",
                                  vendor_organization_id=vendor.id)
    db_session.add(system)
    db_session.flush()
    contract = VendorContract(contract_name=f"Eu deal {tag}", organization_id=org.id, vendor_id=vendor.id,
                              application_id=system.id, annual_cost=amount, currency=currency, status="active",
                              start_date=date.today() - timedelta(days=30),
                              end_date=date.today() + timedelta(days=300))
    db_session.add(contract)
    db_session.flush()
    return vendor, system, contract


def test_a_system_costed_from_an_other_currency_contract_reads_in_that_currency(db_session, make_org, tenant_ctx):
    from app.modules.dashboard.services.overview_figures import _table

    mine, theirs = make_org("vf-ea"), make_org("vf-eb")
    tag = uuid.uuid4().hex[:6]
    _, euro_system, _ = _euro_only_system(db_session, mine, "e" + tag)
    _, pound_system, _ = _euro_only_system(db_session, mine, "p" + tag, amount=6000.0, currency="GBP")
    _euro_only_system(db_session, theirs, "t" + tag, amount=777000.0, currency="USD")
    with tenant_ctx(mine.id):
        table = _table(mine.id)
    rows = {r["id"]: r for r in table["rows"]}
    euro, pound = rows[euro_system.id], rows[pound_system.id]
    assert euro["cost_currency"] == "EUR" and euro["cost"] == 1200.0
    assert re.search(r"\u20ac1,200(?:\.00)? a year, from 1 active contract", euro["cost_label"]), euro["cost_label"]
    assert "\u00a3" not in euro["cost_label"]
    assert pound["cost"] == 6000.0 and pound["cost_currency"] is None and pound["cost_label"] == "\u00a36,000"
    assert table["cost_total"] == 6000.0
    assert "777" not in repr(table)


def test_the_run_cost_of_a_system_reads_the_ledger_and_never_the_contracts_table(db_session, make_org, tenant_ctx):
    import inspect

    from app.services import application_cost_accessor
    from app.services.application_cost_accessor import get_run_cost_with_source

    mine = make_org("vf-ec")
    tag = uuid.uuid4().hex[:6]
    _, euro_system, _ = _euro_only_system(db_session, mine, "e" + tag)
    _, pound_system, _ = _euro_only_system(db_session, mine, "p" + tag, amount=6000.0, currency="GBP")
    with tenant_ctx(mine.id):
        assert get_run_cost_with_source(euro_system) == (None, None)
        assert get_run_cost_with_source(pound_system) == (6000.0, "from 1 active contract")
    assert "VendorContract" not in inspect.getsource(application_cost_accessor.get_run_cost_with_source)


def test_spend_states_the_other_currency_contracts_apart_from_its_totals(app, db_session, make_org):
    mine, theirs = make_org("vf-ed"), make_org("vf-ee")
    tag = uuid.uuid4().hex[:6]
    _gbp_org_with_contracts(db_session, mine, "m" + tag, [(6000.0, "GBP"), (4000.0, "EUR")])
    _gbp_org_with_contracts(db_session, theirs, "t" + tag, [(555000.0, "USD")])
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    text = _answer(client.get("/procurement/spend").get_data(as_text=True))
    assert "\u00a36k" in text and "\u00a310k" not in text
    assert re.search(r"Not added: 1 active contract states \u20ac4,000(?:\.00)? a year in EUR", text), text
    assert "555" not in text


def test_an_unsupported_currency_is_refused_in_plain_words(app, db_session, make_org):
    from app.models.user import User

    mine = make_org("vf-ef")
    client = _admin_client(app, db_session, mine)
    db_session.query(User).filter_by(organization_id=mine.id).update({"enterprise_role": "procurement"})
    db_session.flush()
    _forget_cached_user()
    posted = client.post("/procurement/contracts/new", data={"contract_name": "Odd", "currency": "ZZZ"})
    text = _answer(posted.get_data(as_text=True))
    assert posted.status_code == 400
    assert "Choose a currency from the list." in text
    assert "is not one of" not in text and "currency=" not in text.split("Choose a currency")[1][:80]


def test_the_vendor_page_states_an_other_currency_contract_amount_with_its_reason(app, db_session, make_org):
    mine = make_org("vf-eg")
    tag = uuid.uuid4().hex[:6]
    vendor, _, _ = _euro_only_system(db_session, mine, "e" + tag)
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    text = _answer(client.get(f"/applications/vendors/{vendor.id}").get_data(as_text=True))
    assert re.search(r"\u20ac1,200(?:\.00)? a year", text), text
    assert "Not added: stated in EUR" in text


def test_the_currency_field_uses_the_field_and_select_macros(app, db_session, make_org):
    from app.models.user import User

    mine = make_org("vf-eh")
    client = _admin_client(app, db_session, mine)
    db_session.query(User).filter_by(organization_id=mine.id).update({"enterprise_role": "procurement"})
    db_session.flush()
    _forget_cached_user()
    html = client.get("/procurement/contracts/new").get_data(as_text=True)
    select = re.search(r'<select[^>]*name="currency"[^>]*>', html)
    assert select, "no currency select"
    assert "focus:ring-1" not in select.group(0) and "bg-background px-3" not in select.group(0)
    assert re.search(r'<option[^>]*value="GBP"[^>]*selected', html)


def test_the_contract_overlap_report_adds_only_the_contracts_the_ledger_counts(db_session, make_org, tenant_ctx):
    from app.models.application_capability import ApplicationCapabilityMapping
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.business_capabilities import BusinessCapability
    from app.models.contract_application import ContractApplication
    from app.modules.intelligence.services.query_catalogue import run_entry

    mine, theirs = make_org("vf-fa"), make_org("vf-fb")
    tag = uuid.uuid4().hex[:6]
    capability = BusinessCapability(name=f"Cap {tag}", organization_id=mine.id)
    db_session.add(capability)
    db_session.flush()
    for amount, currency in ((1000.0, "GBP"), (900.0, "EUR")):
        system = ApplicationComponent(name=f"Ov {currency} {tag}", organization_id=mine.id)
        db_session.add(system)
        db_session.flush()
        contract = VendorContract(contract_name=f"Ov {currency} {tag}", organization_id=mine.id,
                                  contract_value=amount, currency=currency, status="active",
                                  start_date=date.today() - timedelta(days=30))
        db_session.add(contract)
        db_session.flush()
        db_session.add(ContractApplication(contract_id=contract.id, application_id=system.id,
                                           organization_id=mine.id))
        db_session.add(ApplicationCapabilityMapping(application_component_id=system.id,
                                                    business_capability_id=capability.id, organization_id=mine.id))
    _gbp_org_with_contracts(db_session, theirs, "t" + tag, [(777000.0, "USD")])
    db_session.flush()
    with tenant_ctx(mine.id):
        result = run_entry("overlapping_contracts", mine.id)
    assert result["groups"][0]["combined_contract_value"] == 1000.0


def test_the_contract_analysis_summary_and_the_portfolio_metrics_read_the_ledger(db_session, make_org, tenant_ctx):
    from app.services.clause_extraction_service import ClauseExtractionService
    from app.services.metrics_service import MetricsService

    mine, theirs = make_org("vf-fc"), make_org("vf-fd")
    tag = uuid.uuid4().hex[:6]
    _, made = _gbp_org_with_contracts(db_session, mine, "m" + tag, [(6000.0, "GBP"), (4000.0, "EUR")])
    _gbp_org_with_contracts(db_session, theirs, "t" + tag, [(555000.0, "USD")])
    for c in made:
        c.contract_value = c.annual_cost * 2
    db_session.flush()
    with tenant_ctx(mine.id):
        summary = ClauseExtractionService._build_summary(ClauseExtractionService.__new__(ClauseExtractionService),
                                                         made, [], [], [])
    assert summary["total_annual_spend"] == 6000.0
    import inspect

    source = inspect.getsource(MetricsService.get_vendor_portfolio_metrics.__wrapped__)
    assert "contract_ledger" in source and "sum(" not in source


def test_vendors_with_no_renewal_ahead_come_after_those_that_have_one(db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.services import vendor_register

    mine, theirs = make_org("vf-order-a"), make_org("vf-order-b")
    tag = uuid.uuid4().hex[:6]
    _estate(db_session, theirs, "t" + tag)
    names = {}
    for key, days in (("zeta", 300), ("alpha", None), ("beta", 200)):
        vendor = VendorOrganization(name=f"{key} {tag}")
        db_session.add(vendor)
        db_session.flush()
        names[key] = vendor.id
        if not days:
            from app.models.application_portfolio import ApplicationComponent
            db_session.add(ApplicationComponent(name=f"{key} sys {tag}", organization_id=mine.id,
                                                lifecycle_status="operational", vendor_organization_id=vendor.id))
        if days:
            db_session.add(VendorContract(contract_name=f"{key} deal {tag}", organization_id=mine.id,
                                          vendor_id=vendor.id, status="active", annual_cost=10.0,
                                          start_date=date.today() - timedelta(days=30),
                                          end_date=date.today() + timedelta(days=days)))
    db_session.flush()
    with tenant_ctx(mine.id):
        order = [r["id"] for r in vendor_register.register(mine.id) if r["id"] in names.values()]

    assert order == [names["beta"], names["zeta"], names["alpha"]]
