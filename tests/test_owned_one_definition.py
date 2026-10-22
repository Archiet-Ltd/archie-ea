"""One definition of "owned": the dashboard's owner coverage and the Ask
question "applications with no owner" give the same answer (PR 713).

An application is owned when it has an accountable owner: a primary or business
ownership record, or a non-empty legacy application/business owner text. A
technical owner on its own does not make it owned.
"""
from __future__ import annotations

import json
import re

import pytest
from bs4 import BeautifulSoup

from tests.test_application_owner_one_store import _person
from tests.test_application_owner_writer import _make_app

pytestmark = pytest.mark.usefixtures("db_session")


def _four_applications(db_session, org):
    from app.services.element_ownership_service import set_element_owner

    primary = _person(db_session, org, "primaryowner")
    technical = _person(db_session, org, "technicalowner")
    with_record = _make_app(db_session, org, "WithPrimary")
    with_text = _make_app(db_session, org, "WithLegacyBusinessText", legacy_business_owner="Olivia Outsider")
    technical_only = _make_app(db_session, org, "TechnicalOnly")
    nothing = _make_app(db_session, org, "Nothing")
    set_element_owner(element_type="application", element_id=with_record.id, user_id=primary.id,
                      organization_id=org.id, ownership_type="primary")
    set_element_owner(element_type="application", element_id=technical_only.id, user_id=technical.id,
                      organization_id=org.id, ownership_type="technical")
    db_session.flush()
    return primary, with_record, with_text, technical_only, nothing


def _dashboard_owner_coverage(app, user, org):
    from flask import g
    from flask_login import login_user

    from app.modules.dashboard.routes.dashboard_views import _build_overview_context

    with app.test_request_context("/dashboard/overview"):
        login_user(user)
        g.current_org_id = org.id
        return _build_overview_context()["data_coverage"]


def _v2_dashboard_owner_coverage(app, user, org, monkeypatch):
    from flask import g
    from flask_login import login_user

    from app.modules.dashboard.v2.routes import dashboard_views as v2

    captured = {}

    def fake_render(template, **context):
        captured.update(context)
        return ""

    monkeypatch.setattr(v2, "render_template", fake_render)
    with app.test_request_context("/dashboard/overview"):
        login_user(user)
        g.current_org_id = org.id
        v2.overview()
    return captured["data_coverage"]


def test_both_screens_report_two_owned_and_two_unowned(app, db_session, make_org, monkeypatch, client, login_as):
    from app.modules.intelligence.services.query_catalogue import _applications_without_owner

    org = make_org("owned-one-def")
    user, with_record, with_text, technical_only, nothing = _four_applications(db_session, org)

    answer = _applications_without_owner(org.id)
    unowned = {row["id"] for group in answer["groups"] for row in group["rows"]}
    assert unowned == {technical_only.id, nothing.id}, answer
    assert answer["total"] == 2

    # 2 of 4 applications owned = 50% on both dashboards.
    assert _dashboard_owner_coverage(app, user, org)["owner"] == 50
    assert _v2_dashboard_owner_coverage(app, user, org, monkeypatch)["owner"] == 50

    # My Applications portfolio banner: 2 owned, 4 total.
    login_as(client, user)
    response = client.get("/applications/")
    assert response.status_code == 200
    soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
    text = " ".join(soup.get_text().split())
    m = re.search(r"(\d+) of (\d+) applications in the portfolio have a named owner", text)
    assert m is not None, "portfolio banner not found"
    owned, total = int(m.group(1)), int(m.group(2))
    assert owned == 2
    assert total == 4

    # Coverage debug endpoint: 2 owned, 4 total.
    login_as(client, user)
    response = client.get("/dashboard/api/coverage-debug")
    assert response.status_code == 200
    data = json.loads(response.get_data(as_text=True))
    assert data["ok"] is True
    assert data["owner"] == 2
    assert data["total"] == 4

    # Gap analysis: the two unowned apps (technical_only, nothing) have no capabilities/processes,
    # so they should be reported as orphaned.
    from app.services.gap_analysis_service import ArchitecturalGapAnalyzer
    from flask import g
    from flask_login import login_user

    with app.test_request_context("/"):
        login_user(user)
        g.current_org_id = org.id
        analyzer = ArchitecturalGapAnalyzer()
        gaps = analyzer.analyze_portfolio_gaps()
        orphaned = gaps.get("orphaned_applications", [])
        orphaned_ids = {o["application_id"] for o in orphaned}
        assert len(orphaned) == 2
        assert orphaned_ids == {technical_only.id, nothing.id}


def test_python_predicate_agrees_with_the_sql(app, db_session, make_org):
    from app.services.element_ownership_service import application_is_owned

    org = make_org("owned-one-def-py")
    _user, with_record, with_text, technical_only, nothing = _four_applications(db_session, org)
    assert [application_is_owned(a) for a in (with_record, with_text, technical_only, nothing)] == [
        True, True, False, False]
