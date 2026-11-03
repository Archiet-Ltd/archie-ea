"""One definition of an active contract, and every contract amount in its own currency.

A contract is active when its status is active and it has no end date or one not yet past. The contract ledger
holds that definition and every screen that counts, lists or prices active contracts asks it. The Overview's
monthly figure is what the active contracts in the organisation's currency cost a year, over twelve, including
the contracts the demonstration company records on systems that also carry their own cost. Each contract row
prints its amount under its own currency's symbol. Editing a contract belongs to procurement: an organisation
Administrator is neither offered the edit page nor admitted to it.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from tests.test_vendor_figures_agree import _gbp_org_with_contracts
from tests.test_vendor_pages_agree import _admin_client, _answer, _forget_cached_user

pytestmark = pytest.mark.usefixtures("db_session")

ROOT = Path(__file__).resolve().parents[1]


def _client(app, db_session, org, role):
    from app.models.user import User
    from tests._session_test_helpers import mint_test_sid

    user = User(email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", first_name="Pro", last_name="Cure",
                organization_id=org.id, confirmed=True, enterprise_role=role)
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        sid = mint_test_sid(user.id)
        if sid:
            sess["_sid"] = sid
    return client


def _contracts(db_session, org, tag):
    """Open-ended, running, ended-but-marked-active and terminated contracts, all in pounds."""
    from app.models.application_portfolio import VendorContract

    today = date.today()
    made = {}
    for name, status, end in (("open", "active", None), ("running", "active", today + timedelta(days=30)),
                              ("today", "active", today), ("ended", "active", today - timedelta(days=1)),
                              ("terminated", "terminated", None)):
        made[name] = VendorContract(contract_name=f"{name} {tag}", organization_id=org.id, annual_cost=1200.0,
                                    currency="GBP", status=status, start_date=today - timedelta(days=90),
                                    end_date=end)
        db_session.add(made[name])
    db_session.flush()
    return made


def _demo_shaped(db_session, org, tag):
    """The demonstration company's shape: each vendor-supplied system records its own cost and carries one
    active contract with no end date stating that cost (the seed's own call), plus a euro contract with no end
    date that sits on no system."""
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.services.application_cost_accessor import state_contract

    for name, cost in (("Ledger", 36000.0), ("Payroll", 18000.0)):
        vendor = VendorOrganization(name=f"{name} vendor {tag}")
        db_session.add(vendor)
        db_session.flush()
        system = ApplicationComponent(name=f"{name} {tag}", organization_id=org.id, lifecycle_status="operational",
                                      total_cost_of_ownership=cost, vendor_organization_id=vendor.id)
        db_session.add(system)
        db_session.flush()
        assert state_contract(system, vendor.name, cost).end_date is None
    oracle = VendorOrganization(name=f"Oracle {tag}")
    db_session.add(oracle)
    db_session.flush()
    euro = VendorContract(contract_name=f"Oracle database {tag}", organization_id=org.id, vendor_id=oracle.id,
                          annual_cost=24000.0, currency="EUR", status="active",
                          start_date=date.today() - timedelta(days=60))
    db_session.add(euro)
    db_session.flush()
    return euro


def test_a_contract_with_no_end_date_is_active_and_one_that_ended_is_not(db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import VendorContract
    from app.services import vendor_register

    mine, theirs = make_org("ac-a"), make_org("ac-b")
    tag = uuid.uuid4().hex[:6]
    made = _contracts(db_session, mine, "m" + tag)
    _contracts(db_session, theirs, "t" + tag)
    assert {name: vendor_register.is_active(c) for name, c in made.items()} == {
        "open": True, "running": True, "today": True, "ended": False, "terminated": False}
    with tenant_ctx(mine.id):
        queried = db_session.execute(
            db_session.query(VendorContract.contract_name)
            .filter(VendorContract.organization_id == mine.id, vendor_register.active_condition())
            .statement).scalars().all()
        ledger = vendor_register.contract_ledger(mine.id)
    assert sorted(queried) == sorted(f"{n} m{tag}" for n in ("open", "running", "today"))
    assert sorted(c.contract_name for c in ledger["counted"]) == sorted(queried)


def test_the_overview_monthly_figure_counts_the_demonstration_company_contracts(db_session, make_org, tenant_ctx):
    from app.modules.dashboard.services.overview_figures import _name_the_basis, _run_cost

    mine, theirs = make_org("ac-c"), make_org("ac-d")
    tag = uuid.uuid4().hex[:6]
    _demo_shaped(db_session, mine, "m" + tag)
    _gbp_org_with_contracts(db_session, theirs, "t" + tag, [(777000.0, "GBP")])
    with tenant_ctx(mine.id):
        run_cost = _run_cost(mine.id, datetime.utcnow())
    _name_the_basis(run_cost, None)
    # every active pound contract, not £0 because each sits on a system that records its own cost
    assert run_cost["this_month"] == round((36000.0 + 18000.0) / 12)
    assert run_cost["total"] == 54000.0
    assert run_cost["basis"] == "from the costs recorded on systems and 2 active contracts"
    # the euro contract is stated apart, under its own symbol, so the card names all three active contracts
    assert run_cost["difference"] == "1 active contract in EUR is not added: €24,000 a year."


def test_the_overview_names_every_active_contract_it_does_not_count(db_session, make_org, tenant_ctx):
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.modules.dashboard.services.overview_figures import _name_the_basis, _run_cost

    mine = make_org("ac-e")
    tag = uuid.uuid4().hex[:6]
    vendor, _ = _gbp_org_with_contracts(db_session, mine, tag, [(6000.0, "GBP"), (None, "GBP"), (500.0, "USD")])
    retired = ApplicationComponent(name=f"Retired {tag}", organization_id=mine.id,
                                   lifecycle_status="5. Decommissioned", vendor_organization_id=vendor.id)
    db_session.add(retired)
    db_session.flush()
    db_session.add(VendorContract(contract_name=f"On retired {tag}", organization_id=mine.id, vendor_id=vendor.id,
                                  application_id=retired.id, annual_cost=900.0, currency="GBP", status="active",
                                  start_date=date.today() - timedelta(days=30)))
    db_session.flush()
    with tenant_ctx(mine.id):
        run_cost = _run_cost(mine.id, datetime.utcnow())
    _name_the_basis(run_cost, None)
    assert run_cost["basis"] == "from 1 active contract"
    assert run_cost["difference"] == (
        "1 more active contract states no annual cost. 1 active contract in USD is not added: $500 a year. "
        "1 active contract sits on a decommissioned or removed system and is not counted.")


def test_the_contracts_list_active_filter_follows_the_one_definition(app, db_session, make_org):
    mine, theirs = make_org("ac-f"), make_org("ac-g")
    tag = uuid.uuid4().hex[:6]
    _contracts(db_session, mine, "m" + tag)
    _contracts(db_session, theirs, "t" + tag)
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()
    resp = client.get("/procurement/contracts?status=active")
    assert resp.status_code == 200
    listed = set(re.findall(r'data-testid="contract-link">([^<]+)<', resp.get_data(as_text=True)))
    assert listed == {f"open m{tag}", f"running m{tag}", f"today m{tag}"}


def test_every_contract_row_prints_its_amount_in_its_own_currency(app, db_session, make_org):
    mine, theirs = make_org("ac-h"), make_org("ac-i")
    tag = uuid.uuid4().hex[:6]
    _, made = _gbp_org_with_contracts(db_session, mine, "m" + tag, [(24000.0, "EUR"), (6000.0, "GBP")])
    made[0].renewal_date = made[1].renewal_date = date.today() + timedelta(days=10)
    db_session.flush()
    _gbp_org_with_contracts(db_session, theirs, "t" + tag, [(777.0, "USD")])
    client = _admin_client(app, db_session, mine)
    for path in ("/procurement/contracts", "/procurement/renewals"):
        _forget_cached_user()
        html = client.get(path).get_data(as_text=True)
        rows = {m.group(1): _answer(m.group(2)) for m in re.finditer(
            r'>(Cur m%s \d)</a>(.*?)</tr>' % tag, html, re.S)}
        assert "€24,000.00" in rows[f"Cur m{tag} 0"] and "£" not in rows[f"Cur m{tag} 0"], (path, rows)
        assert "£6,000.00" in rows[f"Cur m{tag} 1"], (path, rows)
        assert "777" not in html


def test_the_contract_analysis_and_the_assistant_name_the_organisation_currency_not_dollars(
        app, db_session, make_org):
    from flask import g

    from app.models.application_portfolio import VendorContract
    from app.modules.ai_chat.tools.executor import ToolExecutor
    from app.services.clause_extraction_service import ClauseExtractionService

    mine = make_org("ac-j")
    tag = uuid.uuid4().hex[:6]
    unstated = VendorContract(contract_name=f"Unstated {tag}", organization_id=mine.id, annual_cost=1200.0,
                              status="active", start_date=date.today())
    db_session.add(unstated)
    db_session.flush()
    money = [e["value"] for e in ClauseExtractionService()._extract_entities(unstated)
             if e["type"] == "MONEY"]
    assert money == ["GBP 1,200.00"]

    with app.test_request_context("/"):
        g.current_org_id = mine.id
        executor = ToolExecutor.__new__(ToolExecutor)
        executor.user_id = None
        executor._get_organization_id = lambda: mine.id
        answer = executor._tool_create_contract({"name": f"Assistant deal {tag}", "annual_cost": 900})
    assert answer["success"], answer
    assert answer["result"]["currency"] == "GBP" and answer["result"]["annual_cost"] == 900.0


def test_the_renewal_summary_counts_an_ended_contract_as_expired_and_not_as_active(db_session, make_org):
    from app.modules.procurement.services import get_renewal_summary

    mine, theirs = make_org("ac-k"), make_org("ac-l")
    tag = uuid.uuid4().hex[:6]
    _contracts(db_session, mine, "m" + tag)
    _contracts(db_session, theirs, "t" + tag)
    summary = get_renewal_summary(mine.id)
    assert summary["total"] == 3
    assert summary["expired"] == 1
    assert summary["unknown"] == 1 and summary["critical"] == 1


def test_an_organisation_administrator_is_not_offered_the_contract_edit_page_procurement_is(
        app, db_session, make_org):
    mine = make_org("ac-m")
    tag = uuid.uuid4().hex[:6]
    _, made = _gbp_org_with_contracts(db_session, mine, tag, [(6000.0, "GBP")])
    admin = _admin_client(app, db_session, mine)
    for path in ("/procurement/contracts", f"/procurement/contracts/{made[0].id}"):
        _forget_cached_user()
        resp = admin.get(path)
        assert resp.status_code == 200, path
        assert f"/procurement/contracts/{made[0].id}/edit" not in resp.get_data(as_text=True), path
    _forget_cached_user()
    assert admin.get(f"/procurement/contracts/{made[0].id}/edit").status_code == 403
    buyer = _client(app, db_session, mine, "procurement")
    _forget_cached_user()
    listing = buyer.get("/procurement/contracts").get_data(as_text=True)
    assert f"/procurement/contracts/{made[0].id}/edit" in listing
    _forget_cached_user()
    form = buyer.get(f"/procurement/contracts/{made[0].id}/edit")
    assert form.status_code == 200 and f"Cur {tag} 0" in form.get_data(as_text=True)


ACTIVE_BY_STATUS = re.compile(
    r"""(VendorContract\.status\s*[!=]=\s*["']active["']|\b(?:c|vc|contract|vc_status)\b(?:\.status)?\s*"""
    r"""(?:[!=]=|not in|in)\s*(?:\(|\[)?\s*["']active["'])""")


def test_every_place_that_decides_a_contract_is_active_asks_the_ledger():
    """The ledger's is_active / active_condition are the one definition (and the ledger names why a contract is
    not counted). The renewal summary reads the status alone once, to find the contracts still marked active
    whose end date has passed."""
    allowed = {"app/services/vendor_register.py": 3, "app/modules/procurement/services.py": 1}
    found = {}
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "VendorContract" not in text and "vendor_contracts" not in text:
            continue
        hits = ACTIVE_BY_STATUS.findall(text)
        if hits:
            found[str(path.relative_to(ROOT))] = len(hits)
    assert found == allowed, found
