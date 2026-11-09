"""Every contract state gives one renewal answer, read from one rule in the vendor register.

A contract that is not active has no upcoming renewal and no urgency. An active contract past its end date
reads "Ended N days ago". An active contract past its renewal date reads "Renewal overdue by N days". Anything
else has its next date and the days to it. The assistant and the contract analysis count the same way.
"""
from __future__ import annotations

import inspect
import re
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from tests.test_vendor_pages_agree import _admin_client, _answer, _forget_cached_user

TODAY = date(2026, 11, 9)


def _on(days):
    return TODAY + timedelta(days=days) if days is not None else None


def _c(status, renews, ends):
    return SimpleNamespace(status=status, renewal_date=_on(renews), end_date=_on(ends))


# status, renewal offset, end offset -> kind, date offset, days, text, urgency, due within 90 days
STATES = [
    ("terminated", 12, 200, "inactive", None, None, None, None, False),
    ("expired", -5, -2, "inactive", None, None, None, None, False),
    ("draft", 20, 90, "inactive", None, None, None, None, False),
    ("active", -30, -5, "ended", -5, -5, "Ended 5 days ago", "expired", False),
    ("active", None, -1, "ended", -1, -1, "Ended 1 day ago", "expired", False),
    ("active", -3, 160, "overdue", -3, -3, "Renewal overdue by 3 days", "expired", True),
    ("active", -1, None, "overdue", -1, -1, "Renewal overdue by 1 day", "expired", True),
    ("active", 10, 200, "ahead", 10, 10, "In 10 days", "critical", True),
    ("active", 0, 200, "ahead", 0, 0, "Renews today", "critical", True),
    ("active", None, 100, "ahead", 100, 100, "In 100 days", "upcoming", False),
    ("active", 1, None, "ahead", 1, 1, "In 1 day", "critical", True),
    ("active", None, None, "none", None, None, None, "unknown", False),
]


@pytest.mark.parametrize("status,renews,ends,kind,on,days,text,urgency,due", STATES)
def test_each_contract_state_gives_one_answer(status, renews, ends, kind, on, days, text, urgency, due):
    from app.services import vendor_register as vr

    contract = _c(status, renews, ends)
    state = vr.renewal_state(contract, TODAY)
    assert (state.kind, state.on, state.days, state.text) == (kind, _on(on), days, text)
    assert vr.renewal_date_of(contract, TODAY) == _on(on)
    assert vr.get_days_until_renewal(contract, TODAY) == days
    assert vr.get_renewal_urgency(contract, TODAY) == urgency
    assert vr.renewal_is_overdue(contract, TODAY) is (kind == "overdue")
    assert vr.contract_renewal_due(contract, TODAY) is due
    ahead = vr.next_renewal([contract], TODAY)
    assert ahead == (_on(on) if kind in ("overdue", "ahead") else None)


def test_next_renewal_keeps_an_overdue_date_and_lists_it_first():
    from app.services import vendor_register as vr

    overdue, soon, ended, terminated = _c("active", -8, 100), _c("active", 5, 90), _c("active", -9, -2), _c("terminated", 3, 50)
    assert vr.next_renewal([soon, overdue, ended, terminated], TODAY) == _on(-8)
    assert vr.next_renewal([soon, ended, terminated], TODAY) == _on(5)
    assert vr.next_renewal([ended, terminated], TODAY) is None


def _estate(db_session, org, tag):
    """One vendor, with a system, per state: ahead (12 days), overdue (9 days ago), ended (4 days ago),
    terminated (renewal in 12 days)."""
    from app.models.application_portfolio import ApplicationComponent, VendorContract
    from app.models.vendor.vendor_organization import VendorOrganization

    today = date.today()
    made = {}
    for key, status, renews, ends in (
            ("ahead", "active", 12, 200), ("overdue", "active", -9, 200),
            ("ended", "active", -30, -4), ("terminated", "terminated", 12, 200)):
        vendor = VendorOrganization(name=f"V-{key} {tag}")
        db_session.add(vendor)
        db_session.flush()
        system = ApplicationComponent(name=f"S-{key} {tag}", organization_id=org.id,
                                      lifecycle_status="operational", vendor_organization_id=vendor.id)
        db_session.add(system)
        db_session.flush()
        contract = VendorContract(
            contract_name=f"C-{key} {tag}", organization_id=org.id, vendor_id=vendor.id, application_id=system.id,
            annual_cost=1000.0, status=status, start_date=today - timedelta(days=300),
            renewal_date=today + timedelta(days=renews), end_date=today + timedelta(days=ends))
        db_session.add(contract)
        made[key] = (vendor, contract)
    db_session.flush()
    return made


