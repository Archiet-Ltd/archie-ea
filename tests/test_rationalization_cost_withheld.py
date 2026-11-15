"""The rationalisation export, evidence trail and run-detection give money only to a role the cost rule allows.

A solution architect without spend authority downloads the business case as CSV, Excel and PDF, opens an
application's evidence trail and runs detection, and reads every score, action and group with no amount: no
savings column, no annual cost, no figure written into a rationale. One sentence says cost figures are withheld.
A finance person reads the same replies and downloads with the amounts. Two real organisations throughout.
"""
from __future__ import annotations

import io
import json
import uuid

import pytest

from tests.test_vendor_pages import _user

pytestmark = pytest.mark.usefixtures("db_session")

HELD = "Cost figures are withheld for your role."
MINE_COST, MINE_SAVINGS = 268305, 136274
THEIRS_COST, THEIRS_SAVINGS = 929301, 929302


def _seed(db_session, org, cost, savings, tag):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.application_rationalization import ApplicationRationalizationScore

    apps = []
    for i, stem in enumerate(("Ledgerline", "Ledgerline Books")):
        row = ApplicationComponent(name=f"{stem} {tag}", organization_id=org.id, lifecycle_status="operational",
                                   description="Holds the ledger.", total_cost_of_ownership=cost + i, user_count=40)
        db_session.add(row)
        apps.append(row)
    db_session.flush()
    for row in apps:
        db_session.add(ApplicationRationalizationScore(
            application_component_id=row.id, organization_id=org.id, disposition_action="retain",
            rationalization_action="ELIMINATE", overall_health_score=72, priority="medium",
            technical_health_score=70, business_value_score=64, cost_efficiency_score=58, vendor_risk_score=66,
            action_rationale="Keeps the ledger running.", estimated_annual_savings=savings,
            estimated_investment_needed=savings, total_cost_of_ownership=cost))
    db_session.flush()
    return apps


def _forms(*amounts):
    return [form for amount in amounts for form in ("{:,.0f}".format(amount), str(int(amount)))]


def _download(client, fmt):
    response = client.post("/applications/rationalization/api/export", json={"format": fmt})
    assert response.status_code == 200, (fmt, response.status_code)
    return response.get_data()


def _excel_text(data):
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(data))
    return "\n".join(str(cell.value) for sheet in book for row in sheet.iter_rows() for cell in row
                     if cell.value is not None)


def test_a_withheld_role_downloads_the_business_case_without_an_amount(client, login_as, db_session, make_org):
    mine, theirs = make_org("ra-a"), make_org("ra-b")
    tag = uuid.uuid4().hex[:6]
    _seed(db_session, mine, MINE_COST, MINE_SAVINGS, tag)
    _seed(db_session, theirs, THEIRS_COST, THEIRS_SAVINGS, "t" + tag)
    login_as(client, _user(db_session, mine, "solution_architect"))
    amounts = _forms(MINE_SAVINGS, THEIRS_SAVINGS, MINE_COST, THEIRS_COST, MINE_SAVINGS * 2)

    csv_text = _download(client, "csv").decode("utf-8")
    excel_text = _excel_text(_download(client, "excel"))
    pdf_text = _download(client, "pdf").decode("utf-8")
    for fmt, text in (("csv", csv_text), ("excel", excel_text), ("pdf", pdf_text)):
        # The applications are there with their scores, so the missing amounts are withholding.
        assert f"Ledgerline {tag}" in text and "72" in text, fmt
        assert [form for form in amounts if form in text] == [], fmt
        assert "Savings" not in text.replace("Cost figures", ""), fmt
    assert HELD in excel_text and HELD in pdf_text


def test_a_finance_person_downloads_the_business_case_with_the_amounts(client, login_as, db_session, make_org):
    mine, theirs = make_org("ra-c"), make_org("ra-d")
    tag = uuid.uuid4().hex[:6]
    _seed(db_session, mine, MINE_COST, MINE_SAVINGS, tag)
    _seed(db_session, theirs, THEIRS_COST, THEIRS_SAVINGS, "t" + tag)
    login_as(client, _user(db_session, mine, "finance"))

    csv_text = _download(client, "csv").decode("utf-8")
    assert "Estimated Annual Savings" in csv_text and str(MINE_SAVINGS) in csv_text
    assert _forms(THEIRS_SAVINGS)[1] not in csv_text
    assert str(MINE_SAVINGS) in _excel_text(_download(client, "excel"))
    assert f"{MINE_SAVINGS * 2:,.0f}" in _download(client, "pdf").decode("utf-8")


def test_the_evidence_trail_carries_no_amount_for_a_withheld_role(client, login_as, db_session, make_org):
    mine = make_org("ra-e")
    apps = _seed(db_session, mine, MINE_COST, MINE_SAVINGS, uuid.uuid4().hex[:6])
    path = f"/applications/rationalization/api/evidence-trail/{apps[0].id}"

    login_as(client, _user(db_session, mine, "finance"))
    body = client.get(path).get_json()
    text = json.dumps(body, ensure_ascii=False)
    assert body.get("success") is True, body
    assert "Annual Cost (TCO)" in text and str(MINE_COST) in text

    login_as(client, _user(db_session, mine, "solution_architect"))
    body = client.get(path).get_json()
    text = json.dumps(body, ensure_ascii=False)
    assert body.get("success") is True and "Annual Cost (TCO)" in text
    assert [form for form in _forms(MINE_COST) if form in text] == []
    assert "£" not in text
    assert text.count(HELD) == 1


def test_run_detection_carries_no_savings_for_a_withheld_role(client, login_as, db_session, make_org):
    mine = make_org("ra-f")
    _seed(db_session, mine, MINE_COST, MINE_SAVINGS, uuid.uuid4().hex[:6])
    path = "/applications/rationalization/api/run-detection"

    login_as(client, _user(db_session, mine, "finance"))
    allowed = client.post(path, json={"strategy": "fast", "similarity_threshold": 0.3}).get_json()
    assert allowed.get("success") is True and allowed.get("groups_found", 0) >= 1, allowed
    assert "estimated_savings" in json.dumps(allowed)

    login_as(client, _user(db_session, mine, "solution_architect"))
    held = client.post(path, json={"strategy": "fast", "similarity_threshold": 0.3}).get_json()
    assert held.get("success") is True and held.get("groups_found", 0) >= 1, held
    assert "estimated_savings" not in json.dumps(held) or held.get("estimated_savings") is None
    assert held.get("costs_withheld_sentence") == HELD
