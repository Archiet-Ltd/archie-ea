"""Vendor analysis replies give money only to a role the one cost rule allows.

A solution architect without spend authority opens a vendor analysis on every route that serves it — the
dashboard detail, results, comparison, provenance and export, and the module's results, comparison, export and
provenance — and reads the vendors, their scores and one sentence saying cost figures are withheld, and no
cost of ownership, licence or support amount. A finance person reads the same replies with the amounts.
Two real organisations throughout: the other one's vendor carries its own amounts.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tests.test_vendor_pages import _user

pytestmark = pytest.mark.usefixtures("db_session")

HELD = "Cost figures are withheld for your role."
DASHBOARD = ("/dashboard/api/vendor-analysis/{id}", "/dashboard/api/vendor-analysis/{id}/results",
             "/dashboard/api/vendor-analysis/{id}/comparison", "/dashboard/api/vendor-analysis/{id}/provenance")
MODULE = ("/vendor-analysis/{id}/results", "/vendor-analysis/{id}/comparison")
MINE = (484001, 48402, 48403)
THEIRS = (585001, 58502, 58503)


def _seed(db_session, org, amounts, owner=None):
    owner = owner or _user(db_session, org, "solution_architect")
    from app.models.business_capabilities import BusinessCapability
    from app.models.vendor_analysis import OptionsAnalysis, VendorOption

    tag = uuid.uuid4().hex[:6]
    cap = BusinessCapability(name=f"Ledger cap {tag}", organization_id=org.id)
    db_session.add(cap)
    db_session.flush()
    analysis = OptionsAnalysis(name=f"Ledger choice {tag}", capability_id=cap.id, created_by_id=owner.id,
                              organization_id=org.id)
    db_session.add(analysis)
    db_session.flush()
    option = VendorOption(analysis_id=analysis.id, vendor_name=f"Ledgerco {tag}", organization_id=org.id,
                          tco_total=amounts[0], license_cost_annual=amounts[1], support_cost_annual=amounts[2],
                          total_score=71, analysis_status="completed")
    db_session.add(option)
    db_session.flush()
    return analysis


def _forms(*amounts):
    return [form for amount in amounts for form in ("{:,.0f}".format(amount), str(int(amount)))]


def _read(client, analysis, patterns):
    replies = []
    for pattern in patterns:
        path = pattern.format(id=analysis.id)
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code)
        replies.append((path, response.get_data(as_text=True)))
    return replies


def test_a_withheld_role_reads_no_vendor_amount_on_any_analysis_route(client, login_as, db_session, make_org):
    mine, theirs = make_org("va-a"), make_org("va-b")
    person = _user(db_session, mine, "solution_architect")
    analysis = _seed(db_session, mine, MINE, person)
    _seed(db_session, theirs, THEIRS)
    login_as(client, person)

    for path, text in _read(client, analysis, DASHBOARD + MODULE):
        # The vendor is there with its score, so the missing amounts are withholding, not an empty reply.
        assert "71" in text, path
        assert [form for form in _forms(*MINE, *THEIRS) if form in text] == [], path
        assert text.count(HELD) == 1, path


def test_a_finance_person_reads_the_vendor_amounts_unchanged(client, login_as, db_session, make_org):
    mine, theirs = make_org("va-c"), make_org("va-d")
    person = _user(db_session, mine, "finance")
    analysis = _seed(db_session, mine, MINE, person)
    _seed(db_session, theirs, THEIRS)
    login_as(client, person)

    for path, text in _read(client, analysis, MODULE):
        assert HELD not in text, path
        assert "484001" in text or "484,001" in text, path
        assert [form for form in _forms(*THEIRS) if form in text] == [], path


def test_the_dashboard_csv_export_leaves_the_money_columns_out_for_a_withheld_role(client, login_as, db_session,
                                                                                  make_org):
    mine = make_org("va-e")
    person = _user(db_session, mine, "solution_architect")
    analysis = _seed(db_session, mine, MINE, person)
    path = f"/dashboard/api/vendor-analysis/{analysis.id}/export?format=csv"

    login_as(client, person)
    text = client.get(path).get_data(as_text=True)
    assert "Ledgerco" in text and "Rank" in text
    assert "TCO" not in text and "License" not in text and "484001" not in text

    finance = _user(db_session, mine, "finance")
    analysis.created_by_id = finance.id
    db_session.flush()
    login_as(client, finance)
    response = client.get(path)
    assert response.status_code == 200, response.status_code
    assert "484001" in response.get_data(as_text=True)
