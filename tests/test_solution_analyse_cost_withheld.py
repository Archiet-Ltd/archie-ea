"""The analyse reply behind both solution-analyser routes gives money only to a role the one cost rule allows.

A role without the spend permission runs the analysis on both routes — /architecture/solutions-architecture/analyze
and /solutions/architect/api/analyze — and reads one sentence saying cost figures are withheld, and no amount
anywhere in the reply: not in the existing applications, not in estimated savings, not in any recommendation's
estimated cost, description, pros or details. A finance person reads the same amounts. The analyse set is never
empty: two applications per organisation match the problem, so the withheld assertions sit on a real result.
Two real organisations throughout: the other one's applications cost 777,001 and 777,002.
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest import mock

import pytest

from tests.test_vendor_pages import _user

pytestmark = pytest.mark.usefixtures("db_session")

HELD = "Cost figures are withheld for your role."
ANALYZE_PATHS = (
    "/architecture/solutions-architecture/analyze",
    "/solutions/architect/api/analyze",
)
FAKE_LLM = {"options": [], "recommendation": "BUILD", "summary": "stand-in"}


def _seed(db_session, org, tag, tcos):
    """Applications whose description matches the problem, each with its own annual cost."""
    from app.models.application_portfolio import ApplicationComponent

    apps = []
    for i, tco in enumerate(tcos):
        row = ApplicationComponent(
            name=f"Legacy ledger {i} {tag}", organization_id=org.id,
            description="Replace the legacy ledger with a modern one",
            lifecycle_status="active", total_cost_of_ownership=tco,
        )
        db_session.add(row)
        apps.append(row)
    db_session.flush()
    return apps


def _forms(*amounts):
    return [form for amount in amounts for form in ("{:,.0f}".format(amount), str(int(amount)))]


def _analyze(client, path):
    from app.modules.architecture.services.options_analysis_service import OptionsAnalysisService

    with mock.patch.object(OptionsAnalysisService, "analyze_requirement_options", return_value=FAKE_LLM):
        return client.post(path, json={"problem_description": "Replace the legacy ledger"})


def _analysis_body(body, path):
    """The analysis mapping of either route's reply."""
    return body["analysis"] if path == "/solutions/architect/api/analyze" else body


def test_a_withheld_role_reads_no_amount_in_either_analyse_reply(client, login_as, db_session, make_org):
    mine, theirs = make_org("sa-a"), make_org("sa-b")
    tag = uuid.uuid4().hex[:6]
    _seed(db_session, mine, tag, (300001, 300002))
    _seed(db_session, theirs, "t" + tag, (777001, 777002))
    login_as(client, _user(db_session, mine, "compliance"))
    amounts = _forms(300001, 300002, 777001, 777002, 180000.9, 504000, 100800, 100000)

    for path in ANALYZE_PATHS:
        response = _analyze(client, path)
        assert response.status_code == 200, (path, response.status_code)
        body = response.get_json()
        analysis = _analysis_body(body, path)
        text = json.dumps(body, ensure_ascii=False)

        # The analyse set is not empty, so the withholding below is proven on a real result.
        existing = analysis.get("existing_applications") or {}
        assert existing.get("found_count", 0) >= 2, (path, existing)
        assert len(analysis.get("recommendations", [])) >= 1, path

        # One sentence, and no amount anywhere in the reply.
        assert HELD in text, path
        assert [form for form in amounts if form in text] == [], path
        assert existing.get("estimated_savings") is None, (path, existing)
        for app in existing.get("applications", []):
            assert app.get("annual_cost") is None, (path, app)
        for rec in analysis.get("recommendations", []):
            assert rec.get("estimated_cost") is None, (path, rec)
            assert "£" not in (rec.get("description") or ""), (path, rec)
            for bullet in (rec.get("pros") or []) + (rec.get("cons") or []):
                assert "£" not in bullet, (path, bullet)
            details = rec.get("details") or {}
            assert details.get("maintenance_cost_annual") is None, (path, details)
            for app in details.get("applications", []) or []:
                assert app.get("annual_cost") is None, (path, app)
            for vendor in details.get("top_vendors", []) or []:
                assert vendor.get("estimated_cost") is None, (path, vendor)
        for vendor in (analysis.get("vendor_options") or {}).get("top_vendors", []) or []:
            assert vendor.get("estimated_cost") is None, (path, vendor)


def test_a_finance_person_reads_the_amounts_on_both_routes(client, login_as, db_session, make_org):
    mine, theirs = make_org("sa-c"), make_org("sa-d")
    tag = uuid.uuid4().hex[:6]
    _seed(db_session, mine, tag, (300011, 300012))
    _seed(db_session, theirs, "t" + tag, (777011, 777012))
    login_as(client, _user(db_session, mine, "finance"))

    for path in ANALYZE_PATHS:
        response = _analyze(client, path)
        assert response.status_code == 200, (path, response.status_code)
        body = response.get_json()
        analysis = _analysis_body(body, path)
        text = json.dumps(body, ensure_ascii=False)

        existing = analysis.get("existing_applications") or {}
        assert existing.get("found_count", 0) >= 2, (path, existing)
        assert HELD not in text, path
        # The person's own applications carry their annual cost; the other organisation's do not appear.
        assert [app.get("annual_cost") for app in existing.get("applications", [])] == [300011.0, 300012.0], path
        assert existing.get("estimated_savings") == round((300011 + 300012) * 0.3, 2), path
        assert "300,011" in text or "300011" in text, path
        assert [form for form in _forms(777011, 777012) if form in text] == [], path


def test_the_orchestrator_reads_annual_cost_through_the_one_accessor(client, login_as, db_session, make_org,
                                                                     monkeypatch):
    """The analyse reply's annual cost comes from application_cost_accessor.get_annual_cost, nothing else."""
    from app.services import application_cost_accessor

    mine = make_org("sa-e")
    tag = uuid.uuid4().hex[:6]
    _seed(db_session, mine, tag, (300021,))
    login_as(client, _user(db_session, mine, "finance"))
    monkeypatch.setattr(application_cost_accessor, "get_annual_cost", lambda app: Decimal("424242"))

    response = _analyze(client, "/architecture/solutions-architecture/analyze")
    assert response.status_code == 200
    existing = response.get_json().get("existing_applications") or {}
    assert [app.get("annual_cost") for app in existing.get("applications", [])] == [424242.0]