def test_the_renewals_page_vendors_page_register_contract_assistant_and_analysis_agree(
        app, db_session, make_org, tenant_ctx):
    from flask import g

    from app.modules.ai_chat.services.architect_persona_charters import _procurement_context
    from app.services.clause_extraction_service import ClauseExtractionService

    mine, theirs = make_org("rs-a"), make_org("rs-b")
    tag = uuid.uuid4().hex[:6]
    estate = _estate(db_session, mine, "m" + tag)
    _estate(db_session, theirs, "t" + tag)
    client = _admin_client(app, db_session, mine)
    _forget_cached_user()

    renewals = _answer(client.get("/procurement/renewals?days=90").get_data(as_text=True))
    assert re.search(r"\b2 Renew within 90 days", renewals), renewals
    assert f"C-overdue m{tag}" in renewals and f"C-ahead m{tag}" in renewals
    assert f"C-ended m{tag}" not in renewals and f"C-terminated m{tag}" not in renewals
    assert re.search(r"C-overdue m%s.*?Renewal overdue by 9 days.*?C-ahead m%s" % (tag, tag), renewals, re.S)
    assert "t" + tag not in renewals

    vendors = _answer(client.get("/applications/vendors").get_data(as_text=True))
    assert "2 renew within 90 days" in vendors, vendors
    def row_of(key):
        return re.search(r"V-%s m%s Evidence.*?Actions for V-%s m%s" % (key, tag, key, tag), vendors, re.S).group(0)

    overdue_row = row_of("overdue")
    assert "Renewal overdue by 9 days" in overdue_row
    assert "Due within 90 days" in row_of("ahead")
    for key in ("ended", "terminated"):
        assert "No renewal ahead" in row_of(key) and "overdue" not in row_of(key)

    register = _answer(client.get("/procurement/contracts").get_data(as_text=True))
    assert re.search(r"C-overdue m%s.*?Renewal overdue by 9 days" % tag, register, re.S)
    assert re.search(r"C-ended m%s.*?Ended 4 days ago" % tag, register, re.S)
    assert re.search(r"C-terminated m%s.*?Not active, no renewal" % tag, register, re.S)
    assert "In 12 days" in register and register.count("In 12 days") == 1

    for key, wanted, unwanted in (("overdue", "Renewal overdue by 9 days", "Ended"),
                                  ("ended", "Ended 4 days ago", "until renewal"),
                                  ("terminated", "No renewal: the contract is terminated", "until renewal"),
                                  ("ahead", "12 days until renewal", "overdue")):
        page = _answer(client.get("/procurement/contracts/%d" % estate[key][1].id).get_data(as_text=True))
        assert wanted in page, (key, page)
        assert unwanted not in page, (key, page)

    with app.test_request_context("/"):
        g.current_org_id = mine.id
        text = _procurement_context()
    assert "Renewals within 90 days (2)" in text
    assert f"C-overdue m{tag}" in text and f"C-ahead m{tag}" in text
    assert "C-ended" not in text and "C-terminated" not in text

    with tenant_ctx(mine.id):
        contracts = [c for _, c in estate.values()]
        summary = ClauseExtractionService._build_summary(
            ClauseExtractionService.__new__(ClauseExtractionService), contracts, [], [], [])
    assert summary["upcoming_renewals"] == 2


def test_the_assistant_and_the_analysis_count_do_not_read_contract_dates_themselves():
    from app.modules.ai_chat.services import architect_persona_charters as charters
    from app.services.clause_extraction_service import ClauseExtractionService

    for source in (inspect.getsource(charters._procurement_context),
                   inspect.getsource(ClauseExtractionService._build_summary)):
        assert not re.search(r"\.(renewal_date|end_date)\b", source), source


def test_a_contract_that_is_not_active_has_no_urgency_on_the_analysis_timeline(db_session, make_org):
    from app.services.clause_extraction_service import ClauseExtractionService

    mine = make_org("rs-c")
    tag = uuid.uuid4().hex[:6]
    live = _estate(db_session, mine, "m" + tag)
    service = ClauseExtractionService()
    ended = {e["event_type"]: e for e in service._build_obligations_timeline([live["terminated"][1]])}
    assert ended["renewal"]["urgency"] is None and ended["renewal"]["days_remaining"] is None
    assert ended["end"]["urgency"] is None
    running = {e["event_type"]: e for e in service._build_obligations_timeline([live["ahead"][1]])}
    assert running["renewal"]["days_remaining"] == 12 and running["renewal"]["urgency"] == "critical"
